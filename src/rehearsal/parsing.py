"""Load a Problem from YAML or JSON.

`yaml.safe_load` parses JSON too, so one loader covers both formats.
Every error names the key it came from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from rehearsal.model import Person, Piece, Problem, ProblemError, Room, TimeGrid
from rehearsal.validate import validate

_ALL = {"all", "any", "*"}


def _windows_to_slots(spec: Any, grid: TimeGrid, what: str) -> frozenset[int]:
    """Interpret an availability spec: None / "all" / one window / a list of windows."""
    if spec is None:
        return grid.all_slots()
    if isinstance(spec, str):
        if spec.strip().lower() in _ALL:
            return grid.all_slots()
        spec = [spec]
    if not isinstance(spec, list):
        raise ProblemError(
            f"availability for {what} must be 'all' or a list of HH:MM-HH:MM windows, "
            f"got {spec!r}"
        )
    slots: set[int] = set()
    for window in spec:
        slots |= grid.parse_window(window, what=what)
    return frozenset(slots)


def problem_from_dict(data: Any) -> Problem:
    if not isinstance(data, dict):
        raise ProblemError("top level of the input must be a mapping")

    day = data.get("day")
    if not isinstance(day, dict) or "start" not in day or "end" not in day:
        raise ProblemError("missing 'day:' section with 'start:' and 'end:' times")
    grid = TimeGrid.from_times(day["start"], day["end"])

    raw_rooms = data.get("rooms")
    if not isinstance(raw_rooms, dict) or not raw_rooms:
        raise ProblemError("missing 'rooms:' section (a mapping of room name to availability)")
    rooms = {
        str(name): Room(str(name), _windows_to_slots(spec, grid, f"room {name!r}"))
        for name, spec in raw_rooms.items()
    }

    raw_people = data.get("people") or {}
    if isinstance(raw_people, list):  # a bare list means "everyone is free all day"
        raw_people = {str(name): None for name in raw_people}
    if not isinstance(raw_people, dict):
        raise ProblemError("'people:' must be a mapping of name to availability, or a list of names")
    people = {
        str(name): Person(str(name), _windows_to_slots(spec, grid, f"person {name!r}"))
        for name, spec in raw_people.items()
    }

    raw_pieces = data.get("pieces")
    if not isinstance(raw_pieces, list) or not raw_pieces:
        raise ProblemError("missing 'pieces:' section (a list of pieces)")
    pieces = []
    for index, raw in enumerate(raw_pieces):
        where = f"pieces[{index}]"
        if not isinstance(raw, dict):
            raise ProblemError(f"{where} must be a mapping, got {raw!r}")
        name = raw.get("name")
        if not name:
            raise ProblemError(f"{where} is missing 'name:'")
        room = raw.get("room")
        if not room:
            raise ProblemError(f"piece {name!r} is missing 'room:'")
        duration = raw.get("slots", 1)
        if not isinstance(duration, int) or isinstance(duration, bool):
            raise ProblemError(f"piece {name!r}: 'slots:' must be a whole number, got {duration!r}")
        players = raw.get("players") or []
        if isinstance(players, str):
            players = [players]
        if not isinstance(players, list):
            raise ProblemError(f"piece {name!r}: 'players:' must be a list of names")
        pieces.append(
            Piece(
                name=str(name),
                room=str(room),
                duration=duration,
                players=tuple(str(p) for p in players),
            )
        )

    # People may be introduced implicitly by appearing in a player list.
    for piece in pieces:
        for player in piece.players:
            people.setdefault(player, Person(player, grid.all_slots()))

    return Problem(grid=grid, rooms=rooms, people=people, pieces=tuple(pieces))


def load_problem(path: str | Path) -> tuple[Problem, list[str]]:
    """Read a YAML/JSON file and return the validated Problem plus any warnings."""
    path = Path(path)
    try:
        text = path.read_text()
    except OSError as exc:
        raise ProblemError(f"cannot read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProblemError(f"cannot parse {path}: {exc}") from exc
    return validate(problem_from_dict(data))
