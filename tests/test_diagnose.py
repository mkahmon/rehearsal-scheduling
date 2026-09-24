from __future__ import annotations

from rehearsal.diagnose import diagnose
from rehearsal.solver import solve
from tests.conftest import build


def test_piece_that_cannot_be_placed_at_all_is_named():
    """Tier 1: the room never has two free slots in a row."""
    problem = build(5, {"R": {0, 2, 4}}, {"A": None}, [("Duo", "R", 2, ["A"])])
    assert solve(problem) is None
    text = diagnose(problem).text()
    assert "'Duo'" in text
    assert "60 contiguous minutes" in text
    assert "never free that long" in text


def test_blocking_player_is_named_with_what_was_left():
    """Tier 1: the room allows placements but one player's availability kills them."""
    problem = build(
        6, {"R": {0, 1, 2, 3}}, {"Early": {0, 1, 2, 3}, "Late": {4, 5}},
        [("Duo", "R", 2, ["Early", "Late"])],
    )
    text = diagnose(problem).text()
    assert "Late" in text, "the player who emptied the domain must be named"
    assert "09:00-10:00" in text, "the placements that were still open must be shown"


def test_room_oversubscription_names_the_window():
    """Tiers 2/3: more rehearsal is forced into a window than it can hold."""
    problem = build(
        4, {"R": {0}}, {"A": None, "B": None},
        [("X", "R", 1, ["A"]), ("Y", "R", 1, ["B"])],
    )
    text = diagnose(problem).text()
    assert "Room 'R'" in text
    assert "60 minutes" in text and "30 minutes are free" in text


def test_person_oversubscription_names_the_person():
    problem = build(
        6, {"R1": None, "R2": None}, {"Busy": {0, 1}},
        [("X", "R1", 1, ["Busy"]), ("Y", "R2", 1, ["Busy"]), ("Z", "R1", 1, ["Busy"])],
    )
    text = diagnose(problem).text()
    assert "Busy must fit" in text


def test_repair_suggestion_names_what_to_drop():
    """Tier 4: two pieces share a player and are both pinned to the same slot."""
    problem = build(
        4, {"R1": {1}, "R2": {1}}, {"A": None},
        [("X", "R1", 1, ["A"]), ("Y", "R2", 1, ["A"])],
    )
    result = diagnose(problem)
    assert result.suggestions, "a repair suggestion is expected"
    suggestion = result.suggestions[0]
    assert "'X'" in suggestion or "'Y'" in suggestion


def test_explain_adds_a_conflict_set():
    problem = build(
        4, {"R1": {1}, "R2": {1}}, {"A": None},
        [("X", "R1", 1, ["A"]), ("Y", "R2", 1, ["A"])],
    )
    without = diagnose(problem, explain=False)
    with_core = diagnose(problem, explain=True)
    assert len(with_core.suggestions) > len(without.suggestions)
    assert "cannot all hold at once" in with_core.suggestions[-1]


def test_diagnosis_always_says_something():
    problem = build(
        4, {"R1": {1}, "R2": {1}}, {"A": None},
        [("X", "R1", 1, ["A"]), ("Y", "R2", 1, ["A"])],
    )
    assert diagnose(problem).reasons, "never produce an empty explanation"
