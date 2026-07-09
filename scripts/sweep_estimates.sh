#!/usr/bin/env bash
# scripts/sweep_estimates.sh (#6) — full PH0 estimator sweep: 7 models x committed AGX3 arch
# files x memory BW in {200, 250, 333, 400} MB/s.
#
# Every combo is attempted; failures are logged, not hidden (AGENTS.md: never fake a result).
# scripts/estimate.py only ever writes a results/ JSON on a clean compile + a recognized report;
# a nonzero exit here means dla_compiler itself couldn't compile that (model, arch) pair at that
# BW point (or, in practice, at all -- see the per-model reasons in
# results/reports/ph0_estimator.md; BW only changes the *number*, never whether it compiles).
#
# Usage: scripts/sweep_estimates.sh [--precision int8|fp32]
#
# Writes:
#   results/ph0_<subject>_<date>.json          — one per successful (model, arch, membw)
#   results/reports/ph0_sweep_attempts.csv     — one row per *attempted* combo, pass or fail
#   models/compiled/_ph0_scratch/sweep_logs/*  — full stdout+stderr per failed attempt (gitignored)
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

precision="int8"
while [ $# -gt 0 ]; do
    case "$1" in
        --precision) precision="$2"; shift 2 ;;
        *) echo "sweep_estimates.sh: unknown arg $1" >&2; exit 2 ;;
    esac
done

models=(
    ad-toycar
    ds-cnn-kws
    mobilenetv1-025-vww
    mobilenetv2-1.0-imagenet
    resnet18-imagenet
    resnet8-cifar10
    tiny-yolov3
)
archs=(
    models/arch/AGX3_Performance.arch
    models/arch/AGX3_Small_NoSoftmax.arch
    models/arch/AGX3_Small_Softmax.arch
)
bws=(200 250 333 400)

log_dir="models/compiled/_ph0_scratch/sweep_logs"
mkdir -p "$log_dir"
attempts_csv="results/reports/ph0_sweep_attempts.csv"
mkdir -p "$(dirname "$attempts_csv")"
echo "model,arch,membw_mbps,precision,status,detail" > "$attempts_csv"

n_ok=0
n_fail=0

for model in "${models[@]}"; do
    for arch in "${archs[@]}"; do
        arch_name="$(basename "$arch" .arch)"
        for bw in "${bws[@]}"; do
            log_file="$log_dir/${model}__${arch_name}__${bw}mbps__${precision}.log"
            if python3 scripts/estimate.py --model "$model" --arch "$arch" --membw "$bw" \
                    --precision "$precision" > "$log_file" 2>&1; then
                detail="$(tail -1 "$log_file" | tr ',' ';')"
                echo "${model},${arch_name},${bw},${precision},ok,\"${detail}\"" >> "$attempts_csv"
                n_ok=$((n_ok + 1))
                echo "OK   ${model} / ${arch_name} / ${bw} MB/s -> ${detail}"
            else
                # estimate.py's own error line is prefixed "estimate.py: "; the raw dla_compiler
                # log tail after it is often multi-line and ends in a blank line, so grab that
                # prefixed line specifically rather than an unreliable `tail -1`.
                detail="$(grep -m1 '^estimate\.py: ' "$log_file" | tr ',' ';' | cut -c1-220)"
                [ -n "$detail" ] || detail="$(tail -5 "$log_file" | tr '\n,' ' ;' | cut -c1-220)"
                echo "${model},${arch_name},${bw},${precision},fail,\"${detail}\"" >> "$attempts_csv"
                n_fail=$((n_fail + 1))
                echo "FAIL ${model} / ${arch_name} / ${bw} MB/s -> ${detail} (full log: ${log_file})"
            fi
        done
    done
done

echo "----------------------------------------------------------------"
echo "sweep_estimates.sh: ${n_ok} ok, ${n_fail} failed, $((n_ok + n_fail)) attempted"
echo "attempts log: ${attempts_csv}"
[ "$n_ok" -gt 0 ]
