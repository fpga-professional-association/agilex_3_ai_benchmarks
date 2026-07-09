#!/usr/bin/env bash
# =============================================================================================
# Reproducible DDR-free AGX3 CoreDLA build for the AXC3000 (device A3CY100BM16AE7S).
#
# Single-command build (no board needed — CPU-only synth/fit/timing):
#     scripts/build.sh              # full compile (synth + fit + timing + asm)
#     scripts/build.sh --synth      # analysis & synthesis only (fast smoke)
#     DEVICE=A3CY135BM16AE6S scripts/build.sh   # override target device
#
# What it does, all inside the FPGA-AI-Suite+Quartus docker image (env.sh is repo-mounted only,
# so this drives docker directly and mounts BOTH the main repo and this worktree):
#   1. dla_create_ip  : generate the AGX3 DDR-free CoreDLA IP (RTL + on-chip weight MIF ROMs)
#                       from arch/AGX3_Ddrfree.arch and a fixed TinyML model (resnet8, INT8).
#   2. compile_ip prep: instantiate the IP in Altera's dla_top_quartus_wrapper (area/fmax
#                       harness — 5 clock/reset ports, everything else internal logic) and
#                       write top.qsf targeting the AXC3000 device.
#   3. quartus_sh --flow compile : real Quartus synth + fit + STA + asm -> build/output_files/top.sof
#
# There is NO shipped AGX3 DDR-free example design (only AGX5/AGX7). This arch + flow is
# hand-authored; see docs/coredla_agx3_build_findings.md.
# =============================================================================================
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"          # quartus/coredla_agx3_ddrfree
REPO="${COREDLA_BENCH_REPO:-/home/tcovert/projects/agilex_3_ai_benchmarks}"
OPENVINO="${AI_SUITE_OPENVINO_DIR:-$HOME/openvino_2025.4.0}"
IMAGE="alterafpga/fpgaaisuite:2026.1.1-quartus"

DEVICE="${DEVICE:-A3CY100BM16AE7S}"               # AXC3000 (Agilex 3 C-series 100, M16A)
FAMILY_STR="Agilex 3"
ARCH_NAME="${ARCH_NAME:-AGX3_Ddrfree}"            # arch/${ARCH_NAME}.arch ; IP dir suffix _AGX3
                                                  # (e.g. ARCH_NAME=AGX3_Ddrfree_Fit for the C100-fit variant)
MODEL="${MODEL:-models/scratch/ir/resnet8_nchw/int8/resnet8-cifar10.xml}"  # repo-relative
MODE="${1:-compile}"

# Container paths
C_PROJ=/proj
C_REPO=/workspace
DLA=/opt/altera/fpga_ai_suite/ubuntu/dla

docker_run() {
  docker run --rm -i --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$REPO:$C_REPO" \
    -v "$PROJ:$C_PROJ" \
    -v "$OPENVINO:/opt/intel/openvino:ro" \
    -v /dev/null:/usr/lib/x86_64-linux-gnu/libudev.so.1 \
    "$IMAGE" bash -lc "$1"
}

echo "== [1/3] Generate AGX3 DDR-free CoreDLA IP =="
if [ ! -e "$PROJ/coredla_ip/altera_ai_ip/verilog/${ARCH_NAME}_AGX3/dla_ip.qsf" ] || [ "${FORCE_IP:-0}" = "1" ]; then
  docker_run "
    source /opt/intel/openvino/setupvars.sh >/dev/null
    source $DLA/setupvars.sh >/dev/null
    dla_create_ip \
      --arch $C_PROJ/arch/${ARCH_NAME}.arch \
      --model $C_REPO/${MODEL} \
      --ip-dir $C_PROJ/coredla_ip \
      --skip-sim-env --overwrite"
else
  echo "   coredla_ip already present (set FORCE_IP=1 to regenerate); skipping."
fi

echo "== [2/3] Prepare standalone Quartus QoR project (device $DEVICE) =="
BUILD="$PROJ/build"
rm -rf "$BUILD"; mkdir -p "$BUILD"
docker_run "
  source $DLA/setupvars.sh >/dev/null
  Q=$DLA/fpga/top/quartus
  WRAP=dla_top_wrapper_${ARCH_NAME}_AGX3
  # (2a) area/fmax wrapper: substitute the concrete IP wrapper name into Altera's template
  sed -e \"s/\\\$REPLACE_WRAPPER/\$WRAP/g\" \$Q/dla_top_quartus_wrapper_sv.template \
      > $C_PROJ/build/dla_top_quartus_wrapper.sv
  # (2b) top.qsf: point at our IP, force the AXC3000 device (template ships the 135 dev-kit part)
  REL=\$(realpath --relative-to=$C_PROJ/build $C_PROJ/coredla_ip)
  sed -e \"s!\\\$REPLACE_ARCH!${ARCH_NAME}_AGX3!g\" \
      -e \"s!\\\$REPLACE_IPDIR!\$REL!g\" \
      -e \"s!A3CY135BM16AE6S!$DEVICE!g\" \
      \$Q/AGX3/top_qsf.template > $C_PROJ/build/top.qsf
  cp \$Q/top.qpf $C_PROJ/build/top.qpf
  cp \$Q/dla_top_quartus_wrapper_clocks.sdc $C_PROJ/build/dla_top_quartus_wrapper_clocks.sdc
  echo 'Prepared top.qsf:'; grep -i 'DEVICE\|FAMILY' $C_PROJ/build/top.qsf"

echo "== [3/3] Quartus $MODE =="
if [ "$MODE" = "--synth" ] || [ "$MODE" = "synth" ]; then
  docker_run "source $DLA/setupvars.sh >/dev/null; cd $C_PROJ/build && quartus_syn --analysis_and_elaboration top"
else
  docker_run "source $DLA/setupvars.sh >/dev/null; cd $C_PROJ/build && quartus_sh --flow compile top"
fi

echo
echo "Done. Reports: $PROJ/build/output_files/  (top.fit.rpt, top.sta.rpt)"
echo "Bitstream (DO NOT PROGRAM — board is owned by another agent): $PROJ/build/output_files/top.sof"
