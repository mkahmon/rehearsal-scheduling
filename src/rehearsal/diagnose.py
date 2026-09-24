"""Explain why a problem has no valid schedule, and suggest the smallest fix.

Never produces a schedule. Runs only on the failure path, so cost does not matter much,
but the tiers are still ordered cheapest-first: the deterministic checks give far better
messages than a solver core does, and they catch most real-world mistakes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from ortools.sat.python import cp_model

from rehearsal.model import Piece, Problem
from rehearsal.starts import StartDomains, contiguous_starts, feasible_starts


@dataclass
class Diagnosis:
    reasons: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def text(self) -> str:
        lines = ["No valid schedule exists."]
        lines += [f"  - {r}" for r in self.reasons]
        if self.suggestions:
            lines.append("")
            lines.append("What to change:")
            lines += [f"  - {s}" for s in self.suggestions]
        return "\n".join(lines)


def _resources(problem: Problem):
    """Yield (label, available slots, pieces) for every room and every person."""
    for room in problem.rooms.values():
        pieces = problem.pieces_in_room(room.name)
        if pieces:
            yield f"Room {room.name!r}", room.available, pieces
    for person in problem.people.values():
        pieces = problem.pieces_for_person(person.name)
        if pieces:
            yield person.name, person.available, pieces


def _impossible_pieces(problem: Problem, domains: StartDomains) -> list[str]:
    """Tier 1: a piece that cannot be placed at all, whatever else happens."""
    reasons = []
    grid = problem.grid
    for piece in problem.pieces:
        if domains.by_piece[piece.name]:
            continue
        minutes = piece.duration * grid.slot_minutes
        blocker = domains.blocker.get(piece.name)
        head = f"{piece.name!r} needs {minutes} contiguous minutes in room {piece.room!r}"
        if blocker is None or not blocker.after:
            reasons.append(f"{head}, which is never free that long in one stretch.")
            continue
        options = ", ".join(
            grid.format_span(s, piece.duration) for s in blocker.remaining
        )
        already = ", ".join(blocker.after)
        reasons.append(
            f"{head}. {already} between them allow only {options}; "
            f"adding {blocker.resource}'s availability rules "
            f"{'that out' if len(blocker.remaining) == 1 else 'them all out'}."
        )
    return reasons


def _overloaded_windows(problem: Problem, domains: StartDomains) -> list[str]:
    """Tiers 2 and 3: more rehearsal time is forced into a window than it can hold.

    Windows are scanned shortest-first so the reported one is the most specific.
    The whole day is just the widest window, so this subsumes the plain capacity check.
    """
    grid = problem.grid
    n = grid.n_slots
    reasons = []

    for label, available, pieces in _resources(problem):
        found = None
        for width in range(1, n + 1):
            for lo in range(n - width + 1):
                hi = lo + width
                capacity = sum(1 for t in available if lo <= t < hi)
                trapped: list[Piece] = []
                for piece in pieces:
                    starts = domains.by_piece[piece.name]
                    if starts and all(lo <= s and s + piece.duration <= hi for s in starts):
                        trapped.append(piece)
                demand = sum(p.duration for p in trapped)
                if demand > capacity:
                    found = (lo, hi, capacity, demand, trapped)
                    break
            if found:
                break
        if not found:
            continue
        lo, hi, capacity, demand, trapped = found
        names = ", ".join(repr(p.name) for p in trapped)
        span = grid.format_span(lo, hi - lo)
        reasons.append(
            f"{label} must fit {demand * grid.slot_minutes} minutes of rehearsal "
            f"({names}) into {span}, where only "
            f"{capacity * grid.slot_minutes} minutes are free."
        )
    return reasons


def _repair_suggestion(problem: Problem, domains: StartDomains, time_limit: float) -> list[str]:
    """Tier 4: the fewest pieces you would have to move or drop to make it fit."""
    placeable = [p for p in problem.pieces if domains.by_piece[p.name]]
    if not placeable:
        return []

    model = cp_model.CpModel()
    start, present, interval = {}, {}, {}
    for piece in placeable:
        start[piece.name] = model.new_int_var_from_domain(
            cp_model.Domain.from_values(domains.by_piece[piece.name]), f"s[{piece.name}]"
        )
        present[piece.name] = model.new_bool_var(f"present[{piece.name}]")
        interval[piece.name] = model.new_optional_fixed_size_interval_var(
            start[piece.name], piece.duration, present[piece.name], f"iv[{piece.name}]"
        )

    names = {p.name for p in placeable}
    for _label, _available, pieces in _resources(problem):
        theirs = [interval[p.name] for p in pieces if p.name in names]
        if len(theirs) > 1:
            model.add_no_overlap(theirs)

    model.maximize(cp_model.LinearExpr.sum(list(present.values())))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(time_limit, 1.0)
    solver.parameters.num_workers = 8
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return []

    dropped = [p.name for p in placeable if not solver.boolean_value(present[p.name])]
    if not dropped:
        return []
    qualifier = "" if status == cp_model.OPTIMAL else " (best found so far)"
    listed = ", ".join(repr(name) for name in dropped)
    verb = "it" if len(dropped) == 1 else "them"
    return [
        (
            f"Everything else fits once {listed} {'is' if len(dropped) == 1 else 'are'} "
            f"removed{qualifier}. Move {verb} to another room, shorten {verb}, or widen "
            f"someone's availability."
        )
    ]


def _unsat_core(problem: Problem, domains: StartDomains, time_limit: float) -> list[str]:
    """Optional deeper pass: a minimal set of assumptions that cannot hold together.

    add_no_overlap cannot take only_enforce_if -- no-overlap is not reifiable -- so this
    model re-expresses conflicts as guarded pairwise disjunctions and availability as
    guarded domain membership, which are.
    """
    grid = problem.grid
    model = cp_model.CpModel()
    # literal index -> (kind, subject, detail)
    labels: dict[int, tuple[str, str, str]] = {}
    assumptions = []

    def assume(literal, kind: str, subject: str, detail: str) -> None:
        labels[literal.index] = (kind, subject, detail)
        assumptions.append(literal)

    start, present = {}, {}
    for piece in problem.pieces:
        upper = grid.n_slots - piece.duration
        if upper < 0:
            return [f"{piece.name!r} is longer than the whole day."]
        start[piece.name] = model.new_int_var(0, upper, f"s[{piece.name}]")
        present[piece.name] = model.new_bool_var(f"present[{piece.name}]")
        assume(present[piece.name], "schedule", piece.name, "")

    # Availability, one assumption per (piece, resource) so the core names the culprit.
    for piece in problem.pieces:
        resources = [(f"room {piece.room!r}", problem.rooms[piece.room].available)]
        resources += [(player, problem.people[player].available) for player in piece.players]
        for label, available in resources:
            allowed = sorted(contiguous_starts(available, piece.duration, grid.n_slots))
            ok = model.new_bool_var(f"avail[{piece.name}|{label}]")
            assume(ok, "avail", label, piece.name)
            if not allowed:
                model.add_bool_and([ok.negated()]).only_enforce_if(present[piece.name])
                continue
            model.add_linear_expression_in_domain(
                start[piece.name], cp_model.Domain.from_values(allowed)
            ).only_enforce_if([ok, present[piece.name]])

    # Pairwise non-overlap, one assumption per (resource, pair).
    for label, _available, pieces in _resources(problem):
        for left, right in combinations(pieces, 2):
            conflict = model.new_bool_var(f"conflict[{label}|{left.name}|{right.name}]")
            assume(conflict, "clash", label, f"{left.name} / {right.name}")
            order = model.new_bool_var("")
            guard = [conflict, present[left.name], present[right.name]]
            model.add(
                start[left.name] + left.duration <= start[right.name]
            ).only_enforce_if([*guard, order])
            model.add(
                start[right.name] + right.duration <= start[left.name]
            ).only_enforce_if([*guard, order.negated()])

    model.add_assumptions(assumptions)
    solver = cp_model.CpSolver()
    # Cores are not reliably populated under the parallel portfolio.
    solver.parameters.num_workers = 1
    solver.parameters.max_time_in_seconds = max(time_limit, 1.0)
    status = solver.solve(model)
    if status != cp_model.INFEASIBLE:
        return []

    core = solver.sufficient_assumptions_for_infeasibility()
    if not core:
        # CP-SAT can return nothing when the non-assumed part is itself infeasible.
        return []
    found = [labels[index] for index in core if index in labels]
    if not found:
        return []
    return _describe_core(found)


def _describe_core(found: list[tuple[str, str, str]]) -> list[str]:
    """Summarise a conflict set. Cores are often large; listing every entry is noise.

    CP-SAT minimises the set it returns but does not guarantee it is the smallest one,
    so a big core means the clash is genuinely spread across the problem rather than
    caused by one pair of pieces.
    """
    by_kind: dict[str, list[tuple[str, str]]] = {"schedule": [], "avail": [], "clash": []}
    for kind, subject, detail in found:
        by_kind[kind].append((subject, detail))

    if len(found) <= 12:
        lines = []
        for subject, _ in sorted(by_kind["schedule"]):
            lines.append(f"scheduling {subject!r} at all")
        for subject, detail in sorted(by_kind["avail"]):
            lines.append(f"{detail!r} fitting inside {subject}")
        for subject, detail in sorted(by_kind["clash"]):
            lines.append(f"{subject}: {detail} not overlapping")
        return ["These cannot all hold at once: " + "; ".join(lines) + "."]

    def tally(entries: list[tuple[str, str]], noun: str) -> str:
        counts: dict[str, int] = {}
        for subject, _ in entries:
            counts[subject] = counts.get(subject, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ", ".join(f"{name} ({n} {noun}{'s' if n > 1 else ''})" for name, n in ranked[:5])
        if len(ranked) > 5:
            shown += f", and {len(ranked) - 5} more"
        return shown

    lines = [
        (
            "The clash is spread across the problem, not caused by one pair of pieces: "
            f"{len(found)} constraints are jointly responsible."
        )
    ]
    if by_kind["schedule"]:
        names = ", ".join(sorted(subject for subject, _ in by_kind["schedule"]))
        lines.append(f"  involving: {names}")
    if by_kind["avail"]:
        lines.append(f"  availability limits from: {tally(by_kind['avail'], 'piece')}")
    if by_kind["clash"]:
        lines.append(f"  competing for: {tally(by_kind['clash'], 'pair')}")
    return ["\n".join(lines)]


def diagnose(
    problem: Problem,
    domains: StartDomains | None = None,
    *,
    explain: bool = False,
    time_limit: float = 10.0,
) -> Diagnosis:
    domains = domains if domains is not None else feasible_starts(problem)
    result = Diagnosis()

    result.reasons.extend(_impossible_pieces(problem, domains))
    if not result.reasons:
        result.reasons.extend(_overloaded_windows(problem, domains))

    result.suggestions.extend(_repair_suggestion(problem, domains, time_limit))
    if explain:
        result.suggestions.extend(_unsat_core(problem, domains, time_limit))

    if not result.reasons:
        result.reasons.append(
            "The room, availability and player constraints cannot all be satisfied "
            "together; no single piece or time window is on its own to blame."
        )
    return result
