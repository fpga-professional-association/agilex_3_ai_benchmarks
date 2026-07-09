#!/usr/bin/env python3
"""Parse a Quartus fitter/synthesis resource report for DSP-mode breakdown (issue #9, PLAN §3 LV2).

Quartus's own resource-usage tables (both `<rev>.fit.rpt`'s "Fitter Resource Usage Summary" and
`<rev>.syn.rpt`'s "Analysis & Synthesis Resource Usage Summary") break total DSP usage into three
buckets:

    [A] Total Fixed Point DSP Blocks       <- classic 18x19 MAC mode
    [B] Total Floating Point DSP Blocks
    [C] Total DSP_PRIME Blocks             <- the tensor-mode / "Native AI Optimized DSP" block

`[C]` is what this project calls "tensor mode" throughout (PLAN §3 LV2, §7 L0). DSPs silently
falling back to `[A]` instead of `[C]` are a **10x compute-density loss** — this script is the merge
gate every Quartus build must pass (AGENTS.md "Quartus" section): it prints a small table and exits
nonzero the moment any report's `[C]` count does not equal what the caller expected.

**Reports live directly in the Quartus project directory** (e.g. `quartus/l0_tensor_chain/
l0_tensor_chain_n1.fit.rpt`), not under a `quartus/**/output_files/` subdirectory — this is a
Quartus 26.1-in-this-Docker-image behavior documented in docs/toolchain.md and already reflected in
the repo's `.gitignore`; `scripts/README.md`'s "output_files/" description predates that discovery
and does not match observed reality. This script takes explicit report paths, so it does not need
to guess the layout.

Usage:
    python scripts/audit_tensor_mode.py --expect N1=quartus/l0_tensor_chain/l0_tensor_chain_n1.fit.rpt \\
        --expect N8=quartus/l0_tensor_chain/l0_tensor_chain_n8.fit.rpt

    # or a single report with an explicit expected tensor-mode count:
    python scripts/audit_tensor_mode.py --report path/to.fit.rpt --expect-tensor 8
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_PAT_FIXED = re.compile(r"\[A\]\s*Total\s+Fixed\s+Point\s+DSP\s+Blocks\s*;\s*(\d+)")
_PAT_FLOAT = re.compile(r"\[B\]\s*Total\s+Floating\s+Point\s+DSP\s+Blocks\s*;\s*(\d+)")
_PAT_TENSOR = re.compile(r"\[C\]\s*Total\s+DSP_PRIME\s+Blocks\s*;\s*(\d+)")


@dataclass(frozen=True)
class DspBreakdown:
    fixed: int
    floating: int
    tensor: int

    @property
    def total(self) -> int:
        return self.fixed + self.floating + self.tensor


class ReportParseError(ValueError):
    pass


def parse_report(text: str, source: str = "<report>") -> DspBreakdown:
    """Extract the [A]/[B]/[C] DSP-mode counts from a Quartus .fit.rpt or .syn.rpt.

    Raises ReportParseError if any of the three counters cannot be found — a missing counter means
    this isn't a Quartus resource-usage report (or Quartus changed its wording), and silently
    reporting 0 would be indistinguishable from a real classic-mode fallback (AGENTS.md: never fake
    a result).
    """
    m_fixed = _PAT_FIXED.search(text)
    m_float = _PAT_FLOAT.search(text)
    m_tensor = _PAT_TENSOR.search(text)
    missing = [name for name, m in (("[A] Fixed Point", m_fixed), ("[B] Floating Point", m_float),
                                     ("[C] DSP_PRIME", m_tensor)) if m is None]
    if missing:
        raise ReportParseError(
            f"{source}: could not find {', '.join(missing)} DSP Blocks line(s) — "
            "not a Quartus resource-usage report, or its wording changed")
    return DspBreakdown(fixed=int(m_fixed.group(1)), floating=int(m_float.group(1)),
                         tensor=int(m_tensor.group(1)))


def parse_report_file(path: Path) -> DspBreakdown:
    return parse_report(path.read_text(errors="replace"), source=str(path))


def _parse_expect_arg(raw: str) -> tuple[str, int, Path]:
    """Parse one `--expect LABEL=N:PATH` or `--expect LABEL=PATH` (N inferred from the LABEL's
    trailing digits, e.g. `N8=...` -> 8) argument."""
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"--expect needs LABEL=[N:]PATH, got {raw!r}")
    label, rest = raw.split("=", 1)
    if ":" in rest:
        n_str, path_str = rest.split(":", 1)
        n = int(n_str)
    else:
        path_str = rest
        digits = re.search(r"(\d+)$", label)
        if not digits:
            raise argparse.ArgumentTypeError(
                f"--expect {raw!r}: no explicit N: and LABEL has no trailing digits to infer it from")
        n = int(digits.group(1))
    return label, n, Path(path_str)


def audit(checks: list[tuple[str, int, Path]]) -> tuple[bool, str]:
    """Run every (label, expected_tensor_count, report_path) check. Returns (all_ok, table_text)."""
    rows = []
    all_ok = True
    for label, expected, path in checks:
        try:
            bd = parse_report_file(path)
        except (OSError, ReportParseError) as exc:
            rows.append((label, str(path), "?", "?", "?", str(expected), "ERROR", str(exc)))
            all_ok = False
            continue
        ok = bd.tensor == expected
        all_ok &= ok
        rows.append((label, str(path), str(bd.fixed), str(bd.floating), str(bd.tensor),
                     str(expected), "PASS" if ok else "FAIL", ""))

    headers = ("label", "report", "fixed[A]", "float[B]", "tensor[C]", "expected", "result", "note")
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    lines = [" | ".join(h.ljust(w) for h, w in zip(headers, widths)),
             "-+-".join("-" * w for w in widths)]
    for row in rows:
        lines.append(" | ".join(str(c).ljust(w) for c, w in zip(row, widths)))
    return all_ok, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--expect", action="append", default=[], metavar="LABEL=[N:]PATH",
                    help="one check: LABEL=PATH (N inferred from LABEL's trailing digits) or "
                         "LABEL=N:PATH. Repeatable.")
    ap.add_argument("--report", metavar="PATH", help="single-report shorthand; pairs with --expect-tensor")
    ap.add_argument("--expect-tensor", type=int, metavar="N",
                    help="expected [C] DSP_PRIME count for --report")
    args = ap.parse_args(argv)

    checks: list[tuple[str, int, Path]] = []
    for raw in args.expect:
        try:
            checks.append(_parse_expect_arg(raw))
        except argparse.ArgumentTypeError as exc:
            ap.error(str(exc))

    if args.report is not None:
        if args.expect_tensor is None:
            ap.error("--report requires --expect-tensor")
        checks.append((Path(args.report).stem, args.expect_tensor, Path(args.report)))

    if not checks:
        ap.error("nothing to check — pass --expect LABEL=[N:]PATH and/or --report/--expect-tensor")

    all_ok, table = audit(checks)
    print(table)
    if not all_ok:
        print("\nFAIL: at least one report's tensor-mode ([C] DSP_PRIME) block count did not match "
              "the expectation — classic-mode fallback is a silent 10x compute-density loss "
              "(PLAN §3 LV2).", file=sys.stderr)
        return 1
    print("\nPASS: every report's tensor-mode DSP count matched expectation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
