#!/usr/bin/env python3
"""Validate every result JSON under results/ against the schema + cross-field rules (issue #4).

Exit code 0 iff every file is valid. On failure, prints per-file errors and exits nonzero so CI
(``.github/workflows/ci.yml``) and humans both catch bad results. Invalid files are never skipped
silently (PLAN §10: numbers without configs are noise).

Usage:
    python scripts/validate_results.py [PATH ...]

With no PATH args, validates all of results/ (excluding schema/ and reports/). With PATH args,
validates exactly those files/dirs (used by tests and pre-commit checks).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import reslib


def _collect(paths: list[str]) -> list[Path]:
    if not paths:
        return reslib.iter_result_paths()
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(sorted(p.rglob("*.json")))
        else:
            out.append(p)
    return out


def validate_paths(paths: list[Path]) -> tuple[int, list[str]]:
    """Return (n_invalid, messages). messages is a flat list ready to print."""
    validator = reslib.make_validator()
    messages: list[str] = []
    n_invalid = 0
    for path in paths:
        try:
            result = reslib.load_result(path)
        except (OSError, ValueError) as exc:  # ValueError covers JSONDecodeError
            n_invalid += 1
            messages.append(f"FAIL {path}: could not read/parse: {exc}")
            continue
        errs = reslib.validate_result(result.data, validator)
        if errs:
            n_invalid += 1
            messages.append(f"FAIL {path}:")
            messages.extend(f"    - {e}" for e in errs)
        else:
            messages.append(f"ok   {path}")
    return n_invalid, messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="result files/dirs (default: all of results/)")
    parser.add_argument("-q", "--quiet", action="store_true", help="only print failures")
    args = parser.parse_args(argv)

    paths = _collect(args.paths)
    if not paths:
        print("no result files found (nothing to validate)")
        return 0

    n_invalid, messages = validate_paths(paths)
    for msg in messages:
        if args.quiet and msg.startswith("ok "):
            continue
        print(msg)

    n_total = len(paths)
    if n_invalid:
        print(f"\n{n_invalid}/{n_total} result file(s) INVALID", file=sys.stderr)
        return 1
    print(f"\nall {n_total} result file(s) valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
