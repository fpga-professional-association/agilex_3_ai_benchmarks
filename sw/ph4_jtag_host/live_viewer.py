#!/usr/bin/env python3
"""Live viewer: watch CIFAR-10 images being sent over JTAG and classified.

Companion to tools/run_cifar10_jtag.py.  That runner appends one JSON record
per classified image to <run-dir>/results.jsonl (flushed after every batch);
this viewer tails that file and renders each record as it lands: the 32x32
CIFAR-10 image blown up, the JTAG transfer that carried it (16,384 B and the
measured microseconds of the input write), the ten FP16 logits the DLA
returned, the predicted vs true class, the device-time latency, and a running
top-1 score with a filmstrip of recent frames.

The viewer only READS the run directory.  It never touches the JTAG link, the
backend, or the measurement path, so it cannot perturb a benchmark in
progress -- the numbers in results.jsonl are exactly what the runner would
have recorded with no viewer attached.

Live, alongside a hardware run (start the runner in another terminal):

  python tools/run_cifar10_jtag.py --backend syscon --images 200 \
         --run-dir build/fpga_ai/bench/demo1
  python tools/live_viewer.py --run-dir build/fpga_ai/bench/demo1

Replay a finished run (e.g. the committed full-10k hardware campaign) at a
chosen rate, as if watching it live:

  python tools/live_viewer.py --run-dir build/fpga_ai/bench/tier2_full10k \
         --replay 20

No dependencies beyond the runner's own (numpy) and Tkinter from the standard
library; images are rendered by building PPM bytes for tk.PhotoImage, so no
imaging package is needed.  `--smoke` exercises the full decode/render path
headless (no window) for CI and remote checks.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np

# Same-directory import: dataset loader + geometry stay single-sourced.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from run_cifar10_jtag import INPUT_BYTES, load_cifar10_test  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent

CLASS_NAMES = ["airplane", "automobile", "bird", "cat", "deer",
               "dog", "frog", "horse", "ship", "truck"]

SCALE = 9            # 32 px -> 288 px main image
STRIP_SCALE = 2      # 32 px -> 64 px filmstrip thumbnails
STRIP_LEN = 10

OK = "#2e7d32"       # green
BAD = "#c62828"      # red
BG = "#141414"
FG = "#e0e0e0"
DIM = "#8a8a8a"
BAR = "#4a90d9"
BAR_HIT = "#69b34c"
BAR_MISS = "#d94a4a"


def ppm_bytes(image: np.ndarray) -> bytes:
    """uint8 HxWx3 -> binary PPM (P6), which tk.PhotoImage accepts natively."""
    h, w, _ = image.shape
    return b"P6 %d %d 255 " % (w, h) + image.tobytes()


class RecordSource:
    """Iterator over results.jsonl that survives the file growing under it."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self._pos = 0

    def poll(self) -> list[dict]:
        """Return complete records appended since the last call."""
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as fh:
            fh.seek(self._pos)
            while True:
                line_start = fh.tell()
                line = fh.readline()
                if not line:
                    break
                if not line.endswith("\n"):        # torn tail of a mid-flush write
                    self._pos = line_start
                    return out
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue                        # damaged line; skip it
            self._pos = fh.tell()
        return out


class Stats:
    def __init__(self):
        self.n = 0
        self.correct = 0
        self.stale = 0
        self.lat_ns = []          # device-time latencies (may be absent)
        self.wr_us = []           # JTAG input-write times

    def add(self, rec: dict) -> None:
        self.n += 1
        if rec.get("stale"):
            self.stale += 1
        elif rec.get("ok"):
            self.correct += 1
        lat = (rec.get("fpga") or {}).get("latency_ns")
        if lat:
            self.lat_ns.append(lat)
        wr = (rec.get("host_us") or {}).get("input_write")
        if wr:
            self.wr_us.append(wr)

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else 0.0


def format_panel(rec: dict, stats: Stats) -> dict:
    """Everything the UI (or --smoke) needs, as plain strings/numbers."""
    idx = int(rec["i"])
    pred, label = int(rec["pred"]), int(rec["label"])
    pred_name = CLASS_NAMES[pred] if 0 <= pred < 10 else "stale"
    label_name = CLASS_NAMES[label]
    wr_us = (rec.get("host_us") or {}).get("input_write", 0)
    kbps = INPUT_BYTES / wr_us * 1e6 / 1024 if wr_us else 0.0
    lat = (rec.get("fpga") or {}).get("latency_ns")
    return {
        "idx": idx,
        "ok": bool(rec.get("ok")),
        "pred_name": pred_name,
        "label_name": label_name,
        "jtag": f"sent {INPUT_BYTES:,} B over JTAG in {wr_us/1000:.1f} ms"
                f"  ({kbps:.0f} KiB/s)" if wr_us else "JTAG write time n/a",
        "latency": f"DLA device time {lat/1000:.1f} µs"
                   f"  ({1e9/lat:,.0f} fps engine rate)" if lat else
                   "device latency not measured",
        "score": f"{stats.correct}/{stats.n} correct"
                 f"  ({stats.accuracy:.2%} top-1)"
                 + (f"  · {stats.stale} stale" if stats.stale else ""),
        "logits": rec.get("logits") or [],
    }


class ViewerApp:
    def __init__(self, images: np.ndarray, source: RecordSource,
                 replay_fps: float | None):
        import tkinter as tk

        self.tk = tk
        self.images = images
        self.source = source
        self.replay_fps = replay_fps
        self.backlog: list[dict] = []
        self.stats = Stats()
        self.strip: list[tuple[int, bool]] = []

        self.root = tk.Tk()
        self.root.title("ResNet-8 on AXC3000 — live JTAG classification")
        self.root.configure(bg=BG)

        left = tk.Frame(self.root, bg=BG)
        left.grid(row=0, column=0, padx=12, pady=12, sticky="n")
        self.canvas = tk.Canvas(left, width=32 * SCALE + 8, height=32 * SCALE + 8,
                                bg=BG, highlightthickness=0)
        self.canvas.pack()
        self.jtag_lbl = tk.Label(left, fg=DIM, bg=BG, font=("Consolas", 10))
        self.jtag_lbl.pack(anchor="w", pady=(6, 0))
        self.lat_lbl = tk.Label(left, fg=DIM, bg=BG, font=("Consolas", 10))
        self.lat_lbl.pack(anchor="w")

        right = tk.Frame(self.root, bg=BG)
        right.grid(row=0, column=1, padx=(0, 12), pady=12, sticky="n")
        self.pred_lbl = tk.Label(right, fg=FG, bg=BG, font=("Segoe UI", 22, "bold"))
        self.pred_lbl.pack(anchor="w")
        self.truth_lbl = tk.Label(right, fg=DIM, bg=BG, font=("Segoe UI", 12))
        self.truth_lbl.pack(anchor="w")
        self.bars = tk.Canvas(right, width=340, height=10 * 24 + 6, bg=BG,
                              highlightthickness=0)
        self.bars.pack(pady=(10, 0))
        self.score_lbl = tk.Label(right, fg=FG, bg=BG, font=("Consolas", 12))
        self.score_lbl.pack(anchor="w", pady=(10, 0))

        strip_w = STRIP_LEN * (32 * STRIP_SCALE + 8)
        self.strip_canvas = tk.Canvas(self.root, width=strip_w,
                                      height=32 * STRIP_SCALE + 10, bg=BG,
                                      highlightthickness=0)
        self.strip_canvas.grid(row=1, column=0, columnspan=2,
                               padx=12, pady=(0, 12), sticky="w")
        self._photo_refs: list = []   # PhotoImages die without a Python ref

    def _photo(self, idx: int, scale: int):
        img = self.tk.PhotoImage(data=ppm_bytes(self.images[idx]))
        return img.zoom(scale, scale)

    def show(self, rec: dict) -> None:
        panel = format_panel(rec, self.stats)
        idx = panel["idx"]
        color = OK if panel["ok"] else BAD

        self._photo_refs = self._photo_refs[-(STRIP_LEN + 1):]
        main = self._photo(idx, SCALE)
        self._photo_refs.append(main)
        c = self.canvas
        c.delete("all")
        c.create_rectangle(1, 1, 32 * SCALE + 7, 32 * SCALE + 7,
                           outline=color, width=3)
        c.create_image(4, 4, image=main, anchor="nw")

        mark = "✓" if panel["ok"] else "✗"
        self.pred_lbl.config(text=f"{mark} {panel['pred_name']}", fg=color)
        self.truth_lbl.config(
            text=f"truth: {panel['label_name']}   ·   test image #{idx}")
        self.jtag_lbl.config(text=panel["jtag"])
        self.lat_lbl.config(text=panel["latency"])
        self.score_lbl.config(text=panel["score"])

        b = self.bars
        b.delete("all")
        logits = panel["logits"]
        if logits:
            lo, hi = min(logits), max(logits)
            span = (hi - lo) or 1.0
            top = int(np.argmax(logits))
            for k, v in enumerate(logits):
                y = k * 24 + 4
                w = 6 + 200 * (v - lo) / span
                fill = (BAR_HIT if panel["ok"] else BAR_MISS) if k == top else BAR
                b.create_rectangle(90, y, 90 + w, y + 16, fill=fill, width=0)
                b.create_text(86, y + 8, text=CLASS_NAMES[k], fill=FG,
                              anchor="e", font=("Consolas", 9))
                b.create_text(96 + w, y + 8, text=f"{v:+.2f}", fill=DIM,
                              anchor="w", font=("Consolas", 8))

        self.strip.append((idx, panel["ok"]))
        self.strip = self.strip[-STRIP_LEN:]
        s = self.strip_canvas
        s.delete("all")
        for k, (si, sok) in enumerate(self.strip):
            x = k * (32 * STRIP_SCALE + 8) + 4
            thumb = self._photo(si, STRIP_SCALE)
            self._photo_refs.append(thumb)
            s.create_rectangle(x - 2, 2, x + 32 * STRIP_SCALE + 2,
                               32 * STRIP_SCALE + 6,
                               outline=OK if sok else BAD, width=2)
            s.create_image(x, 4, image=thumb, anchor="nw")

    def tick(self) -> None:
        self.backlog.extend(self.source.poll())
        if self.backlog:
            if self.replay_fps:                      # animate one per frame
                rec = self.backlog.pop(0)
                self.stats.add(rec)
                self.show(rec)
            else:                                    # live: jump to newest
                for rec in self.backlog:
                    self.stats.add(rec)
                self.show(self.backlog[-1])
                self.backlog.clear()
        delay = int(1000 / self.replay_fps) if self.replay_fps else 200
        self.root.after(delay, self.tick)

    def run(self) -> None:
        self.tick()
        self.root.mainloop()


def smoke(images: np.ndarray, source: RecordSource, limit: int) -> int:
    """Headless pass over the records: same decode + PPM path, no window."""
    stats = Stats()
    shown = 0
    for rec in source.poll():
        stats.add(rec)
        panel = format_panel(rec, stats)
        ppm = ppm_bytes(images[panel["idx"]])
        assert ppm.startswith(b"P6") and len(ppm) > 32 * 32 * 3
        if shown < limit:
            print(f"#{panel['idx']:5d}  {'OK ' if panel['ok'] else 'MISS'}"
                  f"  pred={panel['pred_name']:<10s} truth={panel['label_name']:<10s}"
                  f"  {panel['jtag']}  {panel['latency']}")
        shown += 1
    print(f"smoke: {shown} records rendered headless; {stats.correct}/{stats.n} "
          f"correct ({stats.accuracy:.2%} top-1)")
    return 0 if shown else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-dir", type=pathlib.Path, required=True,
                    help="runner --run-dir to watch (reads results.jsonl)")
    ap.add_argument("--dataset", type=pathlib.Path,
                    default=REPO / "build/fpga_ai/cifar10/cifar-10-python.tar.gz")
    ap.add_argument("--replay", type=float, default=None, metavar="FPS",
                    help="animate existing records at FPS instead of jumping "
                         "to the newest (also keeps tailing afterwards)")
    ap.add_argument("--smoke", action="store_true",
                    help="headless self-check: decode and rasterize every "
                         "record currently in the file, no window")
    ap.add_argument("--smoke-limit", type=int, default=10,
                    help="rows of per-image detail --smoke prints")
    args = ap.parse_args(argv)

    results = args.run_dir / "results.jsonl"
    if args.smoke and not results.exists():
        print(f"no {results}", file=sys.stderr)
        return 1

    images, _labels = load_cifar10_test(args.dataset)
    source = RecordSource(results)

    if args.smoke:
        return smoke(images, source, args.smoke_limit)

    app = ViewerApp(images, source, args.replay)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
