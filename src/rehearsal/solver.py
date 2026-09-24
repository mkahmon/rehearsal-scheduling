"""The CP-SAT model.

Placement is encoded twice and channelled together: as fixed-size intervals (whose
disjunctive propagator is far stronger than the equivalent boolean sums) and as
per-slot start booleans (which the availability-aware objective needs and which give a
much tighter linear relaxation than a first/last span encoding would).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from rehearsal.model import Assignment, Problem, Schedule, idle_slots
from rehearsal.starts import StartDomains, feasible_starts, greedy_placement


@dataclass(frozen=True)
class SolveOptions:
    time_limit: float = 30.0
    workers: int = 8
    log: bool = False
    #: Stop once the proven gap is within this fraction. 0.0 means prove exactly.
    relative_gap: float = 0.0
    hint: bool = True


class ModelInvalid(RuntimeError):
    """CP-SAT rejected the model. Always a bug here, never bad user input."""


class SearchExhausted(RuntimeError):
    """The budget ran out before any schedule was found.

    Distinct from infeasibility: nothing has been proved either way, so this must never
    be reported to the user as "no valid schedule exists".
    """


@dataclass
class _Built:
    model: cp_model.CpModel
    start: dict[str, cp_model.IntVar]
    idle_expr: object
    total_play: int
    within: list[cp_model.IntVar] = field(default_factory=list)


def build_model(problem: Problem, domains: StartDomains, *, hint: bool = True) -> _Built:
    model = cp_model.CpModel()
    grid = problem.grid
    n_slots = grid.n_slots

    start: dict[str, cp_model.IntVar] = {}
    # piece name -> {slot: BoolVar "this piece starts here"}
    starts_at: dict[str, dict[int, cp_model.IntVar]] = {}
    intervals: dict[str, cp_model.IntervalVar] = {}

    for piece in problem.pieces:
        allowed = domains.by_piece[piece.name]
        var = model.new_int_var_from_domain(
            cp_model.Domain.from_values(allowed), f"start[{piece.name}]"
        )
        start[piece.name] = var
        intervals[piece.name] = model.new_fixed_size_interval_var(
            var, piece.duration, f"interval[{piece.name}]"
        )
        booleans = {
            slot: model.new_bool_var(f"at[{piece.name}@{slot}]") for slot in allowed
        }
        starts_at[piece.name] = booleans
        model.add_exactly_one(booleans.values())
        model.add(
            var == cp_model.LinearExpr.weighted_sum(list(booleans.values()), list(booleans))
        )

    # Resource constraints. The interval form propagates much better than the boolean
    # sums below, so both are kept even though either alone would be sufficient.
    for room_name in problem.rooms:
        in_room = [intervals[p.name] for p in problem.pieces_in_room(room_name)]
        if len(in_room) > 1:
            model.add_no_overlap(in_room)

    for person_name in problem.people:
        theirs = [intervals[p.name] for p in problem.pieces_for_person(person_name)]
        if len(theirs) > 1:
            model.add_no_overlap(theirs)

    # Valid redundant bound: at most `len(rooms)` pieces can run at once anywhere.
    if len(problem.pieces) > len(problem.rooms):
        model.add_cumulative(
            list(intervals.values()), [1] * len(intervals), len(problem.rooms)
        )

    # Objective: availability-aware idle time, via per-person per-slot booleans.
    within_vars: list[cp_model.IntVar] = []
    total_play = 0

    for person_name, person in problem.people.items():
        theirs = problem.pieces_for_person(person_name)
        play = sum(p.duration for p in theirs)
        total_play += play
        if len(theirs) < 2:
            # One piece (or none) can never produce a gap.
            continue

        # busy[t]: the start booleans that would put this person in a rehearsal at t.
        # Kept as plain term lists -- no auxiliary variable is needed.
        busy: list[list[cp_model.IntVar]] = []
        for slot in range(n_slots):
            terms = [
                starts_at[p.name][s]
                for p in theirs
                for s in domains.by_piece[p.name]
                if s <= slot < s + p.duration
            ]
            busy.append(terms)
            if len(terms) > 1:
                # Per-slot form of this person's no-overlap. Redundant with the interval
                # form above, but it is what tightens the linear relaxation.
                model.add(cp_model.LinearExpr.sum(terms) <= 1)

        started = [model.new_bool_var(f"started[{person_name}@{t}]") for t in range(n_slots)]
        open_ = [model.new_bool_var(f"open[{person_name}@{t}]") for t in range(n_slots)]

        for slot in range(n_slots):
            if not busy[slot]:
                continue
            occupied = cp_model.LinearExpr.sum(busy[slot])
            model.add(started[slot] >= occupied)
            model.add(open_[slot] >= occupied)
        for slot in range(n_slots - 1):
            model.add(started[slot + 1] >= started[slot])  # monotone non-decreasing
            model.add(open_[slot] >= open_[slot + 1])  # monotone non-increasing

        # Only slots the person is actually free for can be charged as waiting.
        mine: list[cp_model.IntVar] = []
        for slot in sorted(person.available):
            var = model.new_bool_var(f"within[{person_name}@{slot}]")
            model.add(var >= started[slot] + open_[slot] - 1)
            mine.append(var)
        # Redundant but valid: this person's idle time cannot be negative.
        model.add(cp_model.LinearExpr.sum(mine) >= play)
        within_vars.extend(mine)

    # The constant belongs inside the objective, not subtracted at print time, or the
    # reported value and the bound are both offset and any gap limit is misapplied.
    idle_expr = cp_model.LinearExpr.sum(within_vars) - total_play

    if hint:
        for name, slot in greedy_placement(problem, domains).items():
            model.add_hint(start[name], slot)

    return _Built(
        model=model,
        start=start,
        idle_expr=idle_expr,
        total_play=total_play,
        within=within_vars,
    )


def _configure(solver: cp_model.CpSolver, options: SolveOptions, budget: float) -> None:
    solver.parameters.max_time_in_seconds = max(budget, 0.01)
    solver.parameters.num_workers = options.workers
    solver.parameters.log_search_progress = options.log
    if options.relative_gap > 0:
        solver.parameters.relative_gap_limit = options.relative_gap


def solve(
    problem: Problem,
    options: SolveOptions | None = None,
    *,
    warnings: tuple[str, ...] = (),
) -> Schedule | None:
    """Return the best schedule found, or None if the problem is infeasible.

    None means "diagnose this" -- see rehearsal.diagnose.
    """
    options = options or SolveOptions()
    domains = feasible_starts(problem)
    if domains.impossible:
        return None

    built = build_model(problem, domains, hint=options.hint)
    solver = cp_model.CpSolver()

    # Phase 1: minimise idle time.
    built.model.minimize(built.idle_expr)
    _configure(solver, options, options.time_limit)
    status = solver.solve(built.model)

    if status == cp_model.MODEL_INVALID:
        raise ModelInvalid(built.model.validate())
    if status == cp_model.INFEASIBLE:
        return None
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SearchExhausted(
            f"no schedule found within {options.time_limit:g}s "
            f"(solver status: {solver.status_name(status)})"
        )

    best = round(solver.objective_value)
    bound = int(solver.best_objective_bound // 1)
    # CP-SAT also reports OPTIMAL once a relative_gap_limit is met, so the status alone
    # is not enough -- only a bound that has actually reached the incumbent proves it.
    proven = status == cp_model.OPTIMAL and bound >= best

    # Phase 2: among equally good schedules, prefer the one that starts earliest.
    # A lexicographic second pass rather than a weighted term -- the ~2400:1 coefficient
    # spread a weighted tie-break would need degrades the linear relaxation.
    if proven:
        built.model.add(built.idle_expr == best)
        built.model.minimize(
            cp_model.LinearExpr.sum([built.start[p.name] for p in problem.pieces])
        )
        tie_solver = cp_model.CpSolver()
        _configure(tie_solver, options, min(options.time_limit, 10.0))
        tie_status = tie_solver.solve(built.model)
        if tie_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            solver = tie_solver

    assignments = tuple(
        Assignment(piece=piece, start=int(solver.value(built.start[piece.name])))
        for piece in problem.pieces
    )
    # Recomputed from the placements rather than read out of the auxiliary variables,
    # which are only bounds in a non-optimal solution.
    per_person = idle_slots(problem, assignments)

    return Schedule(
        problem=problem,
        assignments=assignments,
        idle_slots_by_person=per_person,
        status=solver.status_name(status),
        proven_optimal=proven,
        best_bound_slots=None if proven else max(bound, 0),
        gap=0.0 if proven else (best - max(bound, 0)) / max(abs(best), 1),
        warnings=warnings,
    )
