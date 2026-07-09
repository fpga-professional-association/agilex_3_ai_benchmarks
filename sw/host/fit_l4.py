#!/usr/bin/env python3
"""L4 overlay fixed-cost fit: least-squares `latency_us_p50 = overhead_us + macs / rate` (issue #20).

PLAN §7 L4: sequenced overlays carry a fixed per-inference cost (descriptor setup, config, pipeline
drain) that dominates sub-millisecond models. Feed this script the per-point `results/` JSONs from a
`sw/model_prep/make_sweep_graphs.py` sweep run under method A on silicon (issue #18) and it fits the
intercept (the overhead, in microseconds) and slope (inverse achieved MAC/s), reports R^2 and a 95%
CI on the intercept, and refuses to fit — no JSON written, nonzero exit — if R^2 < 0.98 ("bad fit is
bad data, not a smaller font"). Per issue #20 "Do not": fits *latency*, never FPS/throughput, and
only takes method-A points (method-B memory noise breaks the fixed-cost model).

This is the SOFTWARE half of #20 (the fit math itself is buildable ahead of hardware, tested here
against synthetic/mock latency data). Running it against real silicon measurements is the
"## Hardware handoff" in the #20 PR.

Usage:
    python fit_l4.py --points results/l4_sweep_d*.json --out results/l4_overlay_fixed_cost_fit.json
    python fit_l4.py --points results/l4_sweep_d*.json --out results/l4_fit.json \\
        --overhead-fraction-for results/l5_dscnn-kws_methodA.json results/l5_ad-toycar_methodA.json
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEVICE = "A3CY100BM16AE7S"
R2_MIN = 0.98  # issue #20 deliverable: "refuses to fit if R^2 < 0.98"

# Student's t two-tailed 95% critical values, df 1..30 (standard table); df>30 falls back to the
# normal approximation (1.96). No scipy dependency (AGENTS.md: stdlib + numpy by default).
_T_CRIT_95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
    10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
    18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}


def t_crit_95(dof: int) -> float:
    if dof < 1:
        raise ValueError("need at least 1 degree of freedom for a confidence interval")
    if dof in _T_CRIT_95:
        return _T_CRIT_95[dof]
    if dof > 30:
        return 1.96
    return _T_CRIT_95[min(_T_CRIT_95, key=lambda d: abs(d - dof))]


class FitQualityError(ValueError):
    """R^2 below R2_MIN, or a non-positive slope -- refuse to fit rather than report a bad constant."""


@dataclass(frozen=True)
class FitResult:
    n: int
    intercept_us: float
    slope_us_per_mac: float
    achieved_macs_per_s: float
    r_squared: float
    dof: int
    intercept_stderr_us: float
    intercept_ci95_us: tuple[float, float]
    residuals_us: list[float]


def least_squares_fit(macs: list[float], latency_us: list[float]) -> FitResult:
    """Ordinary least squares of latency_us on macs (issue #20: fit latency, never FPS).

    Raises ValueError (not FitQualityError -- that's the R^2/slope-sign gate in `fit_and_check`) on
    malformed input: mismatched lengths, too few points for a residual-based CI (need n>=3 for
    dof>=1), or zero spread in the x values (can't fit a slope).
    """
    n = len(macs)
    if n != len(latency_us):
        raise ValueError(f"macs ({n}) and latency_us ({len(latency_us)}) must be the same length")
    if n < 3:
        raise ValueError(f"need >=3 sweep points to fit with a residual-based CI, got {n}")

    xbar = sum(macs) / n
    ybar = sum(latency_us) / n
    sxx = sum((x - xbar) ** 2 for x in macs)
    sxy = sum((x - xbar) * (y - ybar) for x, y in zip(macs, latency_us))
    if sxx == 0:
        raise ValueError("all MACs values are identical -- cannot fit a slope (no spread in x)")

    slope = sxy / sxx
    intercept = ybar - slope * xbar
    residuals = [y - (intercept + slope * x) for x, y in zip(macs, latency_us)]
    ss_res = sum(r * r for r in residuals)
    ss_tot = sum((y - ybar) ** 2 for y in latency_us)
    r2 = 1.0 if ss_tot == 0 and ss_res == 0 else (0.0 if ss_tot == 0 else 1.0 - ss_res / ss_tot)

    dof = n - 2
    mse = ss_res / dof if dof > 0 else 0.0
    se_intercept = math.sqrt(mse * (1.0 / n + (xbar * xbar) / sxx))
    tc = t_crit_95(dof) if dof >= 1 else float("nan")
    ci = (intercept - tc * se_intercept, intercept + tc * se_intercept)

    achieved_rate = (1.0e6 / slope) if slope > 0 else float("nan")  # us/MAC -> MAC/s

    return FitResult(
        n=n, intercept_us=intercept, slope_us_per_mac=slope, achieved_macs_per_s=achieved_rate,
        r_squared=r2, dof=dof, intercept_stderr_us=se_intercept, intercept_ci95_us=ci,
        residuals_us=residuals,
    )


def fit_and_check(macs: list[float], latency_us: list[float], *, r2_min: float = R2_MIN) -> FitResult:
    """`least_squares_fit` plus the issue #20 refusal gate. Raises FitQualityError, writes nothing."""
    result = least_squares_fit(macs, latency_us)
    if result.slope_us_per_mac <= 0:
        raise FitQualityError(
            f"fitted slope is non-positive ({result.slope_us_per_mac:.6g} us/MAC) -- latency should "
            "increase with MACs; check for method-B points mixed into the fit (issue #20 'Do not') "
            "or per-point config drift (fclk/arch changed mid-sweep)"
        )
    if result.r_squared < r2_min:
        raise FitQualityError(
            f"R^2={result.r_squared:.4f} < {r2_min} threshold -- refusing to fit (bad fit is bad "
            "data, not a smaller font; issue #20 deliverable). Check p50 vs mean contamination, "
            "method-A/B mixing, or a sweep point whose config drifted mid-run."
        )
    return result


def overhead_fraction(overhead_us: float, model_latency_us: float) -> float:
    """What fraction of a model's own p50 latency is the fixed overlay overhead (issue #20 report)."""
    if model_latency_us <= 0:
        raise ValueError(f"model_latency_us must be positive, got {model_latency_us}")
    return overhead_us / model_latency_us


# --------------------------------------------------------------------------------------------
# Loading sweep-point result JSONs
# --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SweepMeasurement:
    subject: str
    macs: float
    latency_us: float
    path: Path
    config: dict[str, Any]


def load_l4_measurements(paths: list[Path]) -> list[SweepMeasurement]:
    """Load + validate one `results/` JSON per sweep point (kind=measured, level=L4, feed_method=A).

    Fails loudly (ValueError naming the offending file) on anything the fit can't safely consume --
    a missing macs_per_inference/latency_us_p50, wrong kind/level, or a method-B point (issue #20
    'Do not mix method B points into the fit'). No silent skips.
    """
    measurements = []
    for path in paths:
        path = Path(path)
        data = json.loads(path.read_text())
        if data.get("kind") != "measured":
            raise ValueError(f"{path}: kind={data.get('kind')!r}, fit_l4 only fits kind='measured' points")
        if data.get("level") != "L4":
            raise ValueError(f"{path}: level={data.get('level')!r}, expected 'L4'")
        config = data.get("config", {}) or {}
        feed_method = config.get("feed_method")
        if feed_method != "A":
            raise ValueError(
                f"{path}: config.feed_method={feed_method!r} -- fit_l4 only takes method-A points "
                "(issue #20 'Do not mix method B points into the fit': memory noise breaks the model)"
            )
        macs = config.get("macs_per_inference")
        if macs is None:
            raise ValueError(f"{path}: missing config.macs_per_inference")
        latency = (data.get("metrics", {}) or {}).get("latency_us_p50")
        if latency is None:
            raise ValueError(
                f"{path}: missing metrics.latency_us_p50 (issue #20 step 3: fit on p50, mean is "
                "bimodal-tail contaminated)"
            )
        measurements.append(SweepMeasurement(
            subject=data.get("subject", path.stem), macs=float(macs), latency_us=float(latency),
            path=path, config=config,
        ))
    return measurements


def _shared_config_field(measurements: list[SweepMeasurement], key: str) -> Any:
    """The value of `key` if every measurement's config agrees, else None (caller notes the drift)."""
    values = {m.config.get(key) for m in measurements}
    return values.pop() if len(values) == 1 else None


# --------------------------------------------------------------------------------------------
# Result assembly
# --------------------------------------------------------------------------------------------

def build_result(measurements: list[SweepMeasurement], fit: FitResult, *, date: str,
                  subject: str = "l4-overlay-fixed-cost-fit", plan_ref: str = "§7 L4",
                  fraction_lines: list[str] | None = None) -> dict[str, Any]:
    ci_lo, ci_hi = fit.intercept_ci95_us
    fclk_mhz = _shared_config_field(measurements, "fclk_mhz")
    arch_file = _shared_config_field(measurements, "arch_file")
    tool_versions = next((m.config.get("tool_versions") for m in measurements if m.config.get("tool_versions")), {})

    drift_notes = []
    if fclk_mhz is None:
        drift_notes.append("WARNING: fclk_mhz differs across sweep points -- see source files")
    if arch_file is None:
        drift_notes.append("WARNING: arch_file differs across sweep points -- see source files")

    points_desc = "; ".join(f"{m.subject}: {m.macs:.0f} MACs -> {m.latency_us:.4f} us" for m in
                             sorted(measurements, key=lambda m: m.macs))
    notes = (
        f"n={fit.n}, dof={fit.dof}, R^2={fit.r_squared:.5f}, intercept={fit.intercept_us:.4f} us "
        f"(95% CI [{ci_lo:.4f}, {ci_hi:.4f}] us), slope={fit.slope_us_per_mac:.6g} us/MAC, "
        f"achieved_rate={fit.achieved_macs_per_s:.6g} MAC/s. Points: {points_desc}. "
        f"Source: sw/host/fit_l4.py over {len(measurements)} sw/model_prep/make_sweep_graphs.py "
        "sweep points, method A (PLAN §8), median (p50) latency per issue #20 step 3."
    )
    if fraction_lines:
        notes += " Overhead fractions: " + "; ".join(fraction_lines) + "."
    if drift_notes:
        notes += " " + " ".join(drift_notes)

    config: dict[str, Any] = {"device": DEVICE, "board": "Arrow AXC3000", "feed_method": "A"}
    if fclk_mhz is not None:
        config["fclk_mhz"] = fclk_mhz
    if arch_file is not None:
        config["arch_file"] = arch_file
    if tool_versions:
        config["tool_versions"] = tool_versions

    return {
        "kind": "measured",
        "level": "L4",
        "subject": subject,
        "date": date,
        "plan_ref": plan_ref,
        "config": config,
        "metrics": {
            "overlay_fixed_us": round(fit.intercept_us, 4),
            "n_records": fit.n,
        },
        "notes": notes,
    }


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--points", nargs="+", required=True,
                     help="L4 sweep-point results/ JSONs or glob patterns (>=3 needed, >=5 for issue #20)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--subject", default="l4-overlay-fixed-cost-fit")
    ap.add_argument("--plan-ref", default="§7 L4")
    ap.add_argument("--date", default=None)
    ap.add_argument("--r2-min", type=float, default=R2_MIN)
    ap.add_argument("--overhead-fraction-for", nargs="*", default=[],
                     help="L5 method-A results/ JSONs (e.g. DS-CNN, AD) to report overhead fraction for")
    args = ap.parse_args(argv)

    resolved: set[str] = set()
    for pattern in args.points:
        matches = glob.glob(pattern)
        if matches:
            resolved.update(matches)
        elif Path(pattern).exists():
            resolved.add(pattern)  # literal path, not a glob pattern
    point_paths = sorted(resolved)
    if not point_paths:
        print(f"no files matched --points {args.points}", file=sys.stderr)
        return 2

    try:
        measurements = load_l4_measurements([Path(p) for p in point_paths])
        fit = fit_and_check([m.macs for m in measurements], [m.latency_us for m in measurements],
                             r2_min=args.r2_min)
    except (ValueError, FitQualityError) as exc:
        print(f"fit_l4: {exc}", file=sys.stderr)
        return 1

    fraction_lines = []
    for fp in args.overhead_fraction_for:
        data = json.loads(Path(fp).read_text())
        model_latency = (data.get("metrics", {}) or {}).get("latency_us_p50")
        if model_latency is None:
            print(f"fit_l4: {fp}: missing metrics.latency_us_p50, skipping overhead-fraction", file=sys.stderr)
            continue
        frac = overhead_fraction(fit.intercept_us, model_latency)
        subject = data.get("subject", fp)
        fraction_lines.append(f"{subject}: {frac:.1%} of {model_latency:.2f} us")
        print(f"{subject}: overlay overhead is {frac:.1%} of p50 latency ({model_latency:.2f} us)")

    result = build_result(measurements, fit, date=args.date or _today(), subject=args.subject,
                           plan_ref=args.plan_ref, fraction_lines=fraction_lines or None)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    ci_lo, ci_hi = fit.intercept_ci95_us
    print(f"wrote {args.out}: overlay_fixed_us={fit.intercept_us:.4f} "
          f"(95% CI [{ci_lo:.4f}, {ci_hi:.4f}]), R^2={fit.r_squared:.5f}, n={fit.n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
