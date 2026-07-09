#!/usr/bin/env python3
"""Drive the FPGA AI Suite performance estimator for one (model IR, arch file, memory-BW) tuple.

Issue #6 (PLAN §9 PH0): desk-check every number in the PLAN §5 capacity table using
``dla_compiler --fanalyze-performance`` before synthesizing anything, feeding it the external
memory bandwidth PLAN §9 PH0 says the 25.1+ estimator accepts as input.

Entry point discovery (see ``docs/toolchain.md``, "Performance estimator invocation"):
``dla_compiler --fanalyze-performance --march <arch> --network-file <ir.xml> --fplugin HETERO:FPGA
--foutput-format open_vino_hetero --fassumed-memory-bandwidth <MB/s> --fdump-performance-report
<name> --dumpdir <dir>``. ``--fplugin HETERO:FPGA`` (not the default ``HETERO:FPGA,CPU``) is
mandatory: NNCF-quantized (FakeQuantize) graphs only compile through a single plugin, pure
``HETERO:FPGA`` or pure ``HETERO:CPU`` -- mixed hetero is rejected for quantized graphs, so any op
the FPGA subgraph collector can't place has nowhere to fall back to and the whole compile fails
(this is how DS-CNN/ResNet-8/VWW/Tiny-YOLOv3 fail below -- see ``results/reports/ph0_estimator.md``).

Usage:
    python scripts/estimate.py --model ds-cnn-kws --arch models/arch/AGX3_Performance.arch \\
        --membw 250

Writes one ``results/ph0_<subject>_<yyyymmdd>.json`` (``kind: "estimate"``, ``level: "PH0"``)
conforming to ``results/schema/result.schema.json``. Never fabricates a number: any dla_compiler
failure, or any success whose report doesn't contain a recognized "FINAL THROUGHPUT" line, is a
hard failure (nonzero exit, no JSON written) -- see ``EstimatorError``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_IR_DIR = REPO_ROOT / "models" / "ir"
RESULTS_DIR = REPO_ROOT / "results"
SCRATCH_ROOT = REPO_ROOT / "models" / "compiled" / "_ph0_scratch"

DEVICE = "A3CY100BM16AE7S"
BOARD = "Arrow AXC3000"
PLAN_REF = "§5 table"

# Quantized (FakeQuantize) graphs are only accepted through a single, non-mixed HETERO plugin
# (see module docstring). We always want the FPGA number, never the CPU one.
FPLUGIN = "HETERO:FPGA"

FPS_RE = re.compile(r"FINAL THROUGHPUT\s*=\s*([\d.]+)\s*fps")
MIN_BW_RE = re.compile(r"MINIMUM AVERAGE DDR BANDWIDTH REQUIRED\s*=\s*([\d.]+)\s*MB/s")
TOTAL_XFER_RE = re.compile(r"TOTAL DDR TRANSFERS REQUIRED\s*=\s*([\d.]+)\s*MB")
FILTER_READS_RE = re.compile(r"DDR FILTER READS REQUIRED\s*=\s*([\d.]+)\s*MB")
FEATURE_READS_RE = re.compile(r"DDR FEATURE READS REQUIRED\s*=\s*([\d.]+)\s*MB")
FEATURE_WRITES_RE = re.compile(r"DDR FEATURE WRITES REQUIRED\s*=\s*([\d.]+)\s*MB")
SUBGRAPH_RE = re.compile(r"split into (\d+) subgraph\(s\), FPGA:(\d+)")
VERSION_RE = re.compile(r"([0-9]+\.[0-9]+\.[0-9]+(?:\+b[0-9]+)?)")
MEMORY_BOUND_MARK = "Memory Bandwidth Bottleneck Detected"


class EstimatorError(RuntimeError):
    """dla_compiler failed, or its output isn't in a recognized shape -- never guessed around."""


# --------------------------------------------------------------------------------------------
# Pure parsing (unit-testable without Docker/Quartus/the AI Suite -- see scripts/tests/).
# --------------------------------------------------------------------------------------------


def parse_performance_report(text: str) -> dict[str, Any]:
    """Parse one ``dla_compiler --fanalyze-performance`` report (``reports/perf_0.txt`` content).

    Fails loudly if the headline ``FINAL THROUGHPUT`` line is missing -- required by issue #6 step
    2 ("Parser must fail loudly on unrecognized output"). All other fields are best-effort (used
    for ``notes``, not gating): missing ones come back as ``None``.
    """
    fps_matches = FPS_RE.findall(text)
    if not fps_matches:
        raise EstimatorError(
            "could not find a 'FINAL THROUGHPUT = ... fps' line anywhere in the performance "
            "report -- estimator output format may have changed since this was written; "
            "refusing to guess a number"
        )
    # A heterogeneous (multi-subgraph) report prints one FINAL THROUGHPUT per FPGA subgraph and
    # then a final merged one for the whole graph; the merged number is always the *last* one in
    # the file. A pure-FPGA (single subgraph) report -- the only kind this project's INT8 sweep
    # ever produces, since quantized graphs can't mix HETERO:FPGA,CPU -- has exactly one match.
    fps = float(fps_matches[-1])

    def _last_float(pattern: re.Pattern[str]) -> float | None:
        matches = pattern.findall(text)
        return float(matches[-1]) if matches else None

    result: dict[str, Any] = {
        "fps": fps,
        "ddr_bandwidth_required_mbps": _last_float(MIN_BW_RE),
        "ddr_transfers_required_mb": _last_float(TOTAL_XFER_RE),
        "ddr_filter_reads_mb": _last_float(FILTER_READS_RE),
        "ddr_feature_reads_mb": _last_float(FEATURE_READS_RE),
        "ddr_feature_writes_mb": _last_float(FEATURE_WRITES_RE),
        "memory_bound": MEMORY_BOUND_MARK in text,
    }
    sub = SUBGRAPH_RE.search(text)
    if sub:
        result["n_subgraphs"] = int(sub.group(1))
        result["n_fpga_subgraphs"] = int(sub.group(2))
    return result


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_notes(parsed: dict[str, Any], *, model: str, bin_size_mb: float, precision: str) -> str:
    parts = [
        f"FPGA AI Suite performance-estimator ({FPLUGIN}) output for {model} ({precision})."
    ]
    if parsed.get("memory_bound"):
        parts.append(
            "Memory-bandwidth bottleneck reported: throughput was scaled down from the "
            "compute-only rate to match the assumed external memory bandwidth."
        )
    filt = parsed.get("ddr_filter_reads_mb")
    if filt is not None and bin_size_mb > 0:
        reread = filt / bin_size_mb
        parts.append(
            f"DDR filter (weight) reads per inference: {filt:.3f} MB vs {bin_size_mb:.3f} MB of "
            f"on-disk INT8 weights -> {reread:.2f}x re-read factor. "
            + (
                "Weights are being streamed from external memory on every inference, not folded "
                "into on-chip M20K -- this arch is not a DDR-free/MIF-resident configuration "
                "regardless of model size."
                if reread > 1.5
                else "Close to a single read per inference (roughly consistent with weights held "
                "resident and re-fetched once)."
            )
        )
    fr, fw = parsed.get("ddr_feature_reads_mb"), parsed.get("ddr_feature_writes_mb")
    if fr is not None and fw is not None:
        spill = fr + fw
        parts.append(
            f"Activation-spill traffic (DDR feature reads + writes) per inference: "
            f"{fr:.3f} + {fw:.3f} = {spill:.3f} MB -- refines PLAN §5's flat 2.5 MB spill constant "
            f"({'above' if spill > 2.5 else 'below'} it by {abs(spill - 2.5):.2f} MB)."
        )
    if "n_fpga_subgraphs" in parsed:
        parts.append(
            f"Compiled to {parsed.get('n_subgraphs')} subgraph(s), "
            f"{parsed.get('n_fpga_subgraphs')} on FPGA."
        )
    return " ".join(parts)


# --------------------------------------------------------------------------------------------
# Tool invocation (needs Docker + the licensed images from scripts/env.sh).
# --------------------------------------------------------------------------------------------


def get_ai_suite_version() -> str:
    cmd = "source scripts/env.sh >/dev/null 2>&1 && dla_compiler --version"
    proc = subprocess.run(["bash", "-c", cmd], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    m = VERSION_RE.search(proc.stdout + proc.stderr)
    if proc.returncode != 0 or not m:
        raise EstimatorError(
            f"could not determine dla_compiler --version (exit {proc.returncode}): "
            f"{(proc.stdout + proc.stderr)[-500:]}"
        )
    return m.group(1)


def run_estimator(
    model: str,
    arch: Path,
    membw: float,
    *,
    precision: str = "int8",
    keep_work_dir: bool = False,
) -> dict[str, Any]:
    """Compile+estimate one (model, arch, membw) tuple. Returns the parsed report dict.

    Raises EstimatorError (never returns a partial/guessed result) if dla_compiler exits nonzero
    or its report doesn't parse.
    """
    ir_dir = MODELS_IR_DIR / model / precision
    xml_path = ir_dir / f"{model}.xml"
    bin_path = ir_dir / f"{model}.bin"
    if not xml_path.is_file() or not bin_path.is_file():
        raise EstimatorError(
            f"no {precision} IR for model '{model}' at {ir_dir} "
            f"(expected {model}.xml + {model}.bin -- see models/README.md / sw/model_prep/)"
        )
    arch = arch if arch.is_absolute() else (REPO_ROOT / arch)
    if not arch.is_file():
        raise EstimatorError(f"architecture file not found: {arch}")

    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix=f"{model}_", dir=SCRATCH_ROOT))
    try:
        arch_rel = arch.resolve().relative_to(REPO_ROOT).as_posix()
        xml_rel = xml_path.resolve().relative_to(REPO_ROOT).as_posix()
        work_rel = work_dir.resolve().relative_to(REPO_ROOT).as_posix()

        cmd = (
            "source scripts/env.sh >/dev/null 2>&1 && dla_compiler "
            "--fanalyze-performance "
            f"--march {shlex.quote(arch_rel)} "
            f"--network-file {shlex.quote(xml_rel)} "
            "--foutput-format open_vino_hetero "
            f"--fplugin {FPLUGIN} "
            "--o out.aot "
            f"--dumpdir {shlex.quote(work_rel)} "
            "--overwrite-output-files "
            "--fdump-performance-report perf.txt "
            f"--fassumed-memory-bandwidth {membw:g}"
        )
        proc = subprocess.run(
            ["bash", "-c", cmd], cwd=REPO_ROOT, capture_output=True, text=True, timeout=900
        )
        log = proc.stdout + proc.stderr
        if proc.returncode != 0:
            raise EstimatorError(
                f"dla_compiler failed (exit {proc.returncode}) for model={model} "
                f"arch={arch.name} membw={membw:g}:\n{log[-4000:]}"
            )

        merged = sorted(work_dir.rglob("reports/perf-merged_subgraph_estimates.txt"))
        if merged:
            report_paths = merged[-1:]
        else:
            report_paths = sorted(work_dir.rglob("reports/perf_*.txt"))
            if len(report_paths) > 1:
                raise EstimatorError(
                    f"model={model} arch={arch.name} membw={membw:g} produced "
                    f"{len(report_paths)} per-subgraph reports with no merged report -- "
                    f"multi-subgraph aggregation isn't implemented here, refusing to guess: "
                    f"{report_paths}"
                )
        if not report_paths:
            raise EstimatorError(
                f"dla_compiler exited 0 but produced no reports/perf*.txt under {work_dir} "
                "-- output layout may have changed; refusing to guess"
            )
        report_text = report_paths[0].read_text()
        parsed = parse_performance_report(report_text)
        parsed["_report_path"] = str(report_paths[0].relative_to(REPO_ROOT))
        parsed["_command"] = cmd
        return parsed
    finally:
        if not keep_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


# --------------------------------------------------------------------------------------------
# Result-JSON assembly.
# --------------------------------------------------------------------------------------------


def build_estimate(
    model: str,
    arch: Path,
    membw: float,
    *,
    precision: str = "int8",
    date: str | None = None,
    keep_work_dir: bool = False,
    ai_suite_version: str | None = None,
) -> dict[str, Any]:
    ir_dir = MODELS_IR_DIR / model / precision
    xml_path = ir_dir / f"{model}.xml"
    bin_path = ir_dir / f"{model}.bin"

    parsed = run_estimator(model, arch, membw, precision=precision, keep_work_dir=keep_work_dir)

    ir_sha256 = {"xml": sha256_file(xml_path), "bin": sha256_file(bin_path)}
    manifest_path = MODELS_IR_DIR / model / "quant_manifest.json"
    tool_versions: dict[str, str] = {}
    manifest_hash_mismatch: list[str] = []
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        recorded_hash_key = f"{precision}_ir_sha256"
        recorded = manifest.get(recorded_hash_key, {})
        for fname, recorded_hex in recorded.items():
            fresh = ir_sha256["xml"] if fname.endswith(".xml") else ir_sha256["bin"]
            if recorded_hex != fresh:
                # Not fatal: models/ir/ is gitignored, regenerable scratch (issues #2/#3, both
                # still open/unmerged as of this issue -- see docs/toolchain.md and this issue's
                # PR description). Observed in practice: the on-disk IR was regenerated after
                # quant_manifest.json was last written (mtimes confirm it), so the manifest's
                # recorded hash is stale relative to the actual bytes compiled here. The IR hash
                # this result records (config.ir_sha256) is always freshly computed from the exact
                # bytes fed to dla_compiler, so the estimate itself stays traceable regardless.
                manifest_hash_mismatch.append(fname)
        tool_versions.update(manifest.get("tool_versions", {}))

    ai_suite_version = ai_suite_version or get_ai_suite_version()
    tool_versions["ai_suite"] = ai_suite_version

    bin_size_mb = bin_path.stat().st_size / (1024 * 1024)
    notes = build_notes(parsed, model=model, bin_size_mb=bin_size_mb, precision=precision)
    if manifest_hash_mismatch:
        notes += (
            f" WARNING: on-disk IR ({', '.join(manifest_hash_mismatch)}) does not match the sha256 "
            f"recorded in models/ir/{model}/quant_manifest.json (mtimes show the IR was "
            "regenerated after that manifest was written -- issue #2/#3's model_prep pipeline is "
            "still an open, unmerged, not-independently-verified PR as of this issue). "
            "config.ir_sha256 above is freshly computed from the exact bytes just compiled, so "
            "this result is still self-traceable; it just may not match that manifest snapshot."
        )

    date_str = date or dt.date.today().isoformat()
    arch_slug = arch.stem.lower().replace("_", "-")
    membw_slug = f"{membw:g}".replace(".", "p")
    subject = f"{model}-{arch_slug}-estimator-{membw_slug}mbps"

    quantization = "int8-nncf-ptq" if precision == "int8" else "fp32"

    result: dict[str, Any] = {
        "kind": "estimate",
        "level": "PH0",
        "subject": subject,
        "date": date_str,
        "plan_ref": PLAN_REF,
        "config": {
            "device": DEVICE,
            "board": BOARD,
            "arch_file": arch.resolve().relative_to(REPO_ROOT).as_posix(),
            "model": model,
            "quantization": quantization,
            "tool_versions": tool_versions,
            "memory_bandwidth_mbps": membw,
            "ir_sha256": ir_sha256,
            "estimator_invocation": parsed["_command"],
            "estimator_report_path": parsed["_report_path"],
            "estimator_fplugin": FPLUGIN,
            "quant_manifest_hash_mismatch": bool(manifest_hash_mismatch),
        },
        "metrics": {
            "fps": parsed["fps"],
            "ddr_bandwidth_required_mbps": parsed["ddr_bandwidth_required_mbps"],
            "ddr_transfers_required_mb": parsed["ddr_transfers_required_mb"],
            "ddr_filter_reads_mb": parsed["ddr_filter_reads_mb"],
            "ddr_feature_reads_mb": parsed["ddr_feature_reads_mb"],
            "ddr_feature_writes_mb": parsed["ddr_feature_writes_mb"],
            "memory_bound": parsed["memory_bound"],
        },
        "notes": notes,
    }
    return result


def default_out_path(result: dict[str, Any]) -> Path:
    subject = result["subject"]
    date_compact = result["date"].replace("-", "")
    return RESULTS_DIR / f"ph0_{subject}_{date_compact}.json"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", required=True, help="model id, matches a models/ir/<model>/ directory")
    p.add_argument(
        "--arch", required=True, type=Path,
        help="path to an FPGA AI Suite .arch file, e.g. models/arch/AGX3_Performance.arch",
    )
    p.add_argument(
        "--membw", required=True, type=float,
        help="assumed external memory bandwidth in MB/s (--fassumed-memory-bandwidth)",
    )
    p.add_argument(
        "--precision", default="int8", choices=["int8", "fp32"],
        help="which models/ir/<model>/<precision>/ IR to compile (default: int8)",
    )
    p.add_argument("--out", type=Path, default=None, help="output JSON path (default: results/ph0_<subject>_<date>.json)")
    p.add_argument("--date", default=None, help="override result date YYYY-MM-DD (default: today; for deterministic tests)")
    p.add_argument(
        "--keep-work-dir", action="store_true",
        help="keep the dla_compiler scratch/dump dir under models/compiled/_ph0_scratch/ (gitignored) instead of deleting it",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        result = build_estimate(
            args.model, args.arch, args.membw,
            precision=args.precision, date=args.date, keep_work_dir=args.keep_work_dir,
        )
    except EstimatorError as exc:
        print(f"estimate.py: {exc}", file=sys.stderr)
        return 1

    out_path = args.out or default_out_path(result)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, sort_keys=False) + "\n")
    print(f"wrote {out_path}  (fps={result['metrics']['fps']:.6g})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
