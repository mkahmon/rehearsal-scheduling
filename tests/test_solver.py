"""Solver tests. Every case asserts the exact optimal value, never just "it ran"."""

from __future__ import annotations

from rehearsal.solver import SolveOptions, solve
from tests.conftest import build

OPTS = SolveOptions(time_limit=15.0, workers=4)


def optimum(problem) -> int:
    schedule = solve(problem, OPTS)
    assert schedule is not None, "expected a feasible schedule"
    assert schedule.proven_optimal, f"not proven optimal: {schedule.status}"
    return schedule.total_idle_slots


def test_shared_player_is_sequenced_back_to_back():
    problem = build(
        6, {"R1": None, "R2": None}, {"A": None},
        [("X", "R1", 1, ["A"]), ("Y", "R2", 1, ["A"])],
    )
    schedule = solve(problem, OPTS)
    assert schedule.total_idle_slots == 0
    starts = sorted(a.start for a in schedule.assignments)
    assert starts[1] - starts[0] == 1, "a shared player forces the calls to be adjacent"


def test_disjoint_players_in_different_rooms_run_concurrently():
    problem = build(
        6, {"R1": None, "R2": None}, {"A": None, "B": None},
        [("X", "R1", 2, ["A"]), ("Y", "R2", 2, ["B"])],
    )
    schedule = solve(problem, OPTS)
    assert schedule.total_idle_slots == 0
    assert len({a.start for a in schedule.assignments}) == 1, "nothing forces them apart"


def test_same_room_forces_sequencing_but_costs_nothing():
    problem = build(
        6, {"R": None}, {"A": None, "B": None},
        [("X", "R", 2, ["A"]), ("Y", "R", 2, ["B"])],
    )
    schedule = solve(problem, OPTS)
    assert schedule.total_idle_slots == 0
    starts = sorted(a.start for a in schedule.assignments)
    assert starts[1] >= starts[0] + 2


def test_gap_a_player_is_away_for_is_free():
    """Regression for the availability-aware objective.

    The room is only open at the two ends of the day, so the player's two calls are
    forced four slots apart -- but they are unavailable in between, so it costs nothing.
    A span-minus-playing objective would score this 4.
    """
    problem = build(
        6, {"R": {0, 5}}, {"Away": {0, 5}},
        [("X", "R", 1, ["Away"]), ("Y", "R", 1, ["Away"])],
    )
    assert optimum(problem) == 0


def test_gap_a_player_is_present_for_is_charged():
    """Same shape as above, but now the player is free throughout the gap."""
    problem = build(
        6, {"R": {0, 5}}, {"Around": None},
        [("X", "R", 1, ["Around"]), ("Y", "R", 1, ["Around"])],
    )
    assert optimum(problem) == 4


def test_partial_availability_charges_only_the_free_part():
    problem = build(
        6, {"R": {0, 5}}, {"Half": {0, 1, 2, 5}},
        [("X", "R", 1, ["Half"]), ("Y", "R", 1, ["Half"])],
    )
    assert optimum(problem) == 2, "slots 1 and 2 count; slots 3 and 4 do not"


def test_multi_slot_piece_must_be_contiguous_and_fit_its_window():
    problem = build(
        8, {"R": {0, 1, 2, 4, 6}}, {"A": None}, [("Long", "R", 3, ["A"])],
    )
    schedule = solve(problem, OPTS)
    assert schedule is not None
    assert schedule.start_of("Long") == 0, "only one 3-slot window exists"


def test_optimises_rather_than_packing_earliest():
    """Greedy-earliest would leave a gap; the optimum does not.

    'Pinned' can only happen in the last slot, so A's other two calls belong next to it.
    """
    problem = build(
        5, {"R1": None, "R2": None}, {"A": None, "B": {4}},
        [("P1", "R1", 1, ["A"]), ("P2", "R2", 1, ["A"]), ("Pinned", "R1", 1, ["A", "B"])],
    )
    schedule = solve(problem, OPTS)
    assert schedule.total_idle_slots == 0
    assert sorted(a.start for a in schedule.assignments) == [2, 3, 4]


def test_tie_break_prefers_the_earliest_start():
    """With nothing to optimise, the schedule must not float to a random hour."""
    problem = build(10, {"R": None}, {"A": None}, [("X", "R", 2, ["A"])])
    schedule = solve(problem, OPTS)
    assert schedule.start_of("X") == 0


def test_infeasible_returns_none():
    problem = build(
        5, {"R": {0, 2, 4}}, {"A": None}, [("Duo", "R", 2, ["A"])],
    )
    assert solve(problem, OPTS) is None


def test_every_piece_is_scheduled_exactly_once():
    problem = build(
        8, {"R1": None, "R2": None}, {"A": None, "B": None, "C": None},
        [
            ("P1", "R1", 2, ["A", "B"]),
            ("P2", "R1", 1, ["C"]),
            ("P3", "R2", 2, ["B", "C"]),
            ("P4", "R2", 1, ["A"]),
        ],
    )
    schedule = solve(problem, OPTS)
    assert schedule is not None
    assert len(schedule.assignments) == 4
    assert {a.piece.name for a in schedule.assignments} == {"P1", "P2", "P3", "P4"}


def test_reported_idle_matches_the_assignments():
    from rehearsal.model import idle_slots

    problem = build(
        6, {"R": {0, 5}}, {"Around": None},
        [("X", "R", 1, ["Around"]), ("Y", "R", 1, ["Around"])],
    )
    schedule = solve(problem, OPTS)
    assert schedule.idle_slots_by_person == idle_slots(problem, schedule.assignments)
    assert schedule.total_idle_minutes == schedule.total_idle_slots * 30
