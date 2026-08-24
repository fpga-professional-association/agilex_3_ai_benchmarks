#!/usr/bin/env python3
"""Host-PC CIFAR-10 benchmark runner for the AXC3000 CoreDLA ResNet-8 design.

SKELETON -- no hardware has been touched by this file yet.  Every device access
goes through a Backend; the default backend is `sim`, which fabricates plausible
responses so the whole pipeline (packing, batching, budget accounting, resume,
scoring, reporting) can be exercised offline.  Switch to `--backend syscon` only
once the JTAG design agent has landed a JTAG-to-Avalon master and filled in the
address map (every such hole is marked TODO(JTAG-MAP)).

  python tools/run_cifar10_jtag.py --backend sim --images 200 --run-dir <dir>
  python tools/run_cifar10_jtag.py --backend syscon --images 10000 --run-dir <dir>
  python tools/run_cifar10_jtag.py --report-only --run-dir <dir>

=============================================================================
ARCHITECTURE
=============================================================================
  run_cifar10_jtag.py (this file, CPython 3.14 + NumPy)
    |  dataset load, FP16/Cvec input packing, budget accounting, resume,
    |  scoring, reporting.  Owns ALL arithmetic.
    |
    +-- Programmer            quartus_pgm.exe -c "USB Blaster III" -o "p;<sof>"
    |
    +-- Backend (one per programming cycle)
          syscon: subprocess system-console.exe --cli --script=jtag_worker.tcl
                  driven over stdin/stdout with the line protocol documented in
                  tools/jtag_worker.tcl
          sim:    in-process fake, for offline development

Per programming cycle:
    close backend -> quartus_pgm SOF -> launch backend -> OPEN master ->
    identity/param checks -> CSRINIT -> warm-up job -> N image batches ->
    (budget exhausted or diagnostics bit 2) -> repeat

=============================================================================
THE TWO CLOCKS -- DO NOT MIX THEM
=============================================================================
Two independent time bases are recorded per image and are NEVER combined:

  record["fpga"]  device-clocked counter values, sampled by the worker
                  immediately before and immediately after the queueing write.
                  This is the only admissible source of an FPGA latency claim.
                  Converted to nanoseconds solely by the declared counter domain
                  frequency.  If no usable counter is configured, latency_ns is
                  None and the report says "not measured" rather than guessing.

  record["host_us"]  wall-clock microseconds around JTAG traffic, measured by
                  the worker (Tcl `clock microseconds`).  Transport cost only.
                  `poll_wait` here is NOT inference latency: one poll is a JTAG
                  round trip of roughly the same duration as the inference, so
                  it can only bound latency from above, and loosely.

Reporter refuses to produce a single "latency" number that draws on both.

=============================================================================
EVALUATION LIMIT
=============================================================================
The unlicensed CoreDLA IP stops after a fixed number of VALID inferences per
device programming and raises descriptor-diagnostics bit 2.  This project's
install documents 10,000 (see MEMORY: the 100,000 figure in the handbook is a
different version).  The counter is in silicon and counts EVERY job queued since
programming -- warm-ups, self-tests and discarded jobs included -- and only a
reprogramming clears it.  So:

  * `jobs_since_program` increments on every write of CSR 536, no exceptions;
  * a batch is truncated so it can never straddle the reprogram threshold;
  * diagnostics bit 2 on any job invalidates THAT job: its result is discarded,
    the observed limit is recorded, the device is reprogrammed and the image is
    retried.  The runner thereby measures the true limit instead of trusting it.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import queue
import random
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from dataclasses import dataclass, field

try:
    import numpy as np
except ImportError:  # pragma: no cover
    sys.exit("numpy is required (it is available on this machine; OpenVINO is not)")


REPO = pathlib.Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# Geometry of this graph and this pseudo-DDR.  Mirrors
# fpga/axc3000_mlperf/software/fpga_ai_resnet8/main.c -- keep in sync.
# --------------------------------------------------------------------------
IMAGE_H, IMAGE_W, IMAGE_C = 32, 32, 3
INPUT_CVEC = 8                      # channel-vector padding of the PE array
INPUT_BYTES = IMAGE_H * IMAGE_W * INPUT_CVEC * 2      # 16384
OUTPUT_HALVES = 16                  # 10 logits + Cvec padding


def set_input_cvec(cvec: int) -> None:
    """Re-point the module-level input geometry at a different c_vector.

    The arch's c_vector sets the compiler's input tensor layout
    (element = (h*W + w)*Cvec + c) and therefore the size of the input slave.
    Phase 12 ran k16/c8 -> Cvec 8 -> 16,384 B; phase 13's k16/c16 build needs
    Cvec 16 -> 32,768 B.  Everything downstream reads these two globals, so
    one call at start-up is enough; it is deliberately NOT a per-call argument
    because a half-converted run would silently pack garbage.
    """
    global INPUT_CVEC, INPUT_BYTES
    if cvec < IMAGE_C:
        raise SystemExit(f"input_cvec {cvec} cannot hold {IMAGE_C} channels")
    INPUT_CVEC = cvec
    INPUT_BYTES = IMAGE_H * IMAGE_W * cvec * 2
NUM_CLASSES = 10
CIFAR10_CLASSES = ["airplane", "automobile", "bird", "cat", "deer",
                   "dog", "frog", "horse", "ship", "truck"]


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
@dataclass
class Config:
    # --- tool paths -------------------------------------------------------
    quartus_pgm: str = r"C:\altera_pro\26.1\quartus\bin64\quartus_pgm.exe"
    system_console: str = r"C:\altera_pro\26.1\syscon\bin\system-console.exe"
    # Launch flags, as data so the plumbing can be integration-tested against a
    # plain tclsh (which takes the script as a bare argument) without touching
    # code.  {script} is substituted with the worker path.
    system_console_args: list[str] = field(
        default_factory=lambda: ["--cli", "--script={script}"])
    cable: str = "USB Blaster III"
    sof: str = str(REPO / "fpga/axc3000_mlperf/output_files/axc3000_top.sof")

    # --- address map ------------------------------------------------------
    # RESOLVED on hardware (Phase 12, Nios-less k16c8 build, 2026-08-21).
    # csr_base/mem_base/param_base are HOST space, as seen from the
    # altera_jtag_avalon_master instance "jtag_master".  io_base/config_base/
    # intermediate_base are DLA space (the IP dereferences them through its own
    # ddr_axi port) and do NOT change when the host map changes.
    #   invariant: host_address = DLA_address + 0x00100000
    #
    # Host map:
    #   0x00040000..0x000407FF  fpga_ai.csr_axi   2 KiB
    #   0x00100000..0x0011FFFF  dla_par0        128 KiB   param bytes      0..131071
    #   0x00120000..0x0012FFFF  dla_par1         64 KiB   param bytes 131072..196607
    #   0x00130000..0x00133FFF  dla_par2         16 KiB   param bytes 196608..205055
    #   0x00140000..0x00143FFF  dla_io0          16 KiB   input tensor (16384 B)
    #   0x00144000..0x00144FFF  dla_io1           4 KiB   output tensor (512 B at +0)
    # dla_io0 and dla_io1 are contiguous, so mem_base + output_off (16384)
    # lands exactly on the output slave -- one base covers both.
    csr_base: int = 0x00040000
    mem_base: int = 0x00140000          # dla_io0; output slave follows at +16384
    param_base: int = 0x00100000        # dla_par0..2 are contiguous from here
    io_base: int = 0x00040000           # DLA-space, written to CSR 536
    config_base: int = 0x0
    config_range_minus_two: int = 2430  # 19456 / 8 - 2   (64-bit reader words)
    intermediate_base: int = 0x00033000  # interBuffer is 0 B; parked in par2 tail
    input_off: int = 0
    output_off: int = 16384
    param_bytes: int = 205056
    param_fnv1a: int = 0x191007B5       # from mem/image_info.txt; 0 = skip
    # c_vector of the built arch.  Sets the input tensor layout
    # (element = (h*32 + w)*input_cvec + c, FP16) and hence input_off..
    # input_off + 32*32*input_cvec*2.  MUST match the arch the SOF was built
    # from: a mismatch is silent -- the DLA reads a correctly-sized buffer with
    # the channels interleaved wrong and returns plausible, wrong logits.
    input_cvec: int = 8

    # --- latency counters -------------------------------------------------
    # RESOLVED: CSR 576/580 CLOCKS_ACTIVE is a 64-bit counter GATED ON
    # jobs_active, clocked by ddr_clk (iopll_0.outclk0 = 100.000 MHz exactly),
    # so one tick = 10 ns and the before/after delta across a queue+complete is
    # the device-side job latency -- not a free-running wall clock.  Measured on
    # hardware: ~52,200 ticks = ~522 us per ResNet-8 inference, spread < 1%.
    # Only the low word is sampled: a job is ~5.2e4 ticks, so the 32-bit low
    # half wraps after ~82,000 jobs and the &0xFFFFFFFF delta in score() is
    # correct regardless.
    # IF ddr_clk IS EVER RETUNED (the build notes float 125 MHz) THE 100e6 HERE
    # MUST MOVE WITH IT -- the tick is not self-describing.
    # Each entry: {"name", "addr" (host-space), "hz", "kind": free_running|latch}
    latency_counters: list[dict] = field(default_factory=lambda: [
        {"name": "clocks_active", "addr": 0x00040240, "hz": 100_000_000,
         "kind": "free_running", "primary": True},
    ])

    # --- run policy -------------------------------------------------------
    eval_budget: int = 10000            # valid inferences per programming
    reprogram_at: int = 9500            # queue no more than this per cycle
    batch_size: int = 64                # images per host<->worker round trip
    job_timeout_ms: int = 5000
    warmup_jobs: int = 1                # counts against the budget
    # The warm-up is a REAL inference of a known stimulus with a known answer,
    # run once per programming cycle before any scored image.  It is the only
    # thing that catches a bad programming cycle (stale bitstream, half-loaded
    # parameters, a CSR init that did not take) at the moment it happens rather
    # than after several thousand quietly-wrong results.
    # Stimulus: tools/make_synthetic_input.py, which reproduces main.c's
    # initialize_synthetic_image() LCG exactly.  Expected argmax comes from the
    # independent integer evaluator tools/resnet8_golden.py (class 6, frog) and
    # was confirmed on hardware over 20 jobs / 3 sessions / 2 programmings.
    warmup_image: str = str(
        REPO / "build/fpga_ai/cifar10/dla_k16c8_synthetic/synthetic_fp16_cvec8.bin")
    warmup_expect_class: int = 6        # <0 disables the assertion
    max_retries_per_image: int = 3      # then give up on that image, not the run
    max_programmings: int = 64          # hard stop; a 10k run needs 2
    poison_output: bool = True
    verify_params: bool = True          # 200 KiB readback; ~0.14 s per cycle
    # CAL writes at mem_base+0, i.e. into the 16 KiB input slave.  Asking for
    # more than INPUT_BYTES walks off dla_io0 into the output slave and then
    # into undecoded space, so this is capped at one input tensor by design.
    calibrate_bytes: int = INPUT_BYTES  # 0 disables the throughput probe

    @classmethod
    def load(cls, path: pathlib.Path | None) -> "Config":
        cfg = cls()
        if path is None:
            return cfg
        raw = json.loads(path.read_text())
        for k, v in raw.items():
            # JSON has no comments and these files carry the address-map
            # reasoning that makes them auditable, so a leading underscore
            # marks a key as documentation and is skipped.  Everything else is
            # still rejected: a typo'd key must never be silently ignored.
            if k.startswith("_"):
                continue
            if not hasattr(cfg, k):
                raise SystemExit(f"unknown config key {k!r} in {path}")
            cur = getattr(cfg, k)
            if isinstance(cur, int) and not isinstance(cur, bool) and isinstance(v, str):
                v = int(v, 0)
            setattr(cfg, k, v)
        return cfg


# --------------------------------------------------------------------------
# Dataset + input packing
# --------------------------------------------------------------------------
def load_cifar10_test(path: pathlib.Path) -> tuple[np.ndarray, np.ndarray]:
    """Return (images uint8 [N,32,32,3] NHWC, labels uint8 [N]).

    Accepts either the python tarball or a pre-extracted `test_batch`, or an
    .npz with `images`/`labels`.  The tarball is a pickle, so it is unpickled
    with a restricted loader -- it is downloaded data, not trusted input.
    """
    if path.suffix == ".npz":
        z = np.load(path)
        return z["images"], z["labels"]

    import pickle

    class _Restricted(pickle.Unpickler):
        def find_class(self, module, name):
            if (module, name) in {("numpy", "dtype"), ("numpy", "ndarray"),
                                  ("numpy.core.multiarray", "_reconstruct"),
                                  ("numpy._core.multiarray", "_reconstruct")}:
                return super().find_class(module, name)
            raise pickle.UnpicklingError(f"blocked global {module}.{name}")

    def _decode(fobj):
        d = _Restricted(fobj, encoding="bytes").load()
        flat = np.asarray(d[b"data"], dtype=np.uint8)          # [N, 3072] NCHW
        labels = np.asarray(d[b"labels"], dtype=np.uint8)
        images = flat.reshape(-1, IMAGE_C, IMAGE_H, IMAGE_W).transpose(0, 2, 3, 1)
        return np.ascontiguousarray(images), labels

    if path.is_dir():
        with open(path / "test_batch", "rb") as fh:
            return _decode(fh)
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path) as tf:
            member = next(m for m in tf.getmembers() if m.name.endswith("test_batch"))
            fh = tf.extractfile(member)
            assert fh is not None
            return _decode(fh)
    with open(path, "rb") as fh:
        return _decode(fh)


def pack_input(image: np.ndarray) -> bytes:
    """uint8 NHWC [32,32,3] -> the 16 KiB FP16 Cvec-padded tensor dla_io wants.

    Layout is the compiler's: pixel-major, INPUT_CVEC (8) contiguous halves per
    pixel, channels 0..2 carrying R,G,B and 3..7 zero.  Values are the raw
    0..255 integers converted to binary16 -- exact for every value in range, and
    the graph's leading FakeQuantize applies the int8 offset itself.  This is
    exactly what uint8_to_half()/ddr_write_input() do in main.c.

    CONSOLIDATION NOTE: tools/cifar10_to_dla.py (written in parallel by the
    dataset agent) derives this same layout from the compiler's own
    input_transform_mapping CSV rather than from main.c, and the two agree
    element for element -- (h*W+w)*CVEC+c, FP16, 16384 B, channels 3..7 zero.
    That file is the more authoritative source; prefer folding this function
    into it (InputPack would call it) rather than maintaining two copies.

    NOTE, deliberately opposite to the firmware: main.c skips the always-zero
    padding channels because the Nios V pays per store.  Over JTAG the cost is
    per *transfer*, not per byte, so the host writes one contiguous 16 KiB block
    including the zeros.  Splitting it into 1024 six-byte writes would be
    catastrophically slower.
    """
    if image.shape != (IMAGE_H, IMAGE_W, IMAGE_C):
        raise ValueError(f"expected [{IMAGE_H},{IMAGE_W},{IMAGE_C}], got {image.shape}")
    buf = np.zeros((IMAGE_H * IMAGE_W, INPUT_CVEC), dtype=np.float16)
    buf[:, :IMAGE_C] = image.reshape(-1, IMAGE_C).astype(np.float16)
    out = buf.tobytes()
    assert len(out) == INPUT_BYTES, len(out)
    return out


def parse_halves(hexstr: str) -> np.ndarray:
    """`out=` payload -> uint16 array.

    CONTRACT WITH jtag_worker.tcl: the worker emits one 4-hex-digit group per
    FP16 word, each group being the NUMERIC value of that 16-bit word as
    master_read_16 returned it (Tcl `format %04x`).  It is deliberately NOT a
    byte stream, so there is no byte-order question to get wrong on either
    side -- do not "simplify" this into bytes.fromhex()/frombuffer('<f2'),
    which reads each half byte-swapped and silently yields garbage logits.
    """
    if len(hexstr) % 4:
        raise ValueError(f"output payload not a whole number of halves: {hexstr!r}")
    return np.array([int(hexstr[i:i + 4], 16) for i in range(0, len(hexstr), 4)],
                    dtype=np.uint16)


def decode_logits(hexstr: str) -> list[float]:
    """The first NUM_CLASSES FP16 logits, reinterpreted from their bit patterns."""
    halves = parse_halves(hexstr)
    return [float(x) for x in halves.view(np.float16)[:NUM_CLASSES]]


def is_poison(hexstr: str, poison_word: int = 0x5A5A5A5A) -> bool:
    """True if the output window still holds the pre-job sentinel.

    The sentinel is written as 32-bit words and read back as 16-bit halves, so
    each half carries the low/high pattern of `poison_word`.
    """
    halves = parse_halves(hexstr)
    if halves.size < 2:
        return False
    lo, hi = poison_word & 0xFFFF, (poison_word >> 16) & 0xFFFF
    return int(halves[0]) == lo and int(halves[1]) == hi


class InputPack:
    """Materialises every image as its own 16 KiB file, once.

    Per-image files exist because the only high-throughput System Console write
    is `master_write_from_file`.  10,000 files x 16 KiB = 164 MiB, written in
    seconds by NumPy and reused across reprogramming cycles and resumes.
    """

    def __init__(self, run_dir: pathlib.Path):
        self.dir = run_dir / "inputs"
        self.manifest = run_dir / "manifest.txt"

    def build(self, images: np.ndarray, force: bool = False) -> pathlib.Path:
        n = len(images)
        if self.manifest.exists() and not force:
            existing = self.manifest.read_text().strip().splitlines()
            if len(existing) == n:
                return self.manifest
        self.dir.mkdir(parents=True, exist_ok=True)
        lines = []
        for i in range(n):
            p = self.dir / f"{i:06d}.bin"
            p.write_bytes(pack_input(images[i]))
            lines.append(f"{i} {p.as_posix()}")
        self.manifest.write_text("\n".join(lines) + "\n")
        return self.manifest


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------
class WorkerError(RuntimeError):
    """Worker-side failure.

    `res` carries any RES lines the worker had already emitted before the
    failure.  That matters for BATCH: the worker reports the offending job as a
    RES with err=... and only THEN sends ERR batch_aborted, so the record that
    explains the abort (eval_limit vs timeout, and on which image) would
    otherwise be destroyed by the exception.
    """

    def __init__(self, message: str, res: list[str] | None = None):
        super().__init__(message)
        self.res = res or []


FNV1A_OFFSET = 0x811C9DC5
FNV1A_PRIME = 0x01000193


def fnv1a(data: bytes) -> int:
    h = FNV1A_OFFSET
    for b in data:
        h = ((h ^ b) * FNV1A_PRIME) & 0xFFFFFFFF
    return h


class Backend:
    """Device access. One instance per programming cycle."""

    # RBLK against real RAM only means anything on a real cable.
    supports_readback: bool = False

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def command(self, line: str, collect_res: bool = False) -> tuple[str, list[str]]:
        raise NotImplementedError

    # convenience wrappers -------------------------------------------------
    def configure(self, **kv) -> None:
        self.command("CFG " + " ".join(f"{k}={v}" for k, v in kv.items()))

    def open_master(self) -> str:
        return self.command("OPEN auto")[0]

    def csrinit(self) -> dict[str, str]:
        ok, _ = self.command("CSRINIT")
        return dict(tok.split("=", 1) for tok in ok.split() if "=" in tok)

    def batch(self, manifest: pathlib.Path, first: int, count: int,
              timeout_ms: int) -> tuple[list[dict], str | None]:
        # A worker-side abort (eval limit, job timeout, Tcl exception) is an
        # EXPECTED outcome that the Runner handles by reprogramming and
        # retrying -- it must not escape as an exception and kill a 10,000
        # image campaign.  jtag_worker.tcl signals it with `ERR batch_aborted`,
        # which SysconBackend.command raises; catch it here and convert it to
        # the same (records, "batch_aborted") shape SimBackend returns.
        try:
            ok, res = self.command(
                f"BATCH {manifest.as_posix()} {first} {count} {timeout_ms}",
                collect_res=True)
        except WorkerError as exc:
            if "batch_aborted" not in str(exc):
                raise            # transport death, not a job-level fault
            ok, res = None, exc.res
        records = [dict(tok.split("=", 1) for tok in line.split() if "=" in tok)
                   for line in res]
        return records, (None if ok is not None else "batch_aborted")


class SysconBackend(Backend):
    """system-console.exe --cli --script=jtag_worker.tcl, driven over stdin.

    CONFIRMED on 26.1 build 110: this System Console does serve stdin in
    --cli --script mode.  Two Windows-specific warts had to be worked around,
    both handled here rather than in the worker:

    1. PROMPT BLEED.  Even in --script mode the console's own Tcl shell is
       alive and prints its "% " prompt with no trailing newline, so it fuses
       onto the front of the next line the worker writes -- the very first
       response arrives as "% READY jtag_worker/0.1".  _readline strips any
       leading run of prompts.  Without this the READY handshake times out.

    2. STDIN THEFT.  The console shell and the sourced worker are both readers
       of the same stdin channel.  Whenever the worker is busy in a long
       command (JTAG discovery, a 16 KiB block write) the shell gets scheduled
       and swallows exactly ONE character of whatever arrives next, so the
       worker's next `gets` returns e.g. "ING" instead of "PING".  Measured:
       harmless-looking in isolation, fatal in a batch (a stolen "B" turns
       BATCH into an unknown command and the run desyncs).
       Every command is therefore sent as "\\n" + line + "\\n".  The thief eats
       the guard newline, which costs nothing; if it does not steal, the worker
       reads one empty line, which its dispatcher already treats as a no-op.
       Either way exactly one command and one response cross the pipe.

    3. Loading the remote-channel plugin shells out to `ps`, which does not
       exist on Windows.  The resulting IOException tears down System Console's
       executor thread pool and every later get_service_paths fails with
       "Task ... rejected from ... [Terminated]".  _env() puts Git-for-Windows'
       usr\\bin (which ships ps.exe) on PATH when nothing else provides one.
    """

    supports_readback = True

    READY_TIMEOUT_S = 90.0     # System Console start-up is genuinely ~10-30 s

    # See wart 1 above.  A line may carry several fused prompts.
    _PROMPT_RE = re.compile(r"^(?:%\s*)+")

    def __init__(self, cfg: Config, verbose: bool = False):
        self.cfg = cfg
        self.verbose = verbose
        self.proc: subprocess.Popen | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._pump: threading.Thread | None = None

    @staticmethod
    def _env() -> dict:
        """PATH with a `ps` on it -- see wart 3."""
        env = dict(os.environ)
        if shutil.which("ps") is None:
            for cand in (r"C:\Program Files\Git\usr\bin",
                         r"C:\Program Files (x86)\Git\usr\bin"):
                if os.path.exists(os.path.join(cand, "ps.exe")):
                    # appended, not prepended: that directory also shadows
                    # find/sort/etc. and System Console must keep the Windows ones
                    env["PATH"] = env.get("PATH", "") + os.pathsep + cand
                    break
        return env

    def start(self) -> None:
        worker = REPO / "tools" / "jtag_worker.tcl"
        cmd = [self.cfg.system_console] + [
            a.format(script=worker.as_posix()) for a in self.cfg.system_console_args]
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
            encoding="utf-8", errors="replace", env=self._env())
        self._pump = threading.Thread(target=self._pump_stdout, daemon=True)
        self._pump.start()

        deadline = time.monotonic() + self.READY_TIMEOUT_S
        while time.monotonic() < deadline:
            line = self._readline(timeout=deadline - time.monotonic())
            if line is None:
                break
            if line.startswith("READY"):
                return
        raise WorkerError("system-console never announced READY")

    def _pump_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self._lines.put(line.rstrip("\r\n"))
        self._lines.put(None)

    def _readline(self, timeout: float) -> str | None:
        try:
            line = self._lines.get(timeout=max(0.05, timeout))
        except queue.Empty:
            return None
        if line is not None:
            line = self._PROMPT_RE.sub("", line)     # wart 1
            if self.verbose:
                print(f"  <- {line}", file=sys.stderr)
        return line

    def command(self, line: str, collect_res: bool = False,
                timeout: float = 600.0) -> tuple[str, list[str]]:
        assert self.proc and self.proc.stdin
        if self.verbose:
            print(f"  -> {line}", file=sys.stderr)
        # Leading guard newline absorbs the console shell's one-character
        # theft -- see wart 2 in the class docstring.  Do not "tidy" it away.
        self.proc.stdin.write("\n" + line + "\n")
        self.proc.stdin.flush()

        res: list[str] = []
        deadline = time.monotonic() + timeout
        while True:
            out = self._readline(timeout=deadline - time.monotonic())
            if out is None:
                raise WorkerError(f"worker died or timed out during: {line}")
            if out.startswith("RES "):
                res.append(out[4:])
            elif out.startswith("OK"):
                return out[3:].strip(), res
            elif out.startswith("ERR "):
                # Carry the RES lines collected so far -- see WorkerError.
                raise WorkerError(f"{out[4:]} (during: {line})", res)
            elif out.startswith("LOG "):
                print(f"[worker] {out[4:]}", file=sys.stderr)
            # anything else is System Console chatter; ignore

    def stop(self) -> None:
        if not self.proc:
            return
        try:
            self.command("QUIT", timeout=15.0)
        except Exception:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None


class SimBackend(Backend):
    """Offline stand-in.  Lets the full pipeline run with no board and no cable.

    It is deliberately *pessimistic-shaped* rather than perfect: it models the
    eval-limit stop and a configurable random timeout so the failure paths are
    exercised in development instead of first meeting hardware at image 7,000.
    """

    def __init__(self, cfg: Config, labels: np.ndarray, accuracy: float = 0.85,
                 eval_limit: int | None = None, fault_rate: float = 0.0,
                 seed: int = 1):
        self.cfg = cfg
        self.labels = labels
        self.accuracy = accuracy
        self.eval_limit = eval_limit if eval_limit is not None else cfg.eval_budget
        self.fault_rate = fault_rate
        self.rng = random.Random(seed)
        self.jobs = 0
        self.compl = 0
        self.counter = 0

    def start(self) -> None:
        self.jobs = 0
        self.compl = 0

    def stop(self) -> None:
        pass

    def command(self, line: str, collect_res: bool = False,
                **_) -> tuple[str, list[str]]:
        verb, *rest = line.split()
        if verb == "BATCH":
            return self._batch(rest)
        if verb == "CSRINIT":
            return ("config_base=0x00000000 config_range=2430 diag=0x00000000 "
                    "completion=0 license=0x00000000"), []
        if verb == "CAL":
            n = int(rest[0])
            return f"wr_us={n * 4} rd_us={n * 5} bytes={n}", []
        if verb == "OPEN":
            return "/devices/sim/(link)/master", []
        return "", []

    def _batch(self, rest: list[str]) -> tuple[str, list[str]]:
        manifest, first, count, _timeout = (rest[0], int(rest[1]),
                                            int(rest[2]), int(rest[3]))
        lines = pathlib.Path(manifest).read_text().split("\n")
        out: list[str] = []
        for n in range(count):
            idx = int(lines[first + n].split()[0])
            self.jobs += 1
            diag = 0
            err = ""
            if self.jobs > self.eval_limit:
                diag = 4
                err = " err=eval_limit"
            elif self.rng.random() < self.fault_rate:
                err = " err=timeout"
            else:
                self.compl += 1

            label = int(self.labels[idx])
            pred = label if self.rng.random() < self.accuracy else self.rng.randrange(
                NUM_CLASSES)
            logits = np.full(OUTPUT_HALVES, -4.0, dtype=np.float16)
            logits[:NUM_CLASSES] = self.rng.uniform(-8, 2)
            logits[pred] = 12.0 + self.rng.uniform(0, 3)
            # Same word-oriented payload contract as jtag_worker.tcl.
            payload = "".join(f"{w:04x}" for w in logits.view(np.uint16))
            if err and "eval_limit" in err:
                payload = "5a5a" * OUTPUT_HALVES

            before = self.counter
            self.counter += 264_000 + self.rng.randrange(-2000, 2000)
            out.append(
                f"i={idx} compl={self.compl} polls=2 us_wr=65500 us_q=950 "
                f"us_poll=2100 us_rd=1400 irq=0x00000002 diag=0x{diag:08x} "
                f"ctr_before=0x{before & 0xffffffff:08x} "
                f"ctr_after=0x{self.counter & 0xffffffff:08x} out={payload}{err}")
            if err:
                return None, out  # type: ignore[return-value]
        return str(count), out


# --------------------------------------------------------------------------
# Programmer
# --------------------------------------------------------------------------
class Programmer:
    def __init__(self, cfg: Config, simulate: bool = False):
        self.cfg = cfg
        self.simulate = simulate
        self.count = 0

    def program(self) -> float:
        """Program the SOF.  Returns elapsed seconds.

        The cable is exclusive: no System Console worker may be alive here.  The
        caller enforces that by stopping the backend first.
        """
        t0 = time.perf_counter()
        self.count += 1
        if self.simulate:
            time.sleep(0.05)
            return time.perf_counter() - t0
        sof = pathlib.Path(self.cfg.sof)
        if not sof.exists():
            raise SystemExit(f"missing SOF: {sof}")
        r = subprocess.run(
            [self.cfg.quartus_pgm, "-c", self.cfg.cable, "-m", "jtag",
             "-o", f"p;{sof}"],
            capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise WorkerError(f"quartus_pgm failed ({r.returncode}): {r.stderr[-400:]}")
        return time.perf_counter() - t0


# --------------------------------------------------------------------------
# Progress persistence
# --------------------------------------------------------------------------
class Progress:
    """Append-only JSONL of scored images + a small resume state file.

    The JSONL is the single source of truth: accuracy is always recomputed from
    it, so a resumed run and an uninterrupted run produce identical reports.
    The state file only answers "where do I restart".
    """

    def __init__(self, run_dir: pathlib.Path):
        run_dir.mkdir(parents=True, exist_ok=True)
        self.results = run_dir / "results.jsonl"
        self.state = run_dir / "state.json"
        self._fh = None

    def open(self):
        self._fh = self.results.open("a", encoding="utf-8")
        return self

    def close(self):
        if self._fh:
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._fh.close()
            self._fh = None

    def append(self, record: dict) -> None:
        assert self._fh
        self._fh.write(json.dumps(record, separators=(",", ":")) + "\n")

    def flush(self) -> None:
        if self._fh:
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def done_indices(self) -> set[int]:
        if not self.results.exists():
            return set()
        done = set()
        with self.results.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    done.add(json.loads(line)["i"])
                except (json.JSONDecodeError, KeyError):
                    continue        # torn final line after a crash; ignore it
        return done

    def read_all(self) -> list[dict]:
        if not self.results.exists():
            return []
        out = []
        with self.results.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def save_state(self, **kv) -> None:
        kv["saved_at"] = time.time()
        tmp = self.state.with_suffix(".tmp")
        tmp.write_text(json.dumps(kv, indent=2))
        tmp.replace(self.state)

    def load_state(self) -> dict:
        if not self.state.exists():
            return {}
        try:
            return json.loads(self.state.read_text())
        except json.JSONDecodeError:
            return {}


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
class Runner:
    def __init__(self, cfg: Config, backend_factory, programmer: Programmer,
                 manifest: pathlib.Path, labels: np.ndarray,
                 progress: Progress, verbose: bool = False):
        self.cfg = cfg
        self.backend_factory = backend_factory
        self.programmer = programmer
        self.manifest = manifest
        self.labels = labels
        self.progress = progress
        self.verbose = verbose

        self.backend: Backend | None = None
        self.jobs_since_program = 0
        # Continue the cycle numbering across a resume so "images per cycle" in
        # the report stays a faithful account of the whole campaign rather than
        # collapsing every session onto cycle 0.
        prev = progress.load_state().get("prog_cycle")
        self.prog_cycle = prev if isinstance(prev, int) else -1
        self.observed_limit: int | None = None
        self.events: list[dict] = []
        self.cal: dict | None = None
        self.retries: dict[int, int] = {}     # image index -> failures so far
        self.abandoned: set[int] = set()

    # -- programming cycle -------------------------------------------------
    def reprogram(self, reason: str) -> None:
        if self.backend is not None:
            self.backend.stop()          # release the cable before quartus_pgm
            self.backend = None
        secs = self.programmer.program()
        self.prog_cycle += 1
        self.jobs_since_program = 0
        self.event("program", cycle=self.prog_cycle, reason=reason, seconds=secs)

        self.backend = self.backend_factory()
        self.backend.start()
        path = self.backend.open_master()
        self.event("master", path=path)

        self.backend.configure(
            csr_base=hex(self.cfg.csr_base),
            mem_base=hex(self.cfg.mem_base),
            input_off=self.cfg.input_off,
            output_off=self.cfg.output_off,
            output_halves=OUTPUT_HALVES,
            poison=1 if self.cfg.poison_output else 0,
            counters=",".join(hex(c["addr"]) for c in self.cfg.latency_counters),
            config_base=hex(self.cfg.config_base),
            config_range_minus_two=self.cfg.config_range_minus_two,
            intermediate_base=hex(self.cfg.intermediate_base),
            io_base=hex(self.cfg.io_base),
        )

        if self.cfg.calibrate_bytes and self.cal is None:
            ok, _ = self.backend.command(f"CAL {self.cfg.calibrate_bytes} "
                                         f"{(self.progress.state.parent / 'cal.bin').as_posix()}")
            kv = dict(t.split("=", 1) for t in ok.split() if "=" in t)
            n = int(kv["bytes"])
            self.cal = {
                "write_kBps": round(n / max(1, int(kv["wr_us"])) * 1e6 / 1024, 1),
                "read_kBps": round(n / max(1, int(kv["rd_us"])) * 1e6 / 1024, 1),
            }
            self.event("calibrate", **self.cal)

        # Parameter-image readback verification.  The MIFs are baked into the
        # SOF, so the params load at configuration time for free -- but a
        # corrupted or STALE load is completely silent: the IP will happily run
        # a previous build's weights and return plausible-looking wrong logits.
        # dla_par0/1/2 are contiguous in host space, so one RBLK covers the
        # whole 205,056-byte image.  Measured cost: ~140 ms per cycle.
        # param_bytes == 0 means the arch has enable_on_chip_parameters: config
        # and filters are ROMs inside the IP, there is no pseudo-DDR parameter
        # image to read back, and dla_par0/1/2 do not exist.  Reading param_base
        # would land in undecoded space and hang the master.
        if (self.cfg.verify_params and self.cfg.param_bytes
                and self.backend.supports_readback):
            blob = self.progress.state.parent / "param_readback.bin"
            self.backend.command(
                f"RBLK {hex(self.cfg.param_base)} {self.cfg.param_bytes} "
                f"{blob.as_posix()}")
            got = blob.read_bytes()
            digest = fnv1a(got)
            good = (len(got) == self.cfg.param_bytes
                    and (not self.cfg.param_fnv1a or digest == self.cfg.param_fnv1a))
            self.event("verify_params", bytes=len(got), fnv1a=hex(digest),
                       expected=hex(self.cfg.param_fnv1a),
                       status="ok" if good else "MISMATCH")
            if not good:
                # Wrong weights are worse than no run: every subsequent number
                # would be a confident lie.  Stop here.
                raise WorkerError(
                    f"parameter image readback mismatch: {len(got)} B, "
                    f"fnv1a={digest:#010x}, expected {self.cfg.param_fnv1a:#010x}")

        init = self.backend.csrinit()
        self.event("csrinit", **init)

        for _ in range(self.cfg.warmup_jobs):
            self.jobs_since_program += 1     # warm-ups count against the budget
            self.warmup_job()

        self.progress.save_state(
            prog_cycle=self.prog_cycle,
            jobs_since_program=self.jobs_since_program,
            observed_limit=self.observed_limit)

    def warmup_job(self) -> None:
        """One golden inference, asserted, immediately after a programming.

        WBLK the synthetic stimulus into the input slave, then JOB (which does
        not write an input of its own -- infile "-") and check the argmax.  A
        mismatch means this programming cycle is untrustworthy, and every image
        it would go on to score would be a confident lie, so it aborts.
        """
        assert self.backend is not None
        img = pathlib.Path(self.cfg.warmup_image)
        if not img.exists() or not self.backend.supports_readback:
            # SimBackend has no WBLK/JOB semantics; do not pretend otherwise.
            self.event("warmup", status="skipped",
                       reason="no stimulus" if not img.exists() else "sim backend")
            return
        addr = self.cfg.mem_base + self.cfg.input_off
        self.backend.command(
            f"WBLK {hex(addr)} {img.stat().st_size} {img.as_posix()}")
        _, res = self.backend.command(
            f"JOB {hex(self.cfg.io_base)} {self.cfg.job_timeout_ms}",
            collect_res=True)
        if not res:
            self.event("warmup", status="no_result")
            raise WorkerError("warm-up job returned no RES line")
        rec = dict(t.split("=", 1) for t in res[0].split() if "=" in t)
        logits = decode_logits(rec.get("out", ""))
        pred = int(np.argmax(logits))
        stale = is_poison(rec.get("out", ""))
        good = (not stale and rec.get("err") is None
                and (self.cfg.warmup_expect_class < 0
                     or pred == self.cfg.warmup_expect_class))
        self.event("warmup", status="ok" if good else "MISMATCH", pred=pred,
                   expect=self.cfg.warmup_expect_class, stale=stale,
                   diag=rec.get("diag"), err=rec.get("err"),
                   logits=[round(x, 3) for x in logits[:NUM_CLASSES]])
        if not good:
            raise WorkerError(
                f"warm-up golden image failed: pred={pred} "
                f"expected={self.cfg.warmup_expect_class} stale={stale} "
                f"err={rec.get('err')} diag={rec.get('diag')}")

    def event(self, kind: str, **kv) -> None:
        rec = {"t": time.time(), "event": kind, **kv}
        self.events.append(rec)
        if self.verbose or kind in ("program", "eval_limit", "timeout", "calibrate"):
            print(f"[{kind}] " + " ".join(f"{k}={v}" for k, v in kv.items()))

    # -- main loop ---------------------------------------------------------
    def run(self, todo: list[int]) -> None:
        pending = list(todo)
        if not pending:
            return
        self.reprogram("initial")

        # `pending` is index-ordered and the manifest is index-ordered, so a
        # contiguous run of pending images maps to one BATCH.  After a resume
        # the pending set can be sparse; runs are then shorter, which is fine.
        while pending:
            headroom = self.cfg.reprogram_at - self.jobs_since_program
            if headroom <= 0:
                self.reprogram("budget")
                continue

            run_len = 1
            while (run_len < len(pending)
                   and pending[run_len] == pending[run_len - 1] + 1
                   and run_len < self.cfg.batch_size
                   and run_len < headroom):
                run_len += 1
            first, count = pending[0], run_len

            assert self.backend is not None
            # Charge the whole batch to the budget BEFORE issuing it: if the
            # worker dies mid-batch we cannot know how many descriptors reached
            # the IP, and over-counting only costs an early reprogram whereas
            # under-counting silently runs past the eval limit.
            base_jobs = self.jobs_since_program
            self.jobs_since_program = base_jobs + count
            records, _ = self.backend.batch(self.manifest, first, count,
                                            self.cfg.job_timeout_ms)
            # The worker emits exactly one RES per queueing write, so once it
            # returns cleanly the true count is known and the guess is refunded.
            self.jobs_since_program = base_jobs + len(records)

            retry_from: int | None = None
            for pos, rec in enumerate(records):
                idx = int(rec["i"])
                err = rec.get("err")
                if err == "eval_limit":
                    # This job produced nothing valid.  Record where the wall
                    # actually is, then reprogram and retry this same image.
                    hit = base_jobs + pos + 1
                    self.observed_limit = hit
                    self.event("eval_limit", image=idx, at_job=hit,
                               configured_budget=self.cfg.eval_budget)
                    self.cfg.reprogram_at = max(100, hit - 500)
                    retry_from = idx
                    break
                if err:
                    self.event("timeout", image=idx, detail=err,
                               polls=rec.get("polls"))
                    retry_from = idx
                    break
                self.progress.append(self.score(rec))

            # An image that fails every time must not be allowed to spend the
            # whole run on reprogramming cycles.  Give up on it after a few
            # attempts, note it in the log, and carry on with the rest.
            if retry_from is not None:
                self.retries[retry_from] = self.retries.get(retry_from, 0) + 1
                if self.retries[retry_from] >= self.cfg.max_retries_per_image:
                    self.abandoned.add(retry_from)
                    self.event("abandon", image=retry_from,
                               attempts=self.retries[retry_from])

            # Anything the worker never reported (aborted batch) stays pending.
            reported_ok = {int(r["i"]) for r in records if not r.get("err")}
            pending = [i for i in pending[:count]
                       if i not in reported_ok and i not in self.abandoned] + pending[count:]

            self.progress.flush()
            self.progress.save_state(
                prog_cycle=self.prog_cycle,
                jobs_since_program=self.jobs_since_program,
                observed_limit=self.observed_limit,
                next_pending=pending[0] if pending else None,
                remaining=len(pending))

            if retry_from is not None and pending:
                if self.programmer.count >= self.cfg.max_programmings:
                    self.event("give_up", reason="max_programmings",
                               remaining=len(pending))
                    break
                self.reprogram("fault")

        if self.backend:
            self.backend.stop()
            self.backend = None

    # -- scoring -----------------------------------------------------------
    def score(self, rec: dict) -> dict:
        idx = int(rec["i"])
        payload = rec["out"]
        stale = is_poison(payload)
        logits = decode_logits(payload)
        pred = int(np.argmax(logits)) if not stale else -1
        label = int(self.labels[idx])

        before = [int(x, 16) for x in rec.get("ctr_before", "").split(",") if x]
        after = [int(x, 16) for x in rec.get("ctr_after", "").split(",") if x]
        counters, latency_ns = {}, None
        for spec, b, a in zip(self.cfg.latency_counters, before, after):
            delta = (a - b) & 0xFFFFFFFF        # 32-bit free-running wrap
            counters[spec["name"]] = {"before": b, "after": a, "delta": delta}
            if spec.get("primary") and spec.get("hz"):
                latency_ns = delta * 1e9 / spec["hz"]

        return {
            "i": idx,
            "label": label,
            "pred": pred,
            "ok": (pred == label),
            "stale": stale,
            "logits": [round(x, 4) for x in logits],
            "prog_cycle": self.prog_cycle,
            # device-clocked -- the ONLY admissible latency source
            "fpga": {"counters": counters, "latency_ns": latency_ns},
            # host wall clock around JTAG traffic -- transport only, never
            # combined with the above.  poll_wait bounds latency, is not it.
            "host_us": {
                "input_write": int(rec.get("us_wr", 0)),
                "queue": int(rec.get("us_q", 0)),
                "poll_wait": int(rec.get("us_poll", 0)),
                "output_read": int(rec.get("us_rd", 0)),
            },
            "polls": int(rec.get("polls", 0)),
            "irq": rec.get("irq"),
            "diag": rec.get("diag"),
        }


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(values), q)) if values else float("nan")


def report(records: list[dict], events: list[dict], cfg: Config,
           wall_s: float) -> str:
    L: list[str] = []
    n = len(records)
    if n == 0:
        return "no scored images"

    scored = [r for r in records if not r.get("stale")]
    correct = sum(1 for r in scored if r["ok"])
    L.append("=" * 72)
    L.append("CIFAR-10 on AXC3000 CoreDLA ResNet-8 -- JTAG host runner")
    L.append("=" * 72)
    L.append("")
    L.append("ACCURACY")
    L.append(f"  images scored      {len(scored)}")
    L.append(f"  discarded (stale)  {n - len(scored)}")
    L.append(f"  top-1 correct      {correct}")
    L.append(f"  top-1 accuracy     {100.0 * correct / max(1, len(scored)):.2f} %")
    L.append("")

    conf = np.zeros((NUM_CLASSES, NUM_CLASSES), dtype=np.int64)
    for r in scored:
        if 0 <= r["pred"] < NUM_CLASSES:
            conf[r["label"], r["pred"]] += 1
    L.append("CONFUSION (rows = true, cols = predicted)")
    L.append("            " + "".join(f"{c[:5]:>7}" for c in CIFAR10_CLASSES))
    for i, cname in enumerate(CIFAR10_CLASSES):
        support = conf[i].sum()
        recall = 100.0 * conf[i, i] / support if support else 0.0
        L.append(f"  {cname:<10}" + "".join(f"{v:>7d}" for v in conf[i])
                 + f"   n={support:<5d} recall={recall:5.1f}%")
    L.append("  " + " " * 10 + "".join(
        f"{100.0 * conf[j, j] / conf[:, j].sum() if conf[:, j].sum() else 0.0:>6.1f}%"
        for j in range(NUM_CLASSES)) + "   <- precision")
    L.append("")

    L.append("FPGA LATENCY (device counters only -- never host wall clock)")
    lat = [r["fpga"]["latency_ns"] for r in scored
           if r["fpga"].get("latency_ns") is not None]
    if lat:
        L.append(f"  mean   {np.mean(lat) / 1e6:9.4f} ms")
        L.append(f"  median {percentile(lat, 50) / 1e6:9.4f} ms")
        L.append(f"  p95    {percentile(lat, 95) / 1e6:9.4f} ms")
        L.append(f"  p99    {percentile(lat, 99) / 1e6:9.4f} ms")
        L.append(f"  min    {min(lat) / 1e6:9.4f} ms   max {max(lat) / 1e6:9.4f} ms")
        L.append(f"  DLA-only throughput  {1e9 / np.mean(lat):8.1f} inferences/s")
    else:
        names = sorted({k for r in scored for k in r["fpga"]["counters"]})
        L.append("  NOT MEASURED -- no counter is marked \"primary\" with an `hz`")
        L.append("  domain in Config.latency_counters.  See TODO(JTAG-MAP).")
        if names:
            L.append("  raw counter deltas were still recorded:")
            for nm in names:
                d = [r["fpga"]["counters"][nm]["delta"] for r in scored
                     if nm in r["fpga"]["counters"]]
                L.append(f"    {nm:<24} mean {np.mean(d):12.1f}  "
                         f"min {min(d)}  max {max(d)}")
    L.append("")

    L.append("JTAG TRANSPORT (host wall clock -- NOT inference latency)")
    for key, label in (("input_write", f"input write ({INPUT_BYTES // 1024} KiB)"),
                       ("queue", "queue write (CSR 536)"),
                       ("poll_wait", "completion poll wait*"),
                       ("output_read", "output read")):
        v = [r["host_us"][key] / 1000.0 for r in scored]
        L.append(f"  {label:<24} mean {np.mean(v):8.3f} ms   "
                 f"p95 {percentile(v, 95):8.3f} ms")
    tot = [sum(r["host_us"].values()) / 1000.0 for r in scored]
    L.append(f"  {'per-image total':<24} mean {np.mean(tot):8.3f} ms")
    wr = [r["host_us"]["input_write"] for r in scored if r["host_us"]["input_write"]]
    if wr:
        L.append(f"  achieved write throughput  "
                 f"{INPUT_BYTES / np.mean(wr) * 1e6 / 1024:.1f} KiB/s")
    L.append("  * poll wait contains the inference AND >=1 JTAG round trip;")
    L.append("    it upper-bounds FPGA latency and must never be reported as it.")
    L.append("")

    L.append("RUN INTEGRITY")
    programs = [e for e in events if e["event"] == "program"]
    L.append(f"  programming cycles  {len(programs)}")
    if programs:
        L.append(f"  mean program time   {np.mean([e['seconds'] for e in programs]):.2f} s")
    for kind in ("eval_limit", "timeout", "abandon", "give_up"):
        hits = [e for e in events if e["event"] == kind]
        L.append(f"  {kind + ' events':<20}{len(hits)}")
        for h in hits[:5]:
            L.append(f"      {h}")
    per_cycle: dict[int, int] = {}
    for r in records:
        per_cycle[r["prog_cycle"]] = per_cycle.get(r["prog_cycle"], 0) + 1
    L.append(f"  images per cycle    {dict(sorted(per_cycle.items()))}")
    L.append(f"  wall clock          {wall_s:.1f} s ({wall_s / 60:.1f} min)")
    L.append(f"  per-image wall      {wall_s / n * 1000:.1f} ms")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-dir", type=pathlib.Path,
                    default=REPO / "build/fpga_ai/bench/run1")
    ap.add_argument("--dataset", type=pathlib.Path,
                    default=REPO / "build/fpga_ai/cifar10/cifar-10-python.tar.gz")
    ap.add_argument("--config", type=pathlib.Path, default=None)
    ap.add_argument("--backend", choices=("sim", "syscon"), default="sim")
    ap.add_argument("--images", type=int, default=10000)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--index-file", type=pathlib.Path, default=None,
                    help="restrict the run to these CIFAR-10 test indices "
                         "(.npy or whitespace/comma-separated text).  This is "
                         "how the official MLPerf Tiny ic01 200-image subset is "
                         "selected: perf_samples_idxs.npy.  Indices are run in "
                         "ascending order so that adjacent ones still share a "
                         "BATCH; ordering does not affect accuracy and the "
                         "latency counter is per-job.")
    ap.add_argument("--pack-dir", type=pathlib.Path, default=None,
                    help="reuse an existing packed-input directory (inputs/ + "
                         "manifest.txt) instead of writing another 164 MiB copy")
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--reprogram-at", type=int, default=None)
    ap.add_argument("--resume", action="store_true",
                    help="skip images already present in results.jsonl")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--rebuild-inputs", action="store_true")
    ap.add_argument("--sim-eval-limit", type=int, default=None)
    ap.add_argument("--sim-fault-rate", type=float, default=0.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    cfg = Config.load(args.config)
    # Input geometry follows the arch's c_vector and must be applied before any
    # packing happens.  calibrate_bytes defaults to one input tensor and is
    # clamped here: CAL writes at mem_base + 0, so anything larger walks off the
    # input slave into the output slave.
    set_input_cvec(cfg.input_cvec)
    if cfg.calibrate_bytes > INPUT_BYTES:
        cfg.calibrate_bytes = INPUT_BYTES
    if args.batch_size:
        cfg.batch_size = args.batch_size
    if args.reprogram_at:
        cfg.reprogram_at = args.reprogram_at
    if cfg.reprogram_at > cfg.eval_budget:
        raise SystemExit("reprogram_at must be <= eval_budget")

    args.run_dir.mkdir(parents=True, exist_ok=True)
    progress = Progress(args.run_dir)

    if args.report_only:
        events_path = args.run_dir / "events.jsonl"
        events = [json.loads(l) for l in events_path.read_text().splitlines()
                  if l.strip()] if events_path.exists() else []
        print(report(progress.read_all(), events, cfg, 0.0))
        return 0

    print(f"loading dataset {args.dataset}")
    images, labels = load_cifar10_test(args.dataset)
    print(f"  {len(images)} images, {len(np.unique(labels))} classes")

    if args.index_file:
        if args.index_file.suffix == ".npy":
            idxs = np.load(args.index_file).astype(int).ravel().tolist()
        else:
            idxs = [int(t) for t in
                    args.index_file.read_text().replace(",", " ").split()]
        bad = [i for i in idxs if not 0 <= i < len(images)]
        if bad:
            raise SystemExit(f"index file has {len(bad)} out-of-range indices")
        wanted = sorted(set(idxs))
        if len(wanted) != len(idxs):
            print(f"  note: {len(idxs) - len(wanted)} duplicate indices collapsed")
        print(f"index file {args.index_file.name}: {len(wanted)} images")
    else:
        wanted = list(range(args.offset,
                            min(args.offset + args.images, len(images))))
    pack = InputPack(args.pack_dir or args.run_dir)
    print(f"packing inputs -> {pack.dir}")
    manifest = pack.build(images, force=args.rebuild_inputs)

    todo = wanted
    if args.resume:
        done = progress.done_indices()
        todo = [i for i in wanted if i not in done]
        print(f"resume: {len(done)} already scored, {len(todo)} remaining")

    if args.backend == "sim":
        sim_seed = [0]

        def factory() -> Backend:
            # A fresh seed per programming cycle, otherwise the fake device
            # faults on exactly the same image every cycle and the retry logic
            # is being tested against an impossible device.
            sim_seed[0] += 1
            return SimBackend(cfg, labels, eval_limit=args.sim_eval_limit,
                              fault_rate=args.sim_fault_rate, seed=sim_seed[0])
        programmer = Programmer(cfg, simulate=True)
    else:
        def factory() -> Backend:
            return SysconBackend(cfg, verbose=args.verbose)
        programmer = Programmer(cfg, simulate=False)

    runner = Runner(cfg, factory, programmer, manifest, labels, progress,
                    verbose=args.verbose)

    t0 = time.perf_counter()
    progress.open()
    try:
        runner.run(todo)
    except KeyboardInterrupt:
        print("\ninterrupted -- progress saved; rerun with --resume")
    finally:
        progress.close()
        if runner.backend:
            runner.backend.stop()
    wall = time.perf_counter() - t0

    with (args.run_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
        for e in runner.events:
            fh.write(json.dumps(e) + "\n")

    text = report(progress.read_all(), runner.events, cfg, wall)
    (args.run_dir / "report.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
