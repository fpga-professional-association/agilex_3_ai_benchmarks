#!/usr/bin/env bash
# =============================================================================================
# Reproducible DDR-free AGX3 CoreDLA PLATFORM build for the AXC3000 (device A3CY100BM16AE7S).
#
# Turns a right-sized DDR-free CoreDLA arch + a model into a programmable, board-pinned .sof
# (JTAG-hostless: jtag_to_avalon CSR + msgDMA streaming, no PCIe/HPS/DDR) using Altera's
# `dla_build_example_design.py`, with our axc3000_ddrfree/ + ddrfree_common/ directories
# bind-mounted over $COREDLA_ROOT/platform/{axc3000_ddrfree,ddrfree_common} so the tool
# discovers "axc3000_ddrfree" as a first-class example design. See docs/ddrfree_platform_findings.md.
#
# Usage:
#   ARCH=arch/AGX3_Ddrfree_Fit_dscnn.arch MODEL=quartus/coredla_hyperram_ed/ip/models/ds-cnn-kws/ds-cnn-kws.xml \
#     OUT=out_dscnn scripts_or_here/build_platform.sh            # full build+compile
#   ... build_platform.sh --build-only                            # IP-gen + qsys-generate only, no quartus_sh compile
#
# STRICT: never programs the board (quartus_sh --flow compile only; no quartus_pgm/jtagconfig).
# =============================================================================================
set -euo pipefail

PLATFORM_DIR="$(cd "$(dirname "$0")" && pwd)"              # quartus/coredla_agx3_ddrfree/platform
PROJ="$(cd "$PLATFORM_DIR/.." && pwd)"                     # quartus/coredla_agx3_ddrfree
REPO="${COREDLA_BENCH_REPO:-/home/tcovert/projects/agilex_3_ai_benchmarks}"
OPENVINO="${AI_SUITE_OPENVINO_DIR:-$HOME/openvino_2025.4.0}"
IMAGE="alterafpga/fpgaaisuite:2026.1.1-quartus"

ARCH="${ARCH:-arch/AGX3_Ddrfree_Fit.arch}"                 # relative to $PROJ
MODEL="${MODEL:-models/scratch/ir/resnet8_nchw/int8/resnet8-cifar10.xml}"  # relative to $REPO
OUT="${OUT:-out}"                                          # relative to $PLATFORM_DIR/build
MODE="${1:-full}"                                          # full | --build-only

DLA=/opt/altera/fpga_ai_suite/ubuntu/dla

docker_run() {
  docker run --rm -i --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$REPO:/workspace" \
    -v "$PROJ:/proj" \
    -v "$OPENVINO:/opt/intel/openvino:ro" \
    -v "$PLATFORM_DIR/ddrfree_common:$DLA/platform/ddrfree_common" \
    -v "$PLATFORM_DIR/axc3000_ddrfree:$DLA/platform/axc3000_ddrfree" \
    -v /dev/null:/usr/lib/x86_64-linux-gnu/libudev.so.1 \
    "$IMAGE" bash -lc "$1"
}

mkdir -p "$PLATFORM_DIR/build"

echo "== [1/2] dla_build_example_design.py build (IP-gen + qsys system, no Quartus) =="
docker_run "
  source /opt/intel/openvino/setupvars.sh >/dev/null
  source $DLA/setupvars.sh >/dev/null
  cd /proj/platform/build
  dla_build_example_design.py build axc3000_ddrfree /proj/${ARCH} \
    --model /workspace/${MODEL} \
    -o ${OUT} --skip-compile -f
"

if [ "$MODE" = "--build-only" ]; then
  echo "Done (--build-only). Project at $PLATFORM_DIR/build/${OUT}/hw"
  exit 0
fi

echo "== [2/2] dla_build_example_design.py quartus-compile (real synth+fit+STA+asm) =="
docker_run "
  source /opt/intel/openvino/setupvars.sh >/dev/null
  source $DLA/setupvars.sh >/dev/null
  cd /proj/platform/build
  dla_build_example_design.py quartus-compile ${OUT}
"

echo
echo "Done. Reports: $PLATFORM_DIR/build/${OUT}/hw/output_files/ (top.fit.summary, top.sta.rpt)"
echo "Bitstream (DO NOT PROGRAM -- board owned by orchestrator): $PLATFORM_DIR/build/${OUT}/hw/output_files/top.sof"
