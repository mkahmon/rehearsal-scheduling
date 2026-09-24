from __future__ import annotations

import pytest

from rehearsal.model import Person, Piece, Problem, ProblemError, Room, TimeGrid
from rehearsal.solver import solve
from rehearsal.validate import validate
from tests.conftest import build


def _problem(pieces, people=("A", "B")):
    grid = TimeGrid(day_start=540, n_slots=6)
    return Problem(
        grid=grid,
        rooms={"R": Room("R", grid.all_slots())},
        people={n: Person(n, grid.all_slots()) for n in people},
        pieces=tuple(pieces),
    )


def test_duplicate_player_is_deduped_not_fatal():
    """Left in, the repeated interval makes add_no_overlap assert duration <= 0."""
    problem = _problem([Piece("X", "R", 2, ("A", "A", "B"))])
    cleaned, warnings = validate(problem)
    assert cleaned.pieces[0].players == ("A", "B")
    assert any("more than once" in w for w in warnings)
    # And the problem must still solve rather than reporting a nonsense infeasibility.
    assert solve(cleaned) is not None


def test_zero_length_piece_is_rejected():
    with pytest.raises(ProblemError, match="at least 1"):
        validate(_problem([Piece("X", "R", 0, ("A",))]))


def test_unknown_room_and_player_are_reported_together():
    with pytest.raises(ProblemError) as exc:
        validate(_problem([Piece("X", "Nowhere", 1, ("Ghost",))]))
    assert "Nowhere" in str(exc.value)
    assert "Ghost" in str(exc.value)


def test_duplicate_piece_names_rejected():
    with pytest.raises(ProblemError, match="duplicate piece name"):
        validate(_problem([Piece("X", "R", 1, ("A",)), Piece("X", "R", 1, ("B",))]))


def test_piece_with_no_players_warns_but_solves():
    cleaned, warnings = validate(_problem([Piece("X", "R", 1, ())]))
    assert any("no players" in w for w in warnings)
    assert solve(cleaned) is not None


def test_room_with_no_availability_rejected():
    problem = build(4, {"R": set()}, {"A": None}, [("X", "R", 1, ["A"])])
    with pytest.raises(ProblemError, match="no available time"):
        validate(problem)
