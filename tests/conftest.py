from __future__ import annotations

from rehearsal.model import Person, Piece, Problem, Room, TimeGrid


def build(
    n_slots: int,
    rooms: dict[str, set[int] | None],
    people: dict[str, set[int] | None],
    pieces: list[tuple[str, str, int, list[str]]],
) -> Problem:
    """Construct a Problem directly from slot indices, bypassing the YAML layer."""
    grid = TimeGrid(day_start=9 * 60, n_slots=n_slots)
    everything = grid.all_slots()
    return Problem(
        grid=grid,
        rooms={
            name: Room(name, frozenset(slots) if slots is not None else everything)
            for name, slots in rooms.items()
        },
        people={
            name: Person(name, frozenset(slots) if slots is not None else everything)
            for name, slots in people.items()
        },
        pieces=tuple(
            Piece(name=name, room=room, duration=duration, players=tuple(players))
            for name, room, duration, players in pieces
        ),
    )
