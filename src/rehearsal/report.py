"""Human-readable and machine-readable renderings of a solved schedule."""

from __future__ import annotations

import json
from typing import Any

from rehearsal.model import Schedule, format_time

_FREE = "-"
_CLOSED = ""


def _column_widths(headers: list[str], rows: list[list[str]]) -> list[int]:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    return widths


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = _column_widths(headers, rows)
    def line(cells: list[str]) -> str:
        return "  ".join(cell.ljust(w) for cell, w in zip(cells, widths)).rstrip()
    out = [line(headers), line(["-" * w for w in widths])]
    out += [line(row) for row in rows]
    return "\n".join(out)


def timetable(schedule: Schedule) -> str:
    problem = schedule.problem
    grid = problem.grid
    room_names = sorted(problem.rooms)

    # room -> slot -> piece name
    occupancy: dict[str, dict[int, str]] = {name: {} for name in room_names}
    for assignment in schedule.assignments:
        for slot in assignment.slots():
            occupancy[assignment.piece.room][slot] = assignment.piece.name

    rows = []
    for slot in range(grid.n_slots):
        row = [format_time(grid.slot_start(slot))]
        for name in room_names:
            piece = occupancy[name].get(slot)
            if piece is not None:
                row.append(piece)
            elif slot in problem.rooms[name].available:
                row.append(_FREE)
            else:
                row.append(_CLOSED)
        rows.append(row)

    return _table(["Time", *room_names], rows)


def _runs(labels: list[tuple[int, str]]) -> list[tuple[int, int, str]]:
    """Collapse (slot, label) pairs into (start, length, label) runs."""
    out: list[tuple[int, int, str]] = []
    for slot, label in labels:
        if out and out[-1][2] == label and out[-1][0] + out[-1][1] == slot:
            start, length, existing = out[-1]
            out[-1] = (start, length + 1, existing)
        else:
            out.append((slot, 1, label))
    return out


def itineraries(schedule: Schedule) -> str:
    problem = schedule.problem
    grid = problem.grid

    playing: dict[str, dict[int, str]] = {name: {} for name in problem.people}
    for assignment in schedule.assignments:
        for player in assignment.piece.players:
            for slot in assignment.slots():
                playing[player][slot] = assignment.piece.name

    blocks = []
    for name in sorted(problem.people):
        busy = playing[name]
        idle = schedule.idle_slots_by_person.get(name, 0)
        if not busy:
            blocks.append(f"{name} - nothing scheduled")
            continue

        heading = f"{name} - {idle * grid.slot_minutes} min waiting"
        labels = []
        for slot in range(min(busy), max(busy) + 1):
            if slot in busy:
                labels.append((slot, busy[slot]))
            elif slot in problem.people[name].available:
                labels.append((slot, "(waiting)"))
            else:
                labels.append((slot, "(unavailable)"))
        lines = [
            f"  {grid.format_span(start, length):<13} {label}"
            for start, length, label in _runs(labels)
        ]
        blocks.append("\n".join([heading, *lines]))

    return "\n\n".join(blocks)


def summary(schedule: Schedule) -> str:
    grid = schedule.problem.grid
    per_person = schedule.idle_slots_by_person
    waiting = {name: slots for name, slots in per_person.items() if slots}
    lines = [f"Total waiting: {schedule.total_idle_minutes} min"]
    if waiting:
        worst = max(waiting.items(), key=lambda kv: (kv[1], kv[0]))
        lines.append(
            f"Affects {len(waiting)} of {len(per_person)} players; "
            f"worst is {worst[0]} at {worst[1] * grid.slot_minutes} min"
        )
    else:
        lines.append("Nobody waits between their rehearsals.")

    if schedule.proven_optimal:
        lines.append("Status: proven optimal")
    else:
        bound = schedule.best_bound_slots
        detail = (
            ""
            if bound is None
            else f" No schedule can do better than {bound * grid.slot_minutes} min"
        )
        if schedule.gap is not None and detail:
            detail += f", so this is within {schedule.gap:.1%} of optimal"
        lines.append(
            f"Status: valid, but NOT proven optimal.{detail}."
            " Raise --time-limit to close the gap."
        )
    return "\n".join(lines)


def render(schedule: Schedule) -> str:
    parts = []
    if schedule.warnings:
        parts.append("\n".join(f"warning: {w}" for w in schedule.warnings))
    parts += [timetable(schedule), itineraries(schedule), summary(schedule)]
    return "\n\n".join(parts)


def to_dict(schedule: Schedule) -> dict[str, Any]:
    grid = schedule.problem.grid
    return {
        "status": schedule.status,
        "proven_optimal": schedule.proven_optimal,
        "total_idle_minutes": schedule.total_idle_minutes,
        "gap": schedule.gap,
        "best_bound_minutes": (
            None
            if schedule.best_bound_slots is None
            else schedule.best_bound_slots * grid.slot_minutes
        ),
        "warnings": list(schedule.warnings),
        "assignments": [
            {
                "piece": a.piece.name,
                "room": a.piece.room,
                "start": format_time(grid.slot_start(a.start)),
                "end": format_time(grid.slot_start(a.end)),
                "minutes": a.piece.duration * grid.slot_minutes,
                "players": list(a.piece.players),
            }
            for a in sorted(schedule.assignments, key=lambda a: (a.start, a.piece.room))
        ],
        "idle_minutes_by_person": {
            name: slots * grid.slot_minutes
            for name, slots in sorted(schedule.idle_slots_by_person.items())
        },
    }


def to_json(schedule: Schedule) -> str:
    return json.dumps(to_dict(schedule), indent=2)
