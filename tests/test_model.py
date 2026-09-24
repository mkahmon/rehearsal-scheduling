from __future__ import annotations

import pytest

from rehearsal.model import (
    Assignment,
    ProblemError,
    TimeGrid,
    format_time,
    idle_slots,
    parse_time,
)
from tests.conftest import build


def test_parse_and_format_time():
    assert parse_time("09:30") == 570
    assert format_time(570) == "09:30"
    assert parse_time("24:00") == 1440


@pytest.mark.parametrize("bad", ["9.30", "09:60", "25:00", "", "noon"])
def test_parse_time_rejects_junk(bad):
    with pytest.raises(ProblemError):
        parse_time(bad)


def test_grid_from_times():
    grid = TimeGrid.from_times("09:00", "17:00")
    assert grid.n_slots == 16
    assert grid.format_span(2, 3) == "10:00-11:30"


def test_grid_rejects_partial_slot():
    with pytest.raises(ProblemError, match="whole number"):
        TimeGrid.from_times("09:00", "17:15")


def test_grid_rejects_backwards_day():
    with pytest.raises(ProblemError, match="must be after"):
        TimeGrid.from_times("17:00", "09:00")


def test_window_must_align_to_grid():
    grid = TimeGrid.from_times("09:00", "17:00")
    assert sorted(grid.parse_window("10:00-11:30", what="x")) == [2, 3, 4]
    with pytest.raises(ProblemError, match="boundary"):
        grid.parse_window("10:15-11:00", what="x")
    with pytest.raises(ProblemError, match="outside the day"):
        grid.parse_window("08:00-10:00", what="x")


def test_idle_ignores_unavailable_slots():
    """The defining property: a gap the person was away for is not waiting."""
    problem = build(
        6,
        {"R": None},
        {"Away": {0, 5}, "Around": None},
        [("X", "R", 1, ["Away", "Around"]), ("Y", "R", 1, ["Away", "Around"])],
    )
    pieces = {p.name: p for p in problem.pieces}
    assignments = (Assignment(pieces["X"], 0), Assignment(pieces["Y"], 5))
    idle = idle_slots(problem, assignments)
    assert idle["Away"] == 0, "slots the person was unavailable for must not count"
    assert idle["Around"] == 4, "slots the person was free for must count"


def test_idle_is_zero_for_a_single_call():
    problem = build(6, {"R": None}, {"Solo": None}, [("X", "R", 2, ["Solo"])])
    pieces = {p.name: p for p in problem.pieces}
    assert idle_slots(problem, (Assignment(pieces["X"], 3),))["Solo"] == 0
