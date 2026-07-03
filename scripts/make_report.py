#!/usr/bin/env python3
"""Render results/reports/summary.md — one table per characterization level / tool-flow phase (issue #4).

Groups every valid result JSON by ``level`` (L0, L0b, L1..L5, PH0..PH6), renders a table per group
with columns curated for that level (PLAN §7 says which numbers matter where), and links each row to
its source JSON. Estimates, measurements, and references are visually distinct via a leading ``kind``
column. Fails loudly on any schema-invalid input rather than skipping it.

Usage:
    python scripts/make_report.py [--out results/reports/summary.md] [--check]

--check exits nonzero if the on-disk summary.md differs from freshly generated output (for CI).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable

import reslib

# Level / phase ordering for the report.
LEVEL_ORDER = ["L0", "L0b", "L1", "L2", "L3", "L4", "L5",
               "PH0", "PH1", "PH2", "PH3", "PH4", "PH5", "PH6"]

KIND_MARK = {"measured": "◆ measured", "estimate": "◇ estimate", "reference": "○ reference"}

# A column is (header, extractor). Extractor takes the result dict, returns a cell string.
Column = tuple[str, Callable[[dict[str, Any]], str]]


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        # engineering-ish: keep it readable, no trailing noise
        if value != value:  # NaN
            return "—"
        if abs(value) >= 1000 or (value and abs(value) < 0.01):
            return f"{value:.3g}"
        return f"{value:g}"
    return str(value)


def _path(dotted: str) -> Callable[[dict[str, Any]], str]:
    def extract(data: dict[str, Any]) -> str:
        cur: Any = data
        for key in dotted.split("."):
            if not isinstance(cur, dict) or key not in cur:
                return "—"
            cur = cur[key]
        return _fmt(cur)
    return extract


# Curated columns per level. Levels not listed fall back to a generic metric union (see below).
LEVEL_COLUMNS: dict[str, list[Column]] = {
    "L0":  [("fmax MHz", _path("metrics.fmax_mhz")), ("MACs/DSP/cyc", _path("metrics.macs_per_dsp_cycle"))],
    "L0b": [("quant", _path("config.quantization")), ("MACs/kALM", _path("metrics.macs_per_kalm")),
            ("fmax MHz", _path("metrics.fmax_mhz"))],
    "L1":  [("fmax MHz", _path("metrics.fmax_mhz")), ("DSPs", _path("config.utilization.dsp"))],
    "L2":  [("GB/s aggregate", _path("metrics.gbps_aggregate"))],
    "L3":  [("HyperBus MHz", _path("config.hyperbus_mhz")), ("sustained MB/s", _path("metrics.sustained_mbps"))],
    "L4":  [("overlay fixed µs", _path("metrics.overlay_fixed_us"))],
    "L5":  [("model", _path("config.model")), ("feed", _path("config.feed_method")),
            ("FPS", _path("metrics.fps")), ("p50 µs", _path("metrics.latency_us_p50")),
            ("p99 µs", _path("metrics.latency_us_p99")), ("acc top1", _path("metrics.accuracy_top1"))],
    "PH0": [("model", _path("config.model")), ("FPS", _path("metrics.fps"))],
    "PH1": [("model", _path("config.model")), ("acc top1", _path("metrics.accuracy_top1"))],
    "PH2": [("model", _path("config.model")), ("acc top1", _path("metrics.accuracy_top1"))],
    "PH5": [("model", _path("config.model")), ("acc top1", _path("metrics.accuracy_top1")),
            ("µJ/inf", _path("metrics.uj_per_inference"))],
}

# Columns every table carries in front of the level-specific ones.
def _kind_col(data: dict[str, Any]) -> str:
    return KIND_MARK.get(data.get("kind", ""), data.get("kind", "?"))


def _generic_columns(rows: list[reslib.ResultFile]) -> list[Column]:
    """Union of metric keys present across rows, sorted — for levels without a curated set."""
    keys: set[str] = set()
    for r in rows:
        metrics = r.data.get("metrics", {})
        if isinstance(metrics, dict):
            keys.update(metrics.keys())
    return [(k, _path(f"metrics.{k}")) for k in sorted(keys)]


def _link(report_dir: Path, result: reslib.ResultFile) -> str:
    """Markdown link from the report dir to the result JSON, with subject as text."""
    subject = result.data.get("subject", result.path.stem)
    try:
        rel = Path(result.path).resolve().relative_to(report_dir.resolve())
    except ValueError:
        import os
        rel = Path(os.path.relpath(result.path.resolve(), report_dir.resolve()))
    return f"[{subject}]({rel.as_posix()})"


def _table(report_dir: Path, level: str, rows: list[reslib.ResultFile]) -> str:
    cols = LEVEL_COLUMNS.get(level) or _generic_columns(rows)
    headers = ["kind", "result"] + [h for h, _ in cols] + ["date"]
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    # Deterministic row order: by date then subject.
    for r in sorted(rows, key=lambda x: (x.data.get("date", ""), x.data.get("subject", ""))):
        cells = [_kind_col(r.data), _link(report_dir, r)]
        cells += [ex(r.data) for _, ex in cols]
        cells.append(_fmt(r.data.get("date")))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_report(results: list[reslib.ResultFile], report_dir: Path) -> str:
    by_level: dict[str, list[reslib.ResultFile]] = {}
    for r in results:
        by_level.setdefault(r.data.get("level", "?"), []).append(r)

    out = ["# AXC3000 benchmark results — summary",
           "",
           "Generated by `scripts/make_report.py` from the result JSONs under `results/`. "
           "Do not edit by hand. Marks: ◆ measured · ◇ estimate · ○ reference.",
           ""]
    if not results:
        out.append("_No results yet._")
        return "\n".join(out) + "\n"

    ordered_levels = [lvl for lvl in LEVEL_ORDER if lvl in by_level]
    ordered_levels += sorted(k for k in by_level if k not in LEVEL_ORDER)
    for level in ordered_levels:
        rows = by_level[level]
        out.append(f"## {level}  ({len(rows)} result{'s' if len(rows) != 1 else ''})")
        out.append("")
        out.append(_table(report_dir, level, rows))
        out.append("")
    return "\n".join(out) + "\n"


def generate(results_dir: Path, out_path: Path) -> str:
    """Validate all results, then return the report text (raises on invalid input)."""
    validator = reslib.make_validator()
    results: list[reslib.ResultFile] = []
    for path in reslib.iter_result_paths(results_dir):
        result = reslib.load_result(path)
        errs = reslib.validate_result(result.data, validator)
        if errs:
            raise ValueError(f"{path} is invalid; run validate_results.py:\n  " + "\n  ".join(errs))
        results.append(result)
    return build_report(results, out_path.parent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(reslib.RESULTS_DIR / "reports" / "summary.md"))
    parser.add_argument("--check", action="store_true",
                        help="exit nonzero if on-disk report differs from generated (CI use)")
    args = parser.parse_args(argv)

    out_path = Path(args.out)
    try:
        text = generate(reslib.RESULTS_DIR, out_path)
    except ValueError as exc:
        print(f"cannot build report: {exc}", file=sys.stderr)
        return 1

    if args.check:
        existing = out_path.read_text() if out_path.exists() else ""
        if existing != text:
            print(f"{out_path} is stale — regenerate with: python scripts/make_report.py", file=sys.stderr)
            return 1
        print(f"{out_path} up to date")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text)
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
