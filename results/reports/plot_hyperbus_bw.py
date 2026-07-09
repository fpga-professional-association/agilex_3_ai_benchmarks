#!/usr/bin/env python3
"""Plot HyperBus sustained bandwidth vs burst length from the PH3 result JSONs (issue: PH3 hyperbus bw report).

Reads every ``results/ph3_hyperbus_bw_len*.json`` (kind=measured, level=PH3), extracts
``metrics.burst_words``/``metrics.write_mbps``/``metrics.read_mbps``, and renders a two-series
line chart (write, read) vs burst length in words. Uses matplotlib (Agg backend) if available;
otherwise falls back to a hand-written deterministic SVG line chart. Never hard-codes the
measured numbers -- they are parsed from the JSONs on every run.

Usage:
    python3 results/reports/plot_hyperbus_bw.py [--results-dir results] [--out-svg PATH] [--out-png PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parent
REPO_ROOT = REPORT_DIR.parent.parent


def load_points(results_dir: Path) -> list[dict]:
    """Load (burst_words, write_mbps, read_mbps) tuples from the PH3 hyperbus bw result JSONs."""
    points = []
    for path in sorted(results_dir.glob("ph3_hyperbus_bw_len*.json")):
        data = json.loads(path.read_text())
        if data.get("level") != "PH3" or data.get("kind") != "measured":
            continue
        metrics = data.get("metrics", {})
        if "burst_words" not in metrics:
            continue
        points.append({
            "burst_words": metrics["burst_words"],
            "write_mbps": metrics["write_mbps"],
            "read_mbps": metrics["read_mbps"],
            "path": path.name,
        })
    points.sort(key=lambda p: p["burst_words"])
    if not points:
        raise SystemExit(f"no ph3_hyperbus_bw_len*.json result files found under {results_dir}")
    return points


def plot_matplotlib(points: list[dict], out_svg: Path, out_png: Path | None) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = [p["burst_words"] for p in points]
    wr = [p["write_mbps"] for p in points]
    rd = [p["read_mbps"] for p in points]

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    ax.plot(x, wr, marker="o", color="#1f77b4", label="write MB/s")
    ax.plot(x, rd, marker="s", color="#d62728", label="read MB/s")
    ax.set_xlabel("burst length (words)")
    ax.set_ylabel("sustained MB/s")
    ax.set_title("HyperBus sustained bandwidth vs burst length\nAXC3000, CK=175 MHz SDR PHY (measured)")
    ax.set_xscale("log", base=2)
    ax.set_xticks(x)
    ax.set_xticklabels([str(v) for v in x])
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(out_svg)
    if out_png is not None:
        fig.savefig(out_png)
    plt.close(fig)


def plot_svg_fallback(points: list[dict], out_svg: Path) -> None:
    """Hand-written deterministic SVG line chart (no matplotlib dependency)."""
    W, H = 720, 460
    ml, mr, mt, mb = 70, 30, 50, 60
    plot_w, plot_h = W - ml - mr, H - mt - mb

    xs = [p["burst_words"] for p in points]
    all_y = [p["write_mbps"] for p in points] + [p["read_mbps"] for p in points]
    y_min, y_max = 0.0, max(all_y) * 1.1

    # log2 x-axis positions, evenly spaced by index (categorical-log since burst lengths are power-of-2-ish)
    def xpos(i: int) -> float:
        return ml + (plot_w * i / (len(xs) - 1) if len(xs) > 1 else plot_w / 2)

    def ypos(v: float) -> float:
        return mt + plot_h - (v - y_min) / (y_max - y_min) * plot_h

    def polyline(key: str) -> str:
        pts = " ".join(f"{xpos(i):.1f},{ypos(p[key]):.1f}" for i, p in enumerate(points))
        return pts

    y_ticks = 6
    grid_lines = []
    y_labels = []
    for t in range(y_ticks + 1):
        v = y_min + (y_max - y_min) * t / y_ticks
        y = ypos(v)
        grid_lines.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{ml+plot_w}" y2="{y:.1f}" '
                           f'stroke="currentColor" stroke-opacity="0.15" stroke-width="1"/>')
        y_labels.append(f'<text x="{ml-10}" y="{y+4:.1f}" text-anchor="end" font-size="12" '
                         f'fill="currentColor">{v:.0f}</text>')

    x_labels = []
    x_ticks_marks = []
    for i, p in enumerate(points):
        x = xpos(i)
        x_labels.append(f'<text x="{x:.1f}" y="{mt+plot_h+22}" text-anchor="middle" font-size="12" '
                         f'fill="currentColor">{p["burst_words"]}</text>')
        x_ticks_marks.append(f'<line x1="{x:.1f}" y1="{mt+plot_h}" x2="{x:.1f}" y2="{mt+plot_h+5}" '
                              f'stroke="currentColor"/>')

    write_pts = polyline("write_mbps")
    read_pts = polyline("read_mbps")

    def markers(key: str, color: str) -> str:
        out = []
        for i, p in enumerate(points):
            out.append(f'<circle cx="{xpos(i):.1f}" cy="{ypos(p[key]):.1f}" r="3.5" fill="{color}"/>')
        return "".join(out)

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}"
     font-family="sans-serif" color="#222">
  <style>
    @media (prefers-color-scheme: dark) {{ svg {{ color: #ddd; }} }}
  </style>
  <rect x="0" y="0" width="{W}" height="{H}" fill="none"/>
  {''.join(grid_lines)}
  <line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt+plot_h}" stroke="currentColor"/>
  <line x1="{ml}" y1="{mt+plot_h}" x2="{ml+plot_w}" y2="{mt+plot_h}" stroke="currentColor"/>
  {''.join(y_labels)}
  {''.join(x_labels)}
  {''.join(x_ticks_marks)}
  <polyline points="{write_pts}" fill="none" stroke="#1f77b4" stroke-width="2"/>
  <polyline points="{read_pts}" fill="none" stroke="#d62728" stroke-width="2"/>
  {markers('write_mbps', '#1f77b4')}
  {markers('read_mbps', '#d62728')}
  <text x="{W/2:.1f}" y="24" text-anchor="middle" font-size="15" font-weight="bold" fill="currentColor">
    HyperBus sustained bandwidth vs burst length
  </text>
  <text x="{W/2:.1f}" y="{H-14}" text-anchor="middle" font-size="12" fill="currentColor">
    burst length (words) -- AXC3000, CK=175 MHz SDR PHY (measured)
  </text>
  <text x="18" y="{mt+plot_h/2:.1f}" text-anchor="middle" font-size="12" fill="currentColor"
        transform="rotate(-90 18 {mt+plot_h/2:.1f})">sustained MB/s</text>
  <rect x="{W-190}" y="{mt}" width="14" height="14" fill="#1f77b4"/>
  <text x="{W-170}" y="{mt+11}" font-size="12" fill="currentColor">write MB/s</text>
  <rect x="{W-190}" y="{mt+20}" width="14" height="14" fill="#d62728"/>
  <text x="{W-170}" y="{mt+31}" font-size="12" fill="currentColor">read MB/s</text>
</svg>
'''
    out_svg.write_text(svg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results-dir", default=str(REPO_ROOT / "results"))
    parser.add_argument("--out-svg", default=str(REPORT_DIR / "hyperbus_bw.svg"))
    parser.add_argument("--out-png", default=str(REPORT_DIR / "hyperbus_bw.png"))
    args = parser.parse_args(argv)

    points = load_points(Path(args.results_dir))
    out_svg = Path(args.out_svg)
    out_png = Path(args.out_png)

    try:
        plot_matplotlib(points, out_svg, out_png)
        print(f"wrote {out_svg} and {out_png} (matplotlib)")
    except ImportError:
        plot_svg_fallback(points, out_svg)
        print(f"wrote {out_svg} (hand-written SVG fallback; matplotlib not available)")
        if out_png.exists():
            out_png.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
