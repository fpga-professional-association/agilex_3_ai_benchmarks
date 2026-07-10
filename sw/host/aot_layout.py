#!/usr/bin/env python3
"""Resolve the CoreDLA .aot -> HyperRAM DDR memory layout (Track DRV, crux investigation).

This module answers "what bytes go where for an inference" for the DDR-backed / HyperRAM path.
It is NOT a guess: every field comes from the real FPGA AI Suite 2026.1.1 vendor compiler, read
directly (no hardware, no JTAG capture needed) --

WHY WE DON'T HAND-PARSE THE .aot's BYTES DIRECTLY
--------------------------------------------------
The .aot the compiler emits with the (default / Track M) `--foutput-format open_vino_hetero
--fplugin HETERO:FPGA` is an OpenVINO **HETERO-plugin export blob**: a `"HETERO:FPGA\\0"` device tag
followed by the HETERO plugin's own XML execution-plan document, itself followed by the per-device
sub-blobs (verified by hex-dumping a real compiled .aot -- see docs/coredla_inference_driver.md).
Only *inside* that HETERO envelope, at an offset this repo cannot determine without linking OpenVINO
Core + the closed-source `libcoreDLAHeteroPlugin.so`/`libcoreDLAAotPlugin.so`, does the actual
`dla::CompiledResult` (defined in `$COREDLA_ROOT/compiled_result/inc/compiled_result.h`, serialized
with the `alpaca` library per `compiled_result_reader_writer.cpp`) begin. Reimplementing alpaca's
wire format around an undetermined inner offset would be exactly the kind of fabricated-looking
number AGENTS.md forbids, so this module does not do that.

Instead it uses a file the SAME compiler invocation **already writes, in plain text, with byte-exact
values**, for every compile (no special flag needed -- confirmed by re-running the exact Track M
`dla_compiler` command from `quartus/coredla_hyperram_ed/ip/README.md` and inspecting `--dumpdir`):

    <dumpdir>/<subgraph>/ddr_buffer_info_<subgraph>_0.txt

e.g. (real content, resnet8-cifar10, re-compiled fresh against the same
`quartus/coredla_hyperram_ed/ip/models/resnet8-cifar10/resnet8-cifar10.xml` +
`models/arch/AGX3_Performance.arch` this repo already used for the committed .aot):

    inputOutputBuffer size: 33280
        Inputs:
            input_1: offset 0, size: 32768
        Output: offset 32768, size: 512

    configFilterBuffer size: 208896
         Config: offset 0, size: 22528
         Filter: offset 22528, size: 186368
         Bias+Scale: offset 208896, size: 0

    interBuffer size: 0

These are the exact byte counts `runtime/coredla_device/src/device_memory_allocator.cpp` and
`runtime/dla_aot_splitter/dla_aot_splitter_example/src/main.cpp` (the vendor's own minimal
"run one inference from an .aot without OpenVINO" reference host) use to size and place the DDR
buffers -- see the module docstring in `coredla_csr_handshake.py` and
`docs/coredla_inference_driver.md` for the full citation trail:

  - "config immediately followed by filter (then bias+scale)" is ALREADY true inside this one
    `configFilterBuffer` (matches `device_memory_allocator.cpp`'s "filter must come immediately
    after config" comment and `compiled_result_t::config_filter_bias_scale_array`, which holds them
    concatenated in one bank for the non-DDR-free case).
  - "output immediately after input" is ALREADY true inside this one `inputOutputBuffer`.
  - `Config size` (bytes) is exactly what `InferenceJob.total_config_bytes`/`config_range_minus_two()`
    (`coredla_csr_handshake.py`) needs for `CONFIG_RANGE_MINUS_TWO` -- NOT the whole
    `configFilterBuffer size` (that would double the range and read past the config words into the
    filter as "config").

WHAT THIS MODULE ADDS ON TOP
-----------------------------
1. `parse_ddr_buffer_info()` -- a pure regex parser for the text above (unit-tested against real
   captured samples from all four MLPerf Tiny models, see tests/test_aot_layout.py). No board, no
   Docker, no OpenVINO needed to run these tests.
2. `resolve_hyperram_layout()` -- turns the parsed sizes into concrete, GUARD-BANDED HyperRAM byte
   addresses (PLAN's write-wound law, `docs/coredla_hyperram_onboard_findings.md` §7: "an abutting
   write wounds the 4 words below its base" -- confirmed live on silicon). Every address this
   function hands back is a byte address the host will WRITE to (config+filter blob, input tensor);
   each such write base gets >= `guard_bytes` (default 512 B, i.e. 32 dead words, comfortably above
   the observed 4-word/16-byte wound zone and matching the vendor's own `kMsgDMAMaxBurstBytes = 512`
   burst-alignment granule, `util/inc/dla_aligned_allocator.h`) of TRULY DEAD space below it. The
   hardware's OWN output write (input immediately followed by output, zero gap) is exempt from this
   guard by design -- that abutment is the vendor's allocator CONTRACT, not a host write, and by the
   time it happens the host has already consumed the input, so a wounded input tail is harmless.
3. `build_inference_job()` -- wires the resolved layout straight into
   `coredla_csr_handshake.InferenceJob`.
4. `regenerate_ddr_buffer_info()` + a CLI -- the (Docker/tool-dependent, NOT unit-tested) helper that
   actually invokes `dla_compiler` via `scripts/env.sh` to produce the dump file for a given model,
   so the orchestrator can resolve a fresh layout for any of the four `.xml`/`.bin` IRs without this
   repo having to guess or hand-maintain per-model constants.

See docs/coredla_inference_driver.md for the full write-up, citations, and the exact orchestrator
invocation.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Vendor constant: msgDMA max burst transfer size in bytes (util/inc/dla_aligned_allocator.h,
# `kMsgDMAMaxBurstBytes = 512`). Used here as both the default alignment granule AND the default
# guard-band size between HyperRAM regions -- comfortably larger than the observed 4-word (16-byte)
# write-wound zone (docs/coredla_hyperram_onboard_findings.md §7).
DEFAULT_ALIGN_BYTES = 512
DEFAULT_GUARD_BYTES = 512

_INT_RE = r"(\d+)"
# Matches lines like:
#   input_1: offset 0, size: 32768
#   Output: offset 32768, size: 512
#   Config: offset 0, size: 22528
#   Bias+Scale: offset 564224, size: 0
# name characters: word chars plus '+' (for "Bias+Scale").
_REGION_LINE_RE = re.compile(
    r"^\s*([\w+]+):\s*offset\s*" + _INT_RE + r",\s*size:\s*" + _INT_RE + r"\s*$", re.MULTILINE)
_SUMMARY_RE = {
    "input_output_buffer_size": re.compile(r"inputOutputBuffer size:\s*(\d+)"),
    "config_filter_buffer_size": re.compile(r"configFilterBuffer size:\s*(\d+)"),
    "inter_buffer_size": re.compile(r"interBuffer size:\s*(\d+)"),
}
_RESERVED_NAMES = {"Output", "Config", "Filter", "Bias+Scale"}


@dataclass
class TensorRegion:
    name: str
    offset: int
    size: int


@dataclass
class DdrBufferLayout:
    """One model's byte-exact DDR buffer layout, as reported by `dla_compiler` itself."""

    input_output_buffer_size: int
    inputs: list[TensorRegion]
    output: TensorRegion
    config_filter_buffer_size: int
    config: TensorRegion
    filter: TensorRegion
    bias_scale: TensorRegion
    inter_buffer_size: int

    def single_input(self) -> TensorRegion:
        """Convenience for the (all four MLPerf Tiny models) single-input case."""
        if len(self.inputs) != 1:
            raise ValueError(
                f"expected exactly one input tensor, found {len(self.inputs)}: {self.inputs} "
                "-- multi-input graphs need a caller that picks the right one explicitly")
        return self.inputs[0]


def parse_ddr_buffer_info(text: str) -> DdrBufferLayout:
    """Parse a `ddr_buffer_info_<subgraph>_0.txt` (dla_compiler's own DDR layout dump).

    Pure text parsing -- no board, no Docker, no OpenVINO. See module docstring for provenance and
    tests/test_aot_layout.py for real captured samples (all four MLPerf Tiny models).
    """
    summary: dict[str, int] = {}
    for key, rx in _SUMMARY_RE.items():
        m = rx.search(text)
        if not m:
            raise ValueError(f"ddr_buffer_info text missing {key!r} line (got: {text[:200]!r}...)")
        summary[key] = int(m.group(1))

    regions: dict[str, list[TensorRegion]] = {}
    for m in _REGION_LINE_RE.finditer(text):
        name, offset, size = m.group(1), int(m.group(2)), int(m.group(3))
        regions.setdefault(name, []).append(TensorRegion(name, offset, size))

    def one(name: str) -> TensorRegion:
        found = regions.get(name)
        if not found:
            raise ValueError(f"ddr_buffer_info text missing a {name!r} region line")
        if len(found) != 1:
            raise ValueError(f"expected exactly one {name!r} region line, found {len(found)}")
        return found[0]

    output = one("Output")
    config = one("Config")
    filter_ = one("Filter")
    bias_scale = one("Bias+Scale")
    inputs = [r for name, rs in regions.items() if name not in _RESERVED_NAMES for r in rs]
    if not inputs:
        raise ValueError("ddr_buffer_info text has no input tensor region (only reserved names found)")

    return DdrBufferLayout(
        input_output_buffer_size=summary["input_output_buffer_size"],
        inputs=inputs,
        output=output,
        config_filter_buffer_size=summary["config_filter_buffer_size"],
        config=config,
        filter=filter_,
        bias_scale=bias_scale,
        inter_buffer_size=summary["inter_buffer_size"],
    )


@dataclass
class HyperRamLayout:
    """Concrete, guard-banded HyperRAM byte addresses ready for the CSR handshake.

    `config_base_addr`/`total_config_bytes` and `input_addr`/`intermediate_addr` map 1:1 onto
    `coredla_csr_handshake.InferenceJob` -- see `build_inference_job()`.
    """

    intermediate_addr: int
    intermediate_reserved_bytes: int
    config_base_addr: int
    total_config_bytes: int           # Config-only bytes -- what CONFIG_RANGE_MINUS_TWO wants
    config_filter_write_bytes: int    # Config+Filter+Bias/Scale -- the ONE blob the host writes
    input_addr: int                  # also INPUT_OUTPUT_BASE_ADDR's value (the GO trigger)
    input_bytes: int
    output_addr: int                 # input_addr + output.offset (immediately after input)
    output_bytes: int
    end_addr: int                    # first byte address after everything this layout reserves
    align_bytes: int
    guard_bytes: int


def _round_up(n: int, align: int) -> int:
    if align <= 0:
        return n
    return ((n + align - 1) // align) * align


def resolve_hyperram_layout(layout: DdrBufferLayout, *, base_addr: int = 0,
                            align_bytes: int = DEFAULT_ALIGN_BYTES,
                            guard_bytes: int = DEFAULT_GUARD_BYTES) -> HyperRamLayout:
    """Place intermediate / config+filter / input+output into HyperRAM, low to high, with a dead
    guard band before every HOST write base (config_base_addr, input_addr) -- see module docstring
    for why the hardware's own input->output abutment does NOT need the same guard.
    """
    if align_bytes <= 0 or guard_bytes < 0:
        raise ValueError("align_bytes must be positive and guard_bytes must be non-negative")

    cursor = base_addr

    # 1. Intermediate scratch (device-only; host never writes data here, just tells the CSR the
    #    base address). May be zero-sized (several of the four models have no DDR feature spill).
    intermediate_addr = _round_up(cursor, align_bytes)
    intermediate_reserved = _round_up(layout.inter_buffer_size, align_bytes) if layout.inter_buffer_size else 0
    cursor = intermediate_addr + intermediate_reserved + guard_bytes

    # 2. Config+Filter(+Bias/Scale) -- ONE host write, guard-banded below its base.
    config_base_addr = _round_up(cursor, align_bytes)
    cursor = config_base_addr + _round_up(layout.config_filter_buffer_size, align_bytes) + guard_bytes

    # 3. Input+Output -- ONE region; host writes only the input bytes, hardware writes the output
    #    immediately after (allocator contract, not guard-banded -- see docstring).
    input_addr = _round_up(cursor, align_bytes)
    single_input = layout.single_input()
    output_addr = input_addr + layout.output.offset
    end_addr = input_addr + _round_up(layout.input_output_buffer_size, align_bytes)

    return HyperRamLayout(
        intermediate_addr=intermediate_addr,
        intermediate_reserved_bytes=intermediate_reserved,
        config_base_addr=config_base_addr,
        total_config_bytes=layout.config.size,
        config_filter_write_bytes=layout.config_filter_buffer_size,
        input_addr=input_addr,
        input_bytes=single_input.size,
        output_addr=output_addr,
        output_bytes=layout.output.size,
        end_addr=end_addr,
        align_bytes=align_bytes,
        guard_bytes=guard_bytes,
    )


def build_inference_job(hy: HyperRamLayout):
    """Build a `coredla_csr_handshake.InferenceJob` from a resolved `HyperRamLayout`."""
    # Local import: keeps this module importable standalone (e.g. from the CLI below) without
    # requiring coredla_csr_handshake's smoke_infer-optional import machinery to have run yet.
    from coredla_csr_handshake import InferenceJob

    return InferenceJob(
        config_base_addr=hy.config_base_addr,
        total_config_bytes=hy.total_config_bytes,
        input_addr=hy.input_addr,
        intermediate_addr=hy.intermediate_addr,
    )


# =================================================================================================
# dla_compiler-invoking helper (Docker/tool-dependent -- NOT unit-tested; see
# docs/coredla_inference_driver.md for the exact orchestrator command this wraps).
# =================================================================================================
class LayoutRegenerationError(RuntimeError):
    """dla_compiler failed, or didn't produce a ddr_buffer_info file -- never guessed around."""


def regenerate_ddr_buffer_info(model_xml: Path, arch_file: Path, dumpdir: Path, *,
                               repo_root: Path | None = None,
                               march_flag_is_relative: bool = True) -> Path:  # pragma: no cover
    """Invoke the real `dla_compiler` (via `scripts/env.sh`'s Docker wrapper) to (re)compile
    `model_xml` against `arch_file` and return the path to the `ddr_buffer_info_*.txt` it writes
    under `dumpdir`. This is the SAME command Track M used to produce the committed `.aot`s
    (`quartus/coredla_hyperram_ed/ip/README.md` §2), so it is byte-for-byte reproducible, not a
    fresh/independent estimate.

    Requires: the AI Suite Docker image (`scripts/env.sh`), run from `repo_root` (defaults to the
    repo root inferred from this file's location). NOT exercised by pytest -- Docker/tool dependent,
    matches this repo's convention of isolating hardware/tool-dependent glue behind a thin function
    (AGENTS.md "Python conventions").
    """
    repo_root = repo_root or Path(__file__).resolve().parents[2]
    dumpdir = Path(dumpdir)
    dumpdir.mkdir(parents=True, exist_ok=True)
    model_xml = Path(model_xml)
    arch_file = Path(arch_file)

    def rel(p: Path) -> str:
        try:
            return str(p.resolve().relative_to(repo_root))
        except ValueError:
            return str(p)  # already relative, or genuinely outside the repo (e.g. /tmp) -- best effort

    cmd = (
        "source scripts/env.sh >/dev/null 2>&1 && dla_compiler "
        f"--march {rel(arch_file)} --network-file {rel(model_xml)} "
        "--foutput-format open_vino_hetero --fplugin HETERO:FPGA "
        f"--o {rel(dumpdir)}/out.aot --dumpdir {rel(dumpdir)} --overwrite-output-files"
    )
    proc = subprocess.run(["bash", "-lc", cmd], cwd=repo_root, capture_output=True, text=True)
    if proc.returncode != 0:
        raise LayoutRegenerationError(
            f"dla_compiler failed (exit {proc.returncode}) for {model_xml}:\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")

    matches = sorted(dumpdir.rglob("ddr_buffer_info_*.txt"))
    if not matches:
        raise LayoutRegenerationError(
            f"dla_compiler exited 0 but produced no ddr_buffer_info_*.txt under {dumpdir} "
            f"(stdout tail: {proc.stdout[-500:]!r})")
    if len(matches) > 1:
        raise LayoutRegenerationError(
            f"expected exactly one ddr_buffer_info_*.txt under {dumpdir}, found {len(matches)}: "
            f"{matches} -- pass a per-model dumpdir")
    return matches[0]


def resolve_model_layout(model_xml: Path, arch_file: Path, dumpdir: Path, *,
                         repo_root: Path | None = None,
                         align_bytes: int = DEFAULT_ALIGN_BYTES,
                         guard_bytes: int = DEFAULT_GUARD_BYTES) -> HyperRamLayout:  # pragma: no cover
    """End-to-end: regenerate the dump, parse it, resolve guard-banded HyperRAM addresses."""
    info_path = regenerate_ddr_buffer_info(model_xml, arch_file, dumpdir, repo_root=repo_root)
    layout = parse_ddr_buffer_info(info_path.read_text())
    return resolve_hyperram_layout(layout, align_bytes=align_bytes, guard_bytes=guard_bytes)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Needs scripts/env.sh's AI Suite Docker image; see docs/coredla_inference_driver.md.")
    ap.add_argument("--model-xml", type=Path, default=None, help="CoreDLA-compilable IR .xml")
    ap.add_argument("--arch", type=Path, default=None, help="models/arch/*.arch used to compile")
    ap.add_argument("--dumpdir", type=Path, default=None,
                    help="scratch dir for dla_compiler --dumpdir (per-model; gitignored)")
    ap.add_argument("--align-bytes", type=int, default=DEFAULT_ALIGN_BYTES)
    ap.add_argument("--guard-bytes", type=int, default=DEFAULT_GUARD_BYTES)
    ap.add_argument("--from-info-file", type=Path, default=None,
                    help="skip regeneration; parse an already-produced ddr_buffer_info_*.txt")
    args = ap.parse_args(argv)

    if args.from_info_file is not None:
        layout = parse_ddr_buffer_info(args.from_info_file.read_text())
        hy = resolve_hyperram_layout(layout, align_bytes=args.align_bytes, guard_bytes=args.guard_bytes)
    else:
        if not (args.model_xml and args.arch and args.dumpdir):
            ap.error("--model-xml/--arch/--dumpdir are required unless --from-info-file is given")
        hy = resolve_model_layout(args.model_xml, args.arch, args.dumpdir,
                                  align_bytes=args.align_bytes, guard_bytes=args.guard_bytes)

    print(json.dumps(hy.__dict__, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
