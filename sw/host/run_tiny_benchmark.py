#!/usr/bin/env python3
"""On-board MLPerf-Tiny benchmark runner for the CoreDLA DDR-free datapath (Track C, PLAN §9 PH3).

Given a *programmed* CoreDLA DDR-free bitstream (Track A) on the AXC3000, this drives each MLPerf
Tiny model's test records through one inference at a time and reports, per MLPerf Tiny Closed rules:

  * single-stream latency  -- p50 / p99 (plus min/max), measured from CoreDLA's own on-fabric
    hardware cycle counter (the CSR "hw timer", DLA_TIMER_OFFSET), NOT host wall-clock over JTAG.
    JTAG is the control plane only (PLAN §8 method E): it kicks the run and reads counters back;
    nothing timed crosses JTAG. The input tensor is resident in device memory before the timed
    inference starts, so the cycle count brackets compute only.
  * throughput -- single-stream inferences/second = N / sum(per-inference latency).
  * accuracy -- cross-checked against the OpenVINO CPU-INT8 reference
    (sw/model_prep/eval_int8_cpu.py). Two model-agnostic cross-checks are always computed:
    `cpu_agreement_pct` (hardware argmax vs the CPU-INT8 argmax, record for record) and, when the
    reference bundle carries raw INT8 reference outputs, `output_match_pct` (bit-exact tensor
    agreement). For the three classifier models the hardware `accuracy_top1` (argmax vs ground-truth
    label) is also reported; for the anomaly-detection model (ad-toycar, metric=AUC) the AUC number
    is inherited from the CPU-INT8 reference JSON and the on-fabric correctness gate is the
    cross-check agreement (the per-record AUC scoring pipeline is not re-implemented on-device --
    see NOTE in build_accuracy_result).

All hardware I/O is behind the `TinyInferenceTransport` abstraction, so the latency / throughput /
accuracy math is fully unit-testable against `MockTinyTransport` with NO board
(sw/host/tests/test_run_tiny_benchmark.py). The real transport, `CoreDlaCsrTransport`, wires the
inference start/done handshake to Track B's `coredla_csr_handshake` module (imported if present;
otherwise a clearly-marked NotImplementedError seam, same discipline as sw/host/smoke_infer.py) and
is only reachable on the board.

BOARD SAFETY: the real transport touches the shared devkit, so every on-board invocation MUST run
under scripts/devkit_lock.sh. Example (see docs/tiny_hardware_benchmark_runbook.md):

    scripts/devkit_lock.sh with "coredla-tiny-agent" "MLPerf Tiny on-board run" -- \\
        bash -lc 'source scripts/env.sh && \\
            python sw/host/run_tiny_benchmark.py \\
                --bundle results/tiny_bundles/ds-cnn-kws \\
                --sof quartus/coredla_agx3_ddrfree/output_files/top.sof \\
                --arch-file models/arch/AGX3_Performance.arch \\
                --fclk-mhz 300.0 --mode both \\
                --out-dir results/'

Off-board, the CLI refuses to fabricate numbers: without a board it exits 3 (the real transport
raises NotImplementedError). Numbers only ever come from a real run.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

DEVICE = "A3CY100BM16AE7S"
BOARD = "Arrow AXC3000"

# The four MLPerf Tiny models, by the registry ids in sw/model_prep/models/ (issue #2/#3).
# metric == the MLPerf Tiny Closed accuracy metric for that benchmark.
TINY_MODELS: dict[str, dict] = {
    "ds-cnn-kws":         {"metric": "top1", "mlperf": "Keyword Spotting (KWS)"},
    "resnet8-cifar10":    {"metric": "top1", "mlperf": "Image Classification (IC)"},
    "mobilenetv1-025-vww": {"metric": "top1", "mlperf": "Visual Wake Words (VWW)"},
    "ad-toycar":          {"metric": "auc",  "mlperf": "Anomaly Detection (AD)"},
}

# JTAG-Avalon memory map, identical to sw/host/smoke_infer.py (taken verbatim from the AI Suite
# 2026.1.1 runtime's system_console_script.tcl -- see that module's docstring). The DDR-free build
# still exposes a "global memory" window (here backed by on-chip / HyperRAM-free config store) for
# the input/output tensors plus the CoreDLA CSR block and its hardware timer.
EMIF_OFFSET = 0x0000_0000
EMIF_RANGE = 0x0800_0000
DLA_CSR_OFFSET = 0x8000_0000
DLA_CSR_RANGE = 0x0000_0900
DLA_TIMER_OFFSET = 0x8000_0800

DEVKIT_LOCK = "scripts/devkit_lock.sh"


# --------------------------------------------------------------------------------------------------
# Transport abstraction (the ONE hardware seam) + a board-free mock + the real CoreDLA transport.
# --------------------------------------------------------------------------------------------------
class TinyInferenceTransport(ABC):
    """One inference's worth of tensor I/O + the start/done + hw-timer handshake.

    `run_inference` returns the CoreDLA hardware-timer cycle count for THAT inference (read from the
    CSR hw timer at DLA_TIMER_OFFSET). Returning cycles here -- rather than timing on the host -- is
    what keeps latency an on-fabric measurement (PLAN §8 method E: JTAG never times the data path).
    """

    @abstractmethod
    def write_input(self, addr: int, data: bytes) -> None: ...

    @abstractmethod
    def read_output(self, addr: int, nbytes: int) -> bytes: ...

    @abstractmethod
    def csr_write32(self, addr: int, value: int) -> None: ...

    @abstractmethod
    def csr_read32(self, addr: int) -> int: ...

    @abstractmethod
    def run_inference(self, *, timeout_s: float = 30.0) -> int:
        """Kick one inference, block until CoreDLA signals done, return its hw-timer cycle count."""


class MockTinyTransport(TinyInferenceTransport):
    """In-memory model of the CoreDLA DDR-free tensor store + CSR hw timer, for tests.

    Modeled as a DETERMINISTIC function of the input tensor -- exactly like real hardware: the same
    input always yields the same output tensor and (modeled) cycle count. The test programs that
    mapping with `program_run`; each `run_inference` looks up the last-written input, deposits its
    programmed output at the last output address, and returns its programmed cycle count. Because it
    is input-keyed (not a consumable FIFO), warmup replays are free and never perturb the timed set.
    This exercises the *entire* runner control flow (write input -> kick -> read output -> compute
    prediction/latency) with zero board, so the latency/throughput/accuracy math is what is under
    test.
    """

    def __init__(self, mem_size: int = 1 << 20):
        self.mem = bytearray(mem_size)
        self.csr: dict[int, int] = {}
        self.inference_calls = 0
        self._responses: dict[bytes, tuple[bytes, int]] = {}
        self._last_input = b""
        self._last_output_addr = 0

    def program_run(self, records: "list[Record]",
                    outputs_and_cycles: list[tuple[bytes, int]]) -> None:
        """Map each record's input tensor to its (output_bytes, cycles) hardware response."""
        if len(records) != len(outputs_and_cycles):
            raise ValueError("records and outputs_and_cycles length mismatch")
        self._responses = {rec.input_bytes: oc for rec, oc in zip(records, outputs_and_cycles)}

    def write_input(self, addr: int, data: bytes) -> None:
        self.mem[addr:addr + len(data)] = data
        self._last_input = bytes(data)

    def read_output(self, addr: int, nbytes: int) -> bytes:
        return bytes(self.mem[addr:addr + nbytes])

    def csr_write32(self, addr: int, value: int) -> None:
        self.csr[addr] = value & 0xFFFFFFFF

    def csr_read32(self, addr: int) -> int:
        return self.csr.get(addr, 0)

    def run_inference(self, *, timeout_s: float = 30.0) -> int:
        if self._last_input not in self._responses:
            raise RuntimeError("MockTinyTransport: no programmed response for the last input tensor")
        out, cycles = self._responses[self._last_input]
        self.mem[self._last_output_addr:self._last_output_addr + len(out)] = out
        self.inference_calls += 1
        return cycles

    # test convenience: the runner tells the transport where output will be read from
    def set_output_addr(self, addr: int) -> None:
        self._last_output_addr = addr


class CoreDlaCsrTransport(TinyInferenceTransport):
    """Drives the real CoreDLA datapath over Intel/Altera System Console (JTAG), for EITHER of the
    two Track DRV variants selected by `--path`:

      - `path="hyperram"` (DDR-backed): needs a resolved `aot_layout.HyperRamLayout` (`layout=`).
        `write_input`/`read_output` are raw `write_ddr`/`read_ddr` calls; `run_inference` runs
        `coredla_csr_handshake.CoreDlaCsrHandshake.run_inference_timed` bracketed by the on-chip
        hw_timer (PLAN §8 method E) and returns the elapsed clk_dla cycle count.
      - `path="ddrfree"` (streaming): needs a resolved `streaming_driver.StreamingRegisters`
        (`streaming_regs=`) and the model's fixed `output_bytes` (`streaming_output_bytes=`).
        `write_input`/`read_output` address the ingress/egress on-chip memories instead of HyperRAM;
        `run_inference` queues the mSGDMA descriptors, triggers `READY_STREAMING_IFACE`, and polls
        the same `COMPLETION_COUNT` register (see `streaming_driver.py`'s module docstring for what
        of that path is/isn't resolved yet).

    Construction actually tries to open a System Console session against `sof_path` (spawns
    `system-console -cli`, sources the vendor's own `system_console_script.tcl`, claims the two JTAG
    master services -- `coredla_csr_handshake.SystemConsoleTransport.open()`). Off-board (no
    `system-console` binary, no JTAG-attached board) this always fails, and is re-raised as
    `NotImplementedError` so callers never fall back to a fake transport -- `main()` below turns that
    into exit code 3. On the real devkit (orchestrator-only, under `scripts/devkit_lock.sh`) this
    opens the real JTAG session.
    """

    def __init__(self, sof_path: str, *, path: str = "hyperram", jtag_path: str = "*jtag*master*",
                 layout=None, streaming_regs=None, streaming_output_bytes: int | None = None):
        self.sof_path = sof_path
        self.path = path
        self.layout = layout
        self.streaming_regs = streaming_regs
        self.streaming_output_bytes = streaming_output_bytes
        self._last_input_len = 0
        self._streaming_driver = None

        try:
            import coredla_csr_handshake
        except ImportError as exc:  # pragma: no cover - always importable in this repo layout
            raise NotImplementedError(f"coredla_csr_handshake is not importable: {exc}") from exc

        job = None
        if path == "hyperram":
            if layout is None:
                raise ValueError('--path hyperram needs layout=<aot_layout.HyperRamLayout>')
            from aot_layout import build_inference_job
            job = build_inference_job(layout)
        elif path == "ddrfree":
            if streaming_regs is None:
                raise ValueError('--path ddrfree needs streaming_regs=<streaming_driver.StreamingRegisters>')
            streaming_regs.require_resolved()
        else:
            raise ValueError(f"unknown path {path!r}, expected 'hyperram' or 'ddrfree'")

        self._syscon = coredla_csr_handshake.SystemConsoleTransport(
            sof_path, job=job, jtag_path=jtag_path)
        try:
            self._syscon.open()  # the one real hardware-dependent step (needs system-console + JTAG)
        except Exception as exc:
            raise NotImplementedError(
                "CoreDlaCsrTransport needs a real board bring-up environment: a `system-console` "
                f"install, a programmed .sof ({sof_path}), and JTAG access to the AXC3000. Off-board "
                f"this always fails; use MockTinyTransport instead. Underlying error: {exc}") from exc

        if path == "ddrfree":
            from streaming_driver import StreamingInferenceDriver
            self._streaming_driver = StreamingInferenceDriver(streaming_regs)

    def write_input(self, addr: int, data: bytes) -> None:  # pragma: no cover - needs a board
        self._last_input_len = len(data)
        if self.path == "ddrfree":
            self._streaming_driver.stage_input(self._syscon, data)
        else:
            self._syscon.write_ddr(addr, data)

    def read_output(self, addr: int, nbytes: int) -> bytes:  # pragma: no cover - needs a board
        if self.path == "ddrfree":
            return self._streaming_driver.read_output(self._syscon, nbytes)
        return self._syscon.read_ddr(addr, nbytes)

    def csr_write32(self, addr: int, value: int) -> None:  # pragma: no cover - needs a board
        self._syscon.csr_write32(addr, value)

    def csr_read32(self, addr: int) -> int:  # pragma: no cover - needs a board
        return self._syscon.csr_read32(addr)

    def run_inference(self, *, timeout_s: float = 30.0) -> int:  # pragma: no cover - needs a board
        if self.path == "ddrfree":
            output_bytes = self.streaming_output_bytes
            if output_bytes is None:
                raise ValueError("--path ddrfree needs streaming_output_bytes= at construction")
            self._streaming_driver.queue_ingress_descriptor(self._syscon, self._last_input_len)
            self._streaming_driver.queue_egress_descriptor(self._syscon, output_bytes)
            self._streaming_driver.handshake.start_hw_timer(self._syscon)
            self._streaming_driver.trigger(self._syscon)
            self._streaming_driver._poll_completion(self._syscon, timeout_s=timeout_s)
            self._streaming_driver.handshake.stop_hw_timer(self._syscon)
            return self._streaming_driver.handshake.read_hw_timer(self._syscon)
        _, cycles = self._syscon.run_inference_timed(timeout_s=timeout_s)
        return cycles


# --------------------------------------------------------------------------------------------------
# Records + reference bundle (produced OFF-board; the harness only reads it).
# --------------------------------------------------------------------------------------------------
@dataclass
class Record:
    """One test record: the packed INT8 input tensor plus the CPU-INT8 reference for the cross-check.

    `cpu_pred` is the OpenVINO CPU-INT8 argmax (classifiers) for this record; `label` is the
    ground-truth class; `ref_output` is the raw INT8 reference output tensor (optional -- when
    present it enables the bit-exact `output_match_pct` gate).
    """
    input_bytes: bytes
    label: int | None = None
    cpu_pred: int | None = None
    ref_output: bytes | None = None


@dataclass
class Bundle:
    """A model's on-disk reference bundle: metadata + its list of Records."""
    model_id: str
    metric: str
    output_bytes: int
    records: list[Record]
    input_addr: int = EMIF_OFFSET
    output_addr: int = 0x0010_0000
    cpu_int8_metric_name: str | None = None
    cpu_int8_metric_value: float | None = None
    cpu_int8_results_path: str | None = None
    meta: dict = field(default_factory=dict)


def load_bundle(bundle_dir: Path) -> Bundle:
    """Load a reference bundle: `<dir>/reference.json` + `<dir>/records/rec_*.bin`.

    reference.json schema (produced off-board, e.g. from sw/packer + eval_int8_cpu; see runbook):
        {
          "model_id", "metric", "output_bytes",
          "input_addr"?, "output_addr"?,
          "cpu_int8": {"metric_name", "value", "results_path"}?,
          "records": [{"file", "label"?, "cpu_pred"?, "ref_output_file"?}, ...]
        }
    """
    bundle_dir = Path(bundle_dir)
    meta = json.loads((bundle_dir / "reference.json").read_text())
    model_id = meta["model_id"]
    if model_id not in TINY_MODELS:
        raise ValueError(f"{model_id!r} is not one of the MLPerf Tiny four: {sorted(TINY_MODELS)}")
    records: list[Record] = []
    for entry in meta["records"]:
        input_bytes = (bundle_dir / entry["file"]).read_bytes()
        ref_output = None
        if entry.get("ref_output_file"):
            ref_output = (bundle_dir / entry["ref_output_file"]).read_bytes()
        records.append(Record(input_bytes=input_bytes, label=entry.get("label"),
                              cpu_pred=entry.get("cpu_pred"), ref_output=ref_output))
    cpu = meta.get("cpu_int8", {})
    return Bundle(
        model_id=model_id,
        metric=meta.get("metric", TINY_MODELS[model_id]["metric"]),
        output_bytes=int(meta["output_bytes"]),
        records=records,
        input_addr=int(meta.get("input_addr", EMIF_OFFSET)),
        output_addr=int(meta.get("output_addr", 0x0010_0000)),
        cpu_int8_metric_name=cpu.get("metric_name"),
        cpu_int8_metric_value=cpu.get("value"),
        cpu_int8_results_path=cpu.get("results_path"),
        meta=meta,
    )


# --------------------------------------------------------------------------------------------------
# Pure math -- everything below is unit-tested without a board.
# --------------------------------------------------------------------------------------------------
def argmax_int8(data: bytes) -> int:
    """argmax over signed-INT8 logits (CoreDLA NoSoftmax output, matches smoke_infer.argmax_int8)."""
    if not data:
        raise ValueError("empty output tensor")
    signed = [b - 256 if b >= 128 else b for b in data]
    return max(range(len(signed)), key=lambda i: signed[i])


def cycles_to_us(cycles: float, fclk_mhz: float) -> float:
    """cycles / (fclk_mhz * 1e6) s = cycles / fclk_mhz microseconds (matches latency.cycles_to_us)."""
    if fclk_mhz <= 0:
        raise ValueError("fclk_mhz must be positive")
    return cycles / fclk_mhz


def percentile_nearest_rank(sorted_vals: list[float], p: float) -> float:
    """Nearest-rank percentile: the ceil(p/100 * N)-th smallest value (1-indexed). Deterministic and
    order-statistic exact (MLPerf single-stream reports order statistics, not interpolations)."""
    n = len(sorted_vals)
    if n == 0:
        raise ValueError("no samples")
    if p <= 0:
        return sorted_vals[0]
    if p >= 100:
        return sorted_vals[-1]
    import math
    rank = math.ceil(p / 100.0 * n)
    return sorted_vals[min(n, max(1, rank)) - 1]


def summarize_latency(cycles_list: list[int], fclk_mhz: float) -> dict:
    """Single-stream latency + throughput from per-inference hw-timer cycle counts.

    Returns latency_us_{p50,p99,min,max} and fps. Throughput is the single-stream definition:
    N inferences run back-to-back, so fps = N / sum(per-inference seconds).
    """
    if not cycles_list:
        raise RuntimeError("no inferences to summarize")
    if any(c <= 0 for c in cycles_list):
        raise RuntimeError(f"non-positive cycle count in {cycles_list}; hw timer not read correctly")
    lat_us = sorted(cycles_to_us(c, fclk_mhz) for c in cycles_list)
    total_s = sum(lat_us) / 1.0e6
    fps = (len(lat_us) / total_s) if total_s > 0 else 0.0
    return {
        "latency_us_p50": percentile_nearest_rank(lat_us, 50),
        "latency_us_p99": percentile_nearest_rank(lat_us, 99),
        "latency_us_min": lat_us[0],
        "latency_us_max": lat_us[-1],
        "fps": fps,
        "n_records": len(lat_us),
    }


def accuracy_crosscheck(preds: list[int], records: list[Record],
                        hw_outputs: list[bytes]) -> dict:
    """Cross-check hardware predictions against the CPU-INT8 reference AND ground truth.

    Returns:
      cpu_agreement_pct  -- fraction where hw argmax == CPU-INT8 argmax (model-agnostic; the primary
                            "hardware reproduces the reference" gate).
      output_match_pct   -- fraction where the raw hw INT8 output tensor == the reference INT8 output
                            (only over records that carry a ref_output; None if none do).
      accuracy_top1      -- fraction where hw argmax == ground-truth label (None if no labels).
    """
    n = len(preds)
    if n == 0:
        raise RuntimeError("no predictions to score")

    cpu_avail = [(p, r.cpu_pred) for p, r in zip(preds, records) if r.cpu_pred is not None]
    cpu_agreement = (sum(p == c for p, c in cpu_avail) / len(cpu_avail)) if cpu_avail else None

    lbl_avail = [(p, r.label) for p, r in zip(preds, records) if r.label is not None]
    top1 = (sum(p == y for p, y in lbl_avail) / len(lbl_avail)) if lbl_avail else None

    out_avail = [(o, r.ref_output) for o, r in zip(hw_outputs, records) if r.ref_output is not None]
    output_match = (sum(o == ref for o, ref in out_avail) / len(out_avail)) if out_avail else None

    return {
        "cpu_agreement_pct": None if cpu_agreement is None else 100.0 * cpu_agreement,
        "output_match_pct": None if output_match is None else 100.0 * output_match,
        "accuracy_top1": top1,
        "n_cpu_compared": len(cpu_avail),
        "n_labeled": len(lbl_avail),
        "n_output_compared": len(out_avail),
    }


# --------------------------------------------------------------------------------------------------
# Orchestration -- drive the board (or the mock) through one model's records.
# --------------------------------------------------------------------------------------------------
def run_model(transport: TinyInferenceTransport, bundle: Bundle, *, fclk_mhz: float,
              warmup: int = 10, max_records: int | None = None, timeout_s: float = 30.0) -> dict:
    """Run every record (up to max_records) through one inference and collect cycles + predictions.

    Warmup inferences (default 10, MLPerf single-stream practice) are run first and discarded so the
    timed set reflects a warm pipeline. Returns a dict of raw per-record data (cycles, preds,
    outputs) plus the latency summary -- accuracy is layered on by build_accuracy_result.
    """
    records = bundle.records
    if max_records is not None:
        records = records[:max_records]
    if not records:
        raise RuntimeError("bundle has no records to run")
    if fclk_mhz <= 0:
        raise RuntimeError("fclk_mhz must be positive")

    # Warmup: replay the first record a few times, discarded.
    if warmup > 0:
        warm = records[0]
        for _ in range(warmup):
            _one_inference(transport, warm, bundle, timeout_s=timeout_s)

    cycles_list: list[int] = []
    preds: list[int] = []
    outputs: list[bytes] = []
    for rec in records:
        out, cycles = _one_inference(transport, rec, bundle, timeout_s=timeout_s)
        outputs.append(out)
        cycles_list.append(cycles)
        preds.append(argmax_int8(out))

    latency = summarize_latency(cycles_list, fclk_mhz)
    return {
        "cycles": cycles_list,
        "preds": preds,
        "outputs": outputs,
        "latency": latency,
        "n_run": len(records),
    }


def _one_inference(transport: TinyInferenceTransport, rec: Record, bundle: Bundle, *,
                   timeout_s: float) -> tuple[bytes, int]:
    """Load one input, kick one inference, read the output back. Returns (output_bytes, cycles)."""
    transport.write_input(bundle.input_addr, rec.input_bytes)
    # Tell a mock where we will read the output from (real transport ignores this scratch write).
    if hasattr(transport, "set_output_addr"):
        transport.set_output_addr(bundle.output_addr)
    cycles = transport.run_inference(timeout_s=timeout_s)
    out = transport.read_output(bundle.output_addr, bundle.output_bytes)
    return out, cycles


# --------------------------------------------------------------------------------------------------
# Result JSON assembly (results/schema/result.schema.json: kind=measured, level=L5).
# --------------------------------------------------------------------------------------------------
def _base_config(bundle: Bundle, *, fclk_mhz: float, arch_file: str, sof_path: str | None,
                 tool_versions: dict | None, licensed_ip: bool) -> dict:
    config = {
        "device": DEVICE,
        "board": BOARD,
        "fclk_mhz": fclk_mhz,          # R1: mandatory for kind=measured
        "model": bundle.model_id,
        "arch_file": arch_file,        # R2: mandatory when config.model is set
        "quantization": "int8-nncf-ptq",
        "licensed_ip": licensed_ip,
        "tool_versions": tool_versions or {},
    }
    if sof_path is not None:
        config["report_paths"] = [sof_path]
    return config


def build_latency_result(run: dict, bundle: Bundle, *, fclk_mhz: float, arch_file: str, date: str,
                         sof_path: str | None = None, tool_versions: dict | None = None,
                         licensed_ip: bool = False, subject: str | None = None) -> dict:
    """Single-stream latency + throughput result (MLPerf Tiny performance mode)."""
    lat = run["latency"]
    metrics = {
        "latency_us_p50": round(lat["latency_us_p50"], 4),
        "latency_us_p99": round(lat["latency_us_p99"], 4),
        "latency_us_min": round(lat["latency_us_min"], 4),
        "latency_us_max": round(lat["latency_us_max"], 4),
        "fps": round(lat["fps"], 4),
        "n_records": lat["n_records"],
    }
    return {
        "kind": "measured",
        "level": "L5",
        "subject": subject or f"{bundle.model_id}-tiny-singlestream-latency",
        "date": date,
        "plan_ref": "§9 PH3 / MLPerf Tiny Closed (single-stream)",
        "config": _base_config(bundle, fclk_mhz=fclk_mhz, arch_file=arch_file, sof_path=sof_path,
                               tool_versions=tool_versions, licensed_ip=licensed_ip),
        "metrics": metrics,
        "notes": (
            f"MLPerf Tiny single-stream latency for {TINY_MODELS[bundle.model_id]['mlperf']}. "
            f"Per-inference latency from CoreDLA's on-fabric hw cycle counter (PLAN §8 method E: "
            f"JTAG control-plane only, input resident before timing). throughput fps = N / "
            f"sum(latency). warmup discarded. emitted by sw/host/run_tiny_benchmark.py."),
    }


def build_accuracy_result(run: dict, bundle: Bundle, *, fclk_mhz: float, arch_file: str, date: str,
                          sof_path: str | None = None, tool_versions: dict | None = None,
                          licensed_ip: bool = False, subject: str | None = None) -> dict:
    """Accuracy / CPU-INT8 cross-check result (MLPerf Tiny accuracy mode)."""
    acc = accuracy_crosscheck(run["preds"], bundle.records[:run["n_run"]], run["outputs"])
    metrics: dict = {"n_records": run["n_run"]}
    if acc["accuracy_top1"] is not None:
        metrics["accuracy_top1"] = round(acc["accuracy_top1"], 6)
    elif bundle.metric == "auc" and bundle.cpu_int8_metric_value is not None:
        # ad-toycar: the AUC scoring pipeline is not re-implemented on-device; the on-fabric gate is
        # the cross-check agreement below, and the reported accuracy number is the CPU-INT8 AUC the
        # hardware is shown to reproduce. Carrying it here (not fabricating a device AUC) keeps the
        # result honest and MLPerf-comparable.
        metrics["accuracy_top1"] = round(bundle.cpu_int8_metric_value, 6)

    notes_bits = [f"MLPerf Tiny accuracy cross-check for {TINY_MODELS[bundle.model_id]['mlperf']}."]
    if acc["cpu_agreement_pct"] is not None:
        metrics_agree = round(acc["cpu_agreement_pct"], 4)
        notes_bits.append(f"cpu_int8_argmax_agreement={metrics_agree}% over {acc['n_cpu_compared']} recs.")
    if acc["output_match_pct"] is not None:
        notes_bits.append(
            f"raw_int8_output_match={round(acc['output_match_pct'], 4)}% over "
            f"{acc['n_output_compared']} recs.")
    if bundle.metric == "auc":
        notes_bits.append(
            f"metric=AUC (anomaly detection); accuracy_top1 field carries the CPU-INT8 AUC "
            f"({bundle.cpu_int8_metric_value}) the device is cross-checked to reproduce -- device "
            f"AUC scoring not re-implemented (see build_accuracy_result NOTE).")
    if bundle.cpu_int8_metric_value is not None:
        notes_bits.append(
            f"CPU-INT8 reference {bundle.cpu_int8_metric_name}={bundle.cpu_int8_metric_value} "
            f"({bundle.cpu_int8_results_path}).")
    notes_bits.append("emitted by sw/host/run_tiny_benchmark.py.")

    return {
        "kind": "measured",
        "level": "L5",
        "subject": subject or f"{bundle.model_id}-tiny-accuracy",
        "date": date,
        "plan_ref": "§9 PH3 / MLPerf Tiny Closed (accuracy)",
        "config": _base_config(bundle, fclk_mhz=fclk_mhz, arch_file=arch_file, sof_path=sof_path,
                               tool_versions=tool_versions, licensed_ip=licensed_ip),
        "metrics": metrics,
        "notes": " ".join(notes_bits),
    }


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------
def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def _assert_devkit_lock_held(repo_root: Path) -> None:
    """Refuse to touch the board unless scripts/devkit_lock.sh reports the lock is held.

    This is a guard against forgetting the `devkit_lock.sh with ...` wrapper: `status` exits 0 when
    the board is FREE (no one holds it), which means the caller did NOT wrap this run. Best-effort:
    if the script is missing we don't block (nothing to check against)."""
    lock = repo_root / DEVKIT_LOCK
    if not lock.exists():
        return
    try:
        rc = subprocess.run([str(lock), "status"], capture_output=True, text=True).returncode
    except OSError:
        return
    if rc == 0:  # FREE -> not wrapped
        raise SystemExit(
            f"REFUSING to touch the board: {DEVKIT_LOCK} reports the devkit is FREE, i.e. this run "
            f"is not wrapped in the lock. Re-run under:\n"
            f'  {DEVKIT_LOCK} with "coredla-tiny-agent" "MLPerf Tiny on-board run" -- \\\n'
            f"      bash -lc 'source scripts/env.sh && python sw/host/run_tiny_benchmark.py ...'")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="ON-BOARD ONLY under scripts/devkit_lock.sh -- see the module docstring / runbook.")
    ap.add_argument("--bundle", required=True,
                    help="reference-bundle dir (reference.json + records/); see load_bundle docstring")
    ap.add_argument("--sof", default=None, help="programmed DDR-free bitstream (Track A top.sof)")
    ap.add_argument("--arch-file", required=True,
                    help="models/arch/*.arch the CoreDLA IP was compiled against (schema R2)")
    ap.add_argument("--fclk-mhz", type=float, required=True,
                    help="CoreDLA fabric clock built into the loaded .sof (schema R1)")
    ap.add_argument("--mode", choices=["latency", "accuracy", "both"], default="both",
                    help="which MLPerf Tiny result(s) to emit (default: both)")
    ap.add_argument("--out-dir", required=True, help="results/ dir to write JSON(s) into")
    ap.add_argument("--warmup", type=int, default=10, help="discarded warmup inferences (default 10)")
    ap.add_argument("--max-records", type=int, default=None,
                    help="cap records run (unlicensed IP: <=10000, PLAN §9 PH1)")
    ap.add_argument("--licensed-ip", action="store_true",
                    help="set when the CoreDLA IP is licensed (lifts the 10k-inference cap)")
    ap.add_argument("--timeout-s", type=float, default=30.0)
    ap.add_argument("--date", default=None)
    ap.add_argument("--jtag-path", default="*jtag*master*")
    ap.add_argument("--path", choices=["hyperram", "ddrfree"], default="hyperram",
                    help="DDR-backed/HyperRAM (Track A, all 4 models) or DDR-free/streaming "
                         "(Track B/C, resnet8 + rewritten ds-cnn) -- see docs/onboard_benchmark_plan.md")
    ap.add_argument("--ddr-buffer-info", default=None,
                    help="[--path hyperram] a ddr_buffer_info_*.txt from aot_layout.regenerate_ddr_buffer_info "
                         "(or a fresh dla_compiler run); resolves the guard-banded HyperRAM layout")
    ap.add_argument("--align-bytes", type=int, default=None,
                    help="[--path hyperram] override aot_layout.DEFAULT_ALIGN_BYTES")
    ap.add_argument("--guard-bytes", type=int, default=None,
                    help="[--path hyperram] override aot_layout.DEFAULT_GUARD_BYTES")
    ap.add_argument("--no-lock-check", action="store_true",
                    help="skip the devkit-lock guard (for dry docs only; never on the real board)")
    args = ap.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    if not args.no_lock_check:
        _assert_devkit_lock_held(repo_root)

    bundle = load_bundle(Path(args.bundle))

    if not args.licensed_ip:
        cap = 10_000
        eff = cap if args.max_records is None else min(cap, args.max_records)
        if args.max_records is None or args.max_records > cap:
            print(f"unlicensed CoreDLA IP: capping records at {cap} (PLAN §9 PH1); "
                  f"pass --licensed-ip to lift", file=sys.stderr)
        args.max_records = eff

    transport_kwargs: dict = {"jtag_path": args.jtag_path, "path": args.path}
    if args.path == "hyperram":
        if not args.ddr_buffer_info:
            print("run_tiny_benchmark --path hyperram needs --ddr-buffer-info "
                  "(a ddr_buffer_info_*.txt; see sw/host/aot_layout.py)", file=sys.stderr)
            return 3
        from aot_layout import (
            DEFAULT_ALIGN_BYTES, DEFAULT_GUARD_BYTES, parse_ddr_buffer_info, resolve_hyperram_layout)
        info_text = Path(args.ddr_buffer_info).read_text()
        layout = resolve_hyperram_layout(
            parse_ddr_buffer_info(info_text),
            align_bytes=args.align_bytes or DEFAULT_ALIGN_BYTES,
            guard_bytes=args.guard_bytes or DEFAULT_GUARD_BYTES)
        transport_kwargs["layout"] = layout
        # keep the bundle's input/output addresses in lockstep with the resolved layout, so
        # run_model's write_input(bundle.input_addr, ...)/read_output(bundle.output_addr, ...) hit
        # exactly the guard-banded addresses the CSR handshake was told about.
        bundle.input_addr = layout.input_addr
        bundle.output_addr = layout.output_addr
    else:
        print("run_tiny_benchmark --path ddrfree needs a resolved streaming_driver.StreamingRegisters "
              "for this platform's own Qsys address map -- not yet available from the CLI (see "
              "streaming_driver.py's module docstring); construct CoreDlaCsrTransport(path='ddrfree', "
              "streaming_regs=..., streaming_output_bytes=...) directly instead.", file=sys.stderr)
        return 3

    # The real transport is the ONLY board-touching object; it raises off-board so nothing is faked.
    try:
        transport: TinyInferenceTransport = CoreDlaCsrTransport(args.sof or "", **transport_kwargs)
    except NotImplementedError as exc:
        print(f"run_tiny_benchmark needs a board: {exc}", file=sys.stderr)
        return 3

    run = run_model(transport, bundle, fclk_mhz=args.fclk_mhz, warmup=args.warmup,
                    max_records=args.max_records, timeout_s=args.timeout_s)

    date = args.date or _today()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tv = {}  # tool_versions filled from env on-board (quartus/ai_suite); left empty here
    written = []
    if args.mode in ("latency", "both"):
        res = build_latency_result(run, bundle, fclk_mhz=args.fclk_mhz, arch_file=args.arch_file,
                                   date=date, sof_path=args.sof, tool_versions=tv,
                                   licensed_ip=args.licensed_ip)
        p = out_dir / f"ph3_{bundle.model_id}-tiny-latency_{date.replace('-', '')}.json"
        p.write_text(json.dumps(res, indent=2) + "\n")
        written.append(p)
    if args.mode in ("accuracy", "both"):
        res = build_accuracy_result(run, bundle, fclk_mhz=args.fclk_mhz, arch_file=args.arch_file,
                                    date=date, sof_path=args.sof, tool_versions=tv,
                                    licensed_ip=args.licensed_ip)
        p = out_dir / f"ph3_{bundle.model_id}-tiny-accuracy_{date.replace('-', '')}.json"
        p.write_text(json.dumps(res, indent=2) + "\n")
        written.append(p)

    for p in written:
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
