"""End-to-end tests over the shipped examples, including an independent audit.

The audit deliberately re-derives room/player conflicts and the objective from the
Problem rather than trusting anything the solver reported.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rehearsal.cli import main
from rehearsal.model import idle_slots
from rehearsal.parsing import load_problem
from rehearsal.report import render, to_dict
from rehearsal.solver import SolveOptions, solve

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def audit(problem, schedule) -> int:
    """Re-check every hard constraint from scratch; return total idle slots."""
    assert len(schedule.assignments) == len(problem.pieces)

    room_use: dict[tuple[str, int], str] = {}
    person_use: dict[tuple[str, int], str] = {}
    for a in schedule.assignments:
        assert a.piece.duration == len(a.slots())
        for slot in a.slots():
            assert slot in problem.rooms[a.piece.room].available, (
                f"{a.piece.name} runs while {a.piece.room} is closed"
            )
            key = (a.piece.room, slot)
            assert key not in room_use, f"{a.piece.room} double-booked at slot {slot}"
            room_use[key] = a.piece.name
            for player in a.piece.players:
                assert slot in problem.people[player].available, (
                    f"{player} is unavailable during {a.piece.name}"
                )
                pkey = (player, slot)
                assert pkey not in person_use, f"{player} double-booked at slot {slot}"
                person_use[pkey] = a.piece.name

    recomputed = idle_slots(problem, schedule.assignments)
    assert recomputed == schedule.idle_slots_by_person
    return sum(recomputed.values())


@pytest.mark.parametrize("name", ["quartet.yaml", "full_ensemble.yaml"])
def test_example_solves_validly(name):
    problem, warnings = load_problem(EXAMPLES / name)
    assert warnings == [], f"the shipped example should be clean: {warnings}"
    schedule = solve(problem, SolveOptions(time_limit=60.0))
    assert schedule is not None
    assert schedule.proven_optimal
    assert audit(problem, schedule) == schedule.total_idle_slots
    render(schedule)  # must not raise
    json.dumps(to_dict(schedule))  # must be serialisable


def test_quartet_optimum_is_pinned():
    """Regression on the known optimum. Ben's 3-hour absence must cost nothing."""
    problem, _ = load_problem(EXAMPLES / "quartet.yaml")
    schedule = solve(problem, SolveOptions(time_limit=60.0))
    assert schedule.total_idle_minutes == 60
    assert schedule.idle_slots_by_person["Ben"] == 0


def test_full_ensemble_optimum_is_pinned():
    problem, _ = load_problem(EXAMPLES / "full_ensemble.yaml")
    schedule = solve(problem, SolveOptions(time_limit=120.0))
    assert schedule.proven_optimal
    assert schedule.total_idle_minutes == 870


def test_optimising_beats_an_arbitrary_valid_schedule():
    """Guards against the objective silently becoming a no-op."""
    from ortools.sat.python import cp_model

    from rehearsal.model import Assignment
    from rehearsal.solver import build_model
    from rehearsal.starts import feasible_starts

    problem, _ = load_problem(EXAMPLES / "full_ensemble.yaml")
    built = build_model(problem, feasible_starts(problem), hint=False)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 4
    solver.parameters.max_time_in_seconds = 30
    assert solver.solve(built.model) in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    baseline = sum(
        idle_slots(
            problem,
            tuple(
                Assignment(p, solver.value(built.start[p.name])) for p in problem.pieces
            ),
        ).values()
    )
    optimised = solve(problem, SolveOptions(time_limit=120.0)).total_idle_slots
    assert optimised < baseline, "the objective is not actually improving anything"


def test_cli_end_to_end(tmp_path, capsys):
    out = tmp_path / "schedule.json"
    code = main([str(EXAMPLES / "quartet.yaml"), "--json", str(out)])
    assert code == 0
    printed = capsys.readouterr().out
    assert "Recital Hall" in printed
    assert "Status: proven optimal" in printed
    payload = json.loads(out.read_text())
    assert payload["proven_optimal"] is True
    assert len(payload["assignments"]) == 4
    assert payload["assignments"][0]["start"] < payload["assignments"][-1]["start"]


def test_cli_reports_infeasible_input(tmp_path, capsys):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "day: {start: '09:00', end: '11:00'}\n"
        "rooms: {Hall: ['09:00-09:30']}\n"
        "people: {Ana: all, Ben: all}\n"
        "pieces:\n"
        "  - {name: X, room: Hall, players: [Ana]}\n"
        "  - {name: Y, room: Hall, players: [Ben]}\n"
    )
    assert main([str(path)]) == 1
    assert "No valid schedule exists" in capsys.readouterr().err


def test_cli_rejects_malformed_input(tmp_path, capsys):
    path = tmp_path / "bad.yaml"
    path.write_text("rooms: {Hall: all}\n")
    assert main([str(path)]) == 2
    assert "error:" in capsys.readouterr().err


def test_timeout_is_never_reported_as_infeasible(capsys):
    """A budget that runs out proves nothing; claiming infeasibility would be a lie."""
    code = main([str(EXAMPLES / "full_ensemble.yaml"), "--time-limit", "0.001"])
    err = capsys.readouterr().err
    assert "No valid schedule exists" not in err
    assert code in (0, 3, 4), f"unexpected exit code {code}"
    if code == 4:
        assert "may still be solvable" in err


def test_meeting_a_requested_gap_counts_as_success(capsys):
    code = main([str(EXAMPLES / "full_ensemble.yaml"), "--gap", "0.5", "--time-limit", "5"])
    out = capsys.readouterr().out
    assert code == 0
    if "proven optimal" not in out:
        assert "within" in out and "of optimal" in out


def test_gap_zero_reports_proven_optimal(capsys):
    assert main([str(EXAMPLES / "quartet.yaml")]) == 0
    assert "Status: proven optimal" in capsys.readouterr().out


def test_schedule_reports_a_gap_of_zero_when_proven():
    problem, _ = load_problem(EXAMPLES / "quartet.yaml")
    schedule = solve(problem, SolveOptions(time_limit=30.0))
    assert schedule.proven_optimal
    assert schedule.gap == 0.0
    assert schedule.best_bound_slots is None
