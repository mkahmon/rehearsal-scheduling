"""Structural checks run before any solving.

Several of these are not cosmetic. A duplicated player name or a zero-length piece
silently corrupts the CP-SAT model rather than failing loudly, so they are normalised
or rejected here where the error message can still point at the input.
"""

from __future__ import annotations

from rehearsal.model import Piece, Problem, ProblemError


def validate(problem: Problem) -> tuple[Problem, list[str]]:
    """Return a normalised Problem plus non-fatal warnings. Raises on fatal errors."""
    warnings: list[str] = []
    errors: list[str] = []

    seen: set[str] = set()
    for piece in problem.pieces:
        if piece.name in seen:
            errors.append(f"duplicate piece name {piece.name!r}")
        seen.add(piece.name)

    normalised: list[Piece] = []
    for piece in problem.pieces:
        if piece.room not in problem.rooms:
            errors.append(
                f"piece {piece.name!r} needs room {piece.room!r}, which is not defined "
                f"(known rooms: {', '.join(sorted(problem.rooms)) or 'none'})"
            )
        if piece.duration < 1:
            # A zero-size interval satisfies add_no_overlap vacuously, so CP-SAT would
            # happily double-book the room rather than complain.
            errors.append(
                f"piece {piece.name!r} has slots={piece.duration}; must be at least 1"
            )

        # Deduplicate players, preserving order. Left in place, a repeated name puts the
        # same interval into one add_no_overlap twice, which asserts duration <= 0 and can
        # make the whole problem report INFEASIBLE for a reason that makes no sense.
        deduped = tuple(dict.fromkeys(piece.players))
        if len(deduped) != len(piece.players):
            repeats = sorted({p for p in piece.players if piece.players.count(p) > 1})
            warnings.append(
                f"piece {piece.name!r} lists {', '.join(repeats)} more than once; "
                f"counting each player only once"
            )

        for player in deduped:
            if player not in problem.people:
                errors.append(f"piece {piece.name!r} lists unknown player {player!r}")

        if not deduped:
            warnings.append(f"piece {piece.name!r} has no players listed")

        normalised.append(
            Piece(
                name=piece.name,
                room=piece.room,
                duration=piece.duration,
                players=deduped,
            )
        )

    for room in problem.rooms.values():
        if not room.available:
            errors.append(f"room {room.name!r} has no available time")

    idle_people = sorted(
        name for name in problem.people if not problem.pieces_for_person(name)
    )
    if idle_people:
        warnings.append(
            f"not playing in anything: {', '.join(idle_people)}"
        )

    if errors:
        raise ProblemError("\n".join(errors))

    return (
        Problem(
            grid=problem.grid,
            rooms=problem.rooms,
            people=problem.people,
            pieces=tuple(normalised),
        ),
        warnings,
    )
