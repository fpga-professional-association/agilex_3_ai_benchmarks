#!/usr/bin/env bash
# board_session.sh — self-recovering wrapper for DDR-free on-board runs (AXC3000).
# Runs INSIDE the privileged fpgaaisuite container. Usage:
#   board_session.sh <out_dir> <img.bin> <ninf> [more triplets...]
# For each triplet: cd out_dir, run the functional flow (design_load first on the FIRST run
# or after any recovery). Recovers from SLD-hub wedges: quick liveness probe with timeout;
# on hang -> full reprogram (clears hub); on pgm failure -> caller must usb-cycle (exit 9).
set -u
export PATH=/opt/altera/syscon/bin:/opt/altera/quartus/bin:$PATH
SOF=/workspace/scratch/ddrfree_run/top_ddrfree_resnet8_nofoldcfg.sof

start_jtagd() { killall jtagd 2>/dev/null; sleep 3; jtagd; sleep 4; }

liveness() {
  # 45-s bounded service-discovery probe
  cat > /tmp/live.tcl <<'EOF'
puts "LIVE: enumerating"
set m [get_service_paths master]
puts "LIVE: masters=[llength $m]"
EOF
  timeout 45 system-console --cli --script=/tmp/live.tcl 2>&1 | grep -q "LIVE: masters=" && return 0 || return 1
}

recover() {
  echo "RECOVER: reprogramming to clear SLD hub"
  local out
  out=$(timeout 120 quartus_pgm -c 1 -m jtag -o "p;$SOF" 2>&1)
  echo "$out" | grep -E "succeeded|Error \(" | head -2
  if ! echo "$out" | grep -q succeeded; then
    echo "RECOVER: pgm failed — needs usb cycle"; return 9
  fi
  sleep 3
  liveness && return 0 || return 1
}

start_jtagd
if ! liveness; then
  start_jtagd
  liveness || { recover || exit 9; }
fi
echo "SESSION: services alive"

NEED_DL=1
while [ $# -ge 3 ]; do
  OUT=$1; IMG=$2; NINF=$3; shift 3
  mkdir -p "$OUT"; cd "$OUT"
  for attempt in 1 2; do
    DLARG=""
    [ $NEED_DL -eq 1 ] && DLARG="$SOF"
    echo "RUN: $(basename $OUT) img=$(basename $IMG) ninf=$NINF dl=${DLARG:+yes} attempt=$attempt"
    DL="$DLARG" IMG="$IMG" NINF="$NINF" \
      timeout 400 system-console --cli --script=/workspace/scratch/ddrfree_run/run_functional.tcl 2>&1 | \
      grep -E "WRAPPER|Completion counter|successfully|Total active|Total core|ERROR" | sed 's/^/  /'
    if [ -f output1.bin ] && [ "$(find output1.bin -newermt '-7 minutes' | wc -l)" = "1" ]; then
      NEED_DL=0; break
    fi
    echo "  RUN FAILED — recovering"
    recover || exit 9
    NEED_DL=1
  done
done
echo "SESSION: done"
