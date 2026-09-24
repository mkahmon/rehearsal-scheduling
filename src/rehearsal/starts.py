"""Feasible start slots for each piece.

Restricting each start variable's domain to exactly the placements that respect room
and player availability prunes far more than per-slot constraints would, and the
resource that empties a domain is recorded on the way through -- that record is the
cheapest tier of infeasibility diagnosis, obtained for free.
"""

from __future__ import annotations

from dataclasses import dataclass

from rehearsal.model import Problem


def contiguous_starts(available: frozenset[int], duration: int, n_slots: int) -> set[int]:
    """Starts s where every slot of [s, s+duration) is available and s+duration <= n_slots."""
    return {
        s
        for s in range(n_slots - duration + 1)
        if all(t in available for t in range(s, s + duration))
    }


@dataclass(frozen=True)
class Blocker:
    """Which resource emptied a piece's domain, and what was left just before it."""

    resource: str
    #: Resources already intersected in, in the order they were applied.
    after: tuple[str, ...]
    #: Starts still possible before `resource` was intersected in.
    remaining: tuple[int, ...]


@dataclass(frozen=True)
class StartDomains:
    by_piece: dict[str, tuple[int, ...]]
    #: piece name -> why its domain is empty (absent if it is not)
    blocker: dict[str, Blocker]

    @property
    def impossible(self) -> tuple[str, ...]:
        return tuple(name for name, starts in self.by_piece.items() if not starts)


def feasible_starts(problem: Problem) -> StartDomains:
    n_slots = problem.grid.n_slots
    by_piece: dict[str, tuple[int, ...]] = {}
    blocker: dict[str, Blocker] = {}

    for piece in problem.pieces:
        room_label = f"room {piece.room!r}"
        room = problem.rooms[piece.room]
        allowed = contiguous_starts(room.available, piece.duration, n_slots)
        applied = [room_label]
        if not allowed:
            blocker[piece.name] = Blocker(room_label, (), ())
        else:
            for player in piece.players:
                person = problem.people[player]
                narrowed = allowed & contiguous_starts(person.available, piece.duration, n_slots)
                if not narrowed:
                    blocker[piece.name] = Blocker(
                        resource=player,
                        after=tuple(applied),
                        remaining=tuple(sorted(allowed)),
                    )
                    allowed = narrowed
                    break
                allowed = narrowed
                applied.append(player)
        by_piece[piece.name] = tuple(sorted(allowed))

    return StartDomains(by_piece=by_piece, blocker=blocker)


def greedy_placement(
    problem: Problem, domains: StartDomains
) -> dict[str, int]:
    """Earliest-feasible, most-constrained-first placement, used as a CP-SAT hint.

    Partial results are fine -- CP-SAT accepts incomplete hints and ignores wrong ones.
    """
    order = sorted(
        problem.pieces,
        key=lambda p: (len(domains.by_piece[p.name]), -p.duration, p.name),
    )
    room_busy: dict[str, set[int]] = {name: set() for name in problem.rooms}
    person_busy: dict[str, set[int]] = {name: set() for name in problem.people}
    placement: dict[str, int] = {}

    for piece in order:
        for start in domains.by_piece[piece.name]:
            slots = set(range(start, start + piece.duration))
            if slots & room_busy[piece.room]:
                continue
            if any(slots & person_busy[player] for player in piece.players):
                continue
            placement[piece.name] = start
            room_busy[piece.room] |= slots
            for player in piece.players:
                person_busy[player] |= slots
            break
    return placement
