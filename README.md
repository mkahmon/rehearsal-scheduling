# rehearsal

Builds a rehearsal schedule for a music ensemble that minimises how long players wait
between their calls.

Given rooms, players, and pieces, it assigns each piece to a contiguous block of
30-minute slots in its required room such that no room and no player is ever
double-booked, every piece fits inside its room's and its players' availability, and
total waiting time is as small as it can possibly be. It uses a CP-SAT constraint
solver, so on problems of this size it does not just find a good schedule — it proves
no better one exists.

```bash
uv venv && uv pip install -e ".[dev]"
uv run schedule examples/quartet.yaml
```

## Input

YAML or JSON. Times are `HH:MM` and must fall on 30-minute boundaries.

```yaml
day: { start: "09:00", end: "17:00" }

rooms:
  Recital Hall: ["09:00-12:00", "13:00-17:00"]   # windows the room is open
  Studio B: all                                  # or omit the value entirely

people:
  Ana: all
  Ben: ["09:00-11:00", "14:00-17:00"]            # windows the player is free

pieces:
  - name: Brahms Op. 114
    room: Recital Hall      # required; a piece happens in exactly one room
    slots: 3                # 90 minutes, contiguous. default 1
    players: [Ana, Ben]
```

Anyone named in a `players:` list but not under `people:` is assumed free all day, so
for a simple problem you can leave `people:` out entirely.

## What "waiting" means

For each player, waiting time is the time between the start of their first rehearsal
and the end of their last, during which they were **free but not playing**:

```
idle(player) = slots inside their span, where they were available, minus slots spent rehearsing
```

The availability part matters. If Ben is free 09:00–11:00 and 14:00–17:00, and is called
at 10:30 and again at 14:00, his waiting time is **zero** — he is not hanging around
during the three hours he told us he was elsewhere. Scoring that as three hours of
waiting would systematically penalise anyone with a mid-day commitment and push the
optimiser to cram them at everyone else's expense.

The objective is the sum of `idle(player)` over everyone. Players in one piece or none
always score zero.

## Output

A room-by-room timetable, a per-player itinerary that distinguishes waiting from being
away, and a summary:

```
Time   Recital Hall    Studio B
-----  --------------  ------------
09:00  -               -
09:30  Brahms Op. 114  -
...

Ben - 0 min waiting
  09:30-11:00   Brahms Op. 114
  11:00-14:00   (unavailable)
  14:00-15:00   Schubert D.667

Dev - 60 min waiting
  12:00-13:00   Ravel Trio
  13:00-14:00   (waiting)
  14:00-15:00   Schubert D.667

Total waiting: 60 min
Affects 1 of 4 players; worst is Dev at 60 min
Status: proven optimal
```

`--json PATH` also writes the schedule in machine-readable form.

## When it does not fit

No partial schedules are ever returned — a schedule that comes back is always complete
and valid. If the constraints cannot all be met, the tool explains why, cheapest and
most specific explanation first, and suggests the smallest change that would fix it:

```
No valid schedule exists.
  - Room 'Recital Hall' must fit 420 minutes of rehearsal ('Brahms Quintet',
    'Mozart K.581', 'Schubert Trout') into 10:00-17:00, where only 360 minutes are free.

What to change:
  - Everything else fits once 'Schubert Trout' is removed. Move it to another room,
    shorten it, or widen someone's availability.
```

`--explain` additionally computes a minimal set of constraints that cannot hold
together. It is slower and usually less actionable than the messages above, so reach
for it only when they are not enough.

## Options

| Flag | Meaning |
|---|---|
| `--time-limit SECONDS` | Solver budget. Default 30. |
| `--gap FRACTION` | Stop once within this fraction of optimal, e.g. `0.02`. Default: prove exactly. |
| `--json PATH` | Also write the schedule as JSON. |
| `--explain` | On failure, compute a minimal conflicting set of constraints. |
| `--verbose` | Log solver progress. |

| Code | Meaning |
|---|---|
| `0` | A schedule was produced, and it is proven optimal (or met the `--gap` you asked for). |
| `1` | Proven infeasible. No schedule exists. |
| `2` | The input could not be parsed or validated. |
| `3` | A valid schedule was produced, but not proven optimal within the time limit. |
| `4` | The time limit ran out before *any* schedule was found. Nothing is proved either way. |
| `70` | Internal error. |

Codes `3` and `4` are deliberately distinct from `1`. Running out of time proves
nothing, so a timeout is never reported as "no valid schedule exists". For `3`, the
schedule printed is valid and usually good — the tool simply will not claim it is the
best. Raise `--time-limit`, or pass `--gap 0.02` to accept "within 2% of optimal" as
success.

## Scale

The examples here (13–20 pieces) prove optimality in well under a second. Sum-of-span
scheduling objectives are known for weak lower bounds, so on substantially larger or
more tightly-constrained instances the pattern to expect is that a good schedule appears
quickly while the *proof* takes much longer. Run with `--verbose` to see whether time is
going into finding a better schedule or into closing the bound; if it is the bound and
you do not need a certificate, `--gap` is the right lever.

Everything is modelled on a single day. Multi-day scheduling would need the span
computed per day rather than across the whole grid, otherwise an overnight gap counts
as waiting.

## Development

```bash
uv run pytest
```

`tests/test_objective_equivalence.py` is the important one: it brute-forces every valid
placement on 200 small random instances and checks that the solver's optimum matches a
plain-Python statement of the definition. The constraint encoding of "waiting time" has
several subtly-wrong variants that still produce plausible-looking schedules, and that
test is what rules them out.

| Module | Responsibility |
|---|---|
| `model.py` | Time grid, entities, and `idle_slots` — the definition of the objective |
| `parsing.py` | YAML/JSON to `Problem` |
| `validate.py` | Structural checks and normalisation |
| `starts.py` | Feasible start slots per piece; greedy hint |
| `solver.py` | The CP-SAT model |
| `diagnose.py` | Why it does not fit, and what to change |
| `report.py` | Timetable, itineraries, JSON |
| `cli.py` | Argument handling and exit codes |
