"""Core data model: a time grid of fixed-length slots, and the entities placed on it.

All times are normalised to integer slot indices at construction time. Nothing
downstream of parsing deals in clock time except the report layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SLOT_MINUTES = 30

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


class ProblemError(ValueError):
    """Raised when input cannot be turned into a well-formed Problem."""


def parse_time(text: str) -> int:
    """Parse "HH:MM" into minutes from midnight. "24:00" is allowed as a day end."""
    m = _TIME_RE.match(str(text).strip())
    if not m:
        raise ProblemError(f"invalid time {text!r}; expected HH:MM, e.g. 09:30")
    hours, minutes = int(m.group(1)), int(m.group(2))
    if minutes > 59 or hours > 24 or (hours == 24 and minutes != 0):
        raise ProblemError(f"invalid time {text!r}")
    return hours * 60 + minutes


def format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass(frozen=True)
class TimeGrid:
    """A single day divided into `n_slots` consecutive slots of `slot_minutes`."""

    day_start: int
    n_slots: int
    slot_minutes: int = SLOT_MINUTES

    @classmethod
    def from_times(cls, start: str, end: str, slot_minutes: int = SLOT_MINUTES) -> TimeGrid:
        first, last = parse_time(start), parse_time(end)
        if last <= first:
            raise ProblemError(f"day end {end!r} must be after day start {start!r}")
        span = last - first
        if span % slot_minutes:
            raise ProblemError(
                f"day {start}-{end} is {span} minutes, not a whole number of "
                f"{slot_minutes}-minute slots"
            )
        return cls(day_start=first, n_slots=span // slot_minutes, slot_minutes=slot_minutes)

    @property
    def day_end(self) -> int:
        return self.day_start + self.n_slots * self.slot_minutes

    def slot_start(self, slot: int) -> int:
        return self.day_start + slot * self.slot_minutes

    def slot_end(self, slot: int) -> int:
        return self.slot_start(slot) + self.slot_minutes

    def all_slots(self) -> frozenset[int]:
        return frozenset(range(self.n_slots))

    def minutes_to_slot(self, minutes: int, *, what: str) -> int:
        """Convert a boundary time to its slot index, requiring grid alignment."""
        offset = minutes - self.day_start
        if offset % self.slot_minutes:
            raise ProblemError(
                f"{what} {format_time(minutes)} is not on a {self.slot_minutes}-minute "
                f"boundary from the day start {format_time(self.day_start)}"
            )
        return offset // self.slot_minutes

    def parse_window(self, window: str, *, what: str) -> frozenset[int]:
        """Parse "09:00-11:00" into the set of slots it fully covers."""
        text = str(window).strip()
        if "-" not in text:
            raise ProblemError(f"invalid window {window!r} for {what}; expected HH:MM-HH:MM")
        left, _, right = text.partition("-")
        start, end = parse_time(left), parse_time(right)
        if end <= start:
            raise ProblemError(f"window {window!r} for {what} ends before it starts")
        if start < self.day_start or end > self.day_end:
            raise ProblemError(
                f"window {window!r} for {what} falls outside the day "
                f"{format_time(self.day_start)}-{format_time(self.day_end)}"
            )
        lo = self.minutes_to_slot(start, what=f"window start for {what}")
        hi = self.minutes_to_slot(end, what=f"window end for {what}")
        return frozenset(range(lo, hi))

    def format_span(self, start_slot: int, n_slots: int) -> str:
        return (
            f"{format_time(self.slot_start(start_slot))}-"
            f"{format_time(self.slot_start(start_slot + n_slots))}"
        )


@dataclass(frozen=True)
class Room:
    name: str
    available: frozenset[int]


@dataclass(frozen=True)
class Person:
    name: str
    available: frozenset[int]


@dataclass(frozen=True)
class Piece:
    name: str
    room: str
    duration: int = 1
    players: tuple[str, ...] = ()


@dataclass(frozen=True)
class Problem:
    grid: TimeGrid
    rooms: dict[str, Room]
    people: dict[str, Person]
    pieces: tuple[Piece, ...]

    def pieces_in_room(self, room: str) -> tuple[Piece, ...]:
        return tuple(p for p in self.pieces if p.room == room)

    def pieces_for_person(self, person: str) -> tuple[Piece, ...]:
        return tuple(p for p in self.pieces if person in p.players)


@dataclass(frozen=True)
class Assignment:
    piece: Piece
    start: int

    @property
    def end(self) -> int:
        """Exclusive end slot."""
        return self.start + self.piece.duration

    def slots(self) -> range:
        return range(self.start, self.end)


@dataclass(frozen=True)
class Schedule:
    """A complete, valid assignment of every piece, with its cost breakdown."""

    problem: Problem
    assignments: tuple[Assignment, ...]
    idle_slots_by_person: dict[str, int]
    status: str
    proven_optimal: bool
    best_bound_slots: int | None = None
    #: Relative distance from the proven lower bound; 0.0 when proven optimal.
    gap: float | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_idle_slots(self) -> int:
        return sum(self.idle_slots_by_person.values())

    @property
    def total_idle_minutes(self) -> int:
        return self.total_idle_slots * self.problem.grid.slot_minutes

    def start_of(self, piece_name: str) -> int:
        for a in self.assignments:
            if a.piece.name == piece_name:
                return a.start
        raise KeyError(piece_name)


def idle_slots(problem: Problem, assignments: tuple[Assignment, ...]) -> dict[str, int]:
    """Availability-aware idle time per person, in slots.

    A person's idle time is the number of slots between the start of their first
    rehearsal and the end of their last during which they were free but not playing.
    Slots they were unavailable for do not count: someone who told us they are gone
    from 11:00 to 14:00 is not waiting during it.

    This is the definition the CP-SAT objective encodes; `tests/test_objective_equivalence`
    checks the encoding against this function over brute-forced instances.
    """
    busy: dict[str, set[int]] = {name: set() for name in problem.people}
    for assignment in assignments:
        for player in assignment.piece.players:
            busy[player].update(assignment.slots())

    idle: dict[str, int] = {}
    for name, person in problem.people.items():
        occupied = busy[name]
        if len(occupied) <= 1:
            idle[name] = 0
            continue
        span = range(min(occupied), max(occupied) + 1)
        idle[name] = sum(1 for t in span if t in person.available and t not in occupied)
    return idle
