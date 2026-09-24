"""Cross-check the CP-SAT objective encoding against brute force.

The within/started/open step encoding has several subtly-wrong variants that still
produce plausible-looking schedules, so this is the load-bearing test for the solver.
Placements are enumerated exhaustively and scored with `idle_slots`, which is the plain
Python statement of the definition; the solver's optimum must match the best of those.
"""

from __future__ import annotations

import itertools
import random

import pytest
from ortools.sat.python import cp_model

from rehearsal.model import Assignment, idle_slots
from rehearsal.solver import SolveOptions, build_model, solve
from rehearsal.starts import feasible_starts
from tests.conftest import build

FAST = SolveOptions(time_limit=10.0, workers=4, hint=True)


def cpsat_objective(problem) -> int:
    """The optimum of the CP-SAT objective expression itself.

    Distinct from `Schedule.total_idle_slots`, which `solve` recomputes from the
    placements with `idle_slots`. That recomputation makes the reported waiting time
    correct even if the objective is off by a constant -- but the proven bound, the
    reported gap and the `--gap` tolerance are all read straight off the objective, so
    they are not protected. Asserting on this value is what pins the constant down.
    """
    domains = feasible_starts(problem)
    built = build_model(problem, domains, hint=False)
    built.model.minimize(built.idle_expr)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    solver.parameters.num_workers = 4
    status = solver.solve(built.model)
    assert status == cp_model.OPTIMAL, solver.status_name(status)
    return round(solver.objective_value)


def brute_force_best(problem):
    """Minimum total idle over every valid placement, or None if there is none."""
    domains = feasible_starts(problem)
    options = [domains.by_piece[p.name] for p in problem.pieces]
    if any(not o for o in options):
        return None

    best = None
    for combo in itertools.product(*options):
        assignments = tuple(
            Assignment(piece, start) for piece, start in zip(problem.pieces, combo)
        )
        room_busy: dict[str, set[int]] = {}
        person_busy: dict[str, set[int]] = {}
        ok = True
        for a in assignments:
            slots = set(a.slots())
            if slots & room_busy.setdefault(a.piece.room, set()):
                ok = False
                break
            room_busy[a.piece.room] |= slots
            for player in a.piece.players:
                if slots & person_busy.setdefault(player, set()):
                    ok = False
                    break
                person_busy[player] |= slots
            if not ok:
                break
        if not ok:
            continue
        total = sum(idle_slots(problem, assignments).values())
        if best is None or total < best:
            best = total
    return best


def random_problem(rng: random.Random):
    n_slots = rng.randint(4, 8)
    n_rooms = rng.randint(1, 2)
    n_people = rng.randint(1, 4)
    n_pieces = rng.randint(1, 4)

    def window(density: float) -> set[int]:
        chosen = {t for t in range(n_slots) if rng.random() < density}
        return chosen or {rng.randrange(n_slots)}

    rooms = {f"R{i}": window(0.85) for i in range(n_rooms)}
    people = {f"P{i}": window(0.7) for i in range(n_people)}
    pieces = []
    for i in range(n_pieces):
        players = [n for n in people if rng.random() < 0.55]
        pieces.append(
            (f"piece{i}", rng.choice(list(rooms)), rng.randint(1, 2), players)
        )
    return build(n_slots, rooms, people, pieces)


@pytest.mark.parametrize("seed", range(200))
def test_solver_matches_brute_force(seed):
    problem = random_problem(random.Random(seed))
    expected = brute_force_best(problem)
    schedule = solve(problem, FAST)

    if expected is None:
        assert schedule is None, "solver found a schedule where brute force found none"
        return

    assert schedule is not None, "brute force found a schedule but the solver did not"
    assert schedule.proven_optimal
    assert schedule.total_idle_slots == expected
    assert cpsat_objective(problem) == expected, "objective is off by a constant"


def test_single_piece_player_does_not_offset_the_objective():
    """Regression: `total_play` must only count players who get `within` variables.

    It once summed over everyone while `within` variables were built only for players
    with two or more pieces, so every one-piece player shifted the objective down by
    their own duration. The chosen schedule stayed optimal -- a constant cannot move an
    argmin -- which is why the brute-force check above passed regardless. What broke was
    everything derived from the objective's scale: the printed bound understated itself,
    and a negative optimum drove `Schedule.gap` negative, making any `--gap` succeed.
    """
    problem = build(
        8,
        {"R": None},
        {"Pair": None, "Solo": None},
        [("X", "R", 1, ["Pair"]), ("Y", "R", 1, ["Pair"]), ("Long", "R", 3, ["Solo"])],
    )
    assert cpsat_objective(problem) == 0

    schedule = solve(problem, FAST)
    assert schedule is not None and schedule.proven_optimal
    assert schedule.gap == 0.0
