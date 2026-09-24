"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rehearsal.diagnose import diagnose
from rehearsal.model import ProblemError
from rehearsal.parsing import load_problem
from rehearsal.report import render, to_json
from rehearsal.solver import ModelInvalid, SearchExhausted, SolveOptions, solve
from rehearsal.starts import feasible_starts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schedule",
        description="Build a rehearsal schedule that minimises how long players wait.",
    )
    parser.add_argument("input", type=Path, help="YAML or JSON problem description")
    parser.add_argument(
        "--time-limit",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="solver budget (default: 30)",
    )
    parser.add_argument(
        "--gap",
        type=float,
        default=0.0,
        metavar="FRACTION",
        help="stop once within this fraction of optimal, e.g. 0.02 (default: prove exactly)",
    )
    parser.add_argument(
        "--json", type=Path, metavar="PATH", help="also write the schedule as JSON"
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="on failure, also compute a minimal conflicting set of constraints",
    )
    parser.add_argument("--verbose", action="store_true", help="log solver progress")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        problem, warnings = load_problem(args.input)
    except ProblemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    options = SolveOptions(
        time_limit=args.time_limit,
        log=args.verbose,
        relative_gap=args.gap,
    )
    try:
        schedule = solve(problem, options, warnings=())
    except SearchExhausted as exc:
        # Not the same as infeasible: nothing has been proved either way.
        print(
            f"error: {exc}. The problem may still be solvable -- raise --time-limit.",
            file=sys.stderr,
        )
        return 4
    except ModelInvalid as exc:
        print(
            "internal error: the constraint model was rejected. This is a bug in "
            f"rehearsal, not a problem with your input.\n{exc}",
            file=sys.stderr,
        )
        return 70

    if schedule is None:
        result = diagnose(
            problem,
            feasible_starts(problem),
            explain=args.explain,
            time_limit=min(args.time_limit, 10.0),
        )
        print(result.text(), file=sys.stderr)
        return 1

    print(render(schedule))
    if args.json:
        args.json.write_text(to_json(schedule) + "\n")
        print(f"\nWrote {args.json}", file=sys.stderr)
    if schedule.proven_optimal:
        return 0
    # An explicitly requested tolerance counts as success once it is actually met.
    if args.gap > 0 and schedule.gap is not None and schedule.gap <= args.gap:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
