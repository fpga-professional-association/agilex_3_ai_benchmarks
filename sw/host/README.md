# sw/host/

Host-side control plane (JTAG System Console — control/readback ONLY, never the timed data path,
PLAN §8). Loads record images into HyperRAM, configures the scoreboard (`docs/register_map.md`),
starts runs, polls DONE_COUNT, reads back counters, and emits `results/` JSON. Includes the
unlicensed-IP-cap check (DONE_COUNT vs intent, PLAN §10) — `run_bench.py`'s `UNLICENSED_CAP` is
currently `10_000` (issue #17, matching PLAN §9 PH1's stated figure at the time). Note found while
working issue #7: `docs/toolchain.md` (issue #1) since verified the live upstream cap is actually
**100,000**, not 10,000 (fetched fresh from `altera-fpga/agilex-ed-ai-suite`'s README). Flagging
here rather than silently patching `run_bench.py` — that's issue #17's already-merged code and a
behavior change belongs in its own PR, not a drive-by inside #7.

`smoke_infer.py` (issue #7) is a separate, smaller tool targeting the FPGA AI Suite hostless-JTAG
example design's own JTAG-Avalon memory map (DDR global-memory window + CoreDLA CSR) — a different
platform than the scoreboard/HyperRAM record-replay path the rest of this directory drives. See its
module docstring and `docs/board_bringup.md`.

## Accuracy parity gate (issue #21)

```
python read_result_log.py --n-records N --out hw_log.bin      # hardware: dump the result log
python parity_gate.py --recimg X.recimg --model-ir models/ir/<id>/int8/<id>.xml \
    --quant-manifest models/ir/<id>/quant_manifest.json --hw-log hw_log.bin
```

`parity_gate.py` re-runs every packed record through the same OpenVINO INT8 IR issue #3's
`eval_int8_cpu.py` uses (dequantizing with the packer's own scale/zero-point first, so the CPU
reference sees bit-identical inputs to hardware) and requires a **100% per-record match** — see
`docs/parity_debugging.md` for the diagnosis order when it isn't. The comparison logic
(`compare_predictions`) is pure and fully offline-testable; the OpenVINO/decode glue
(`compute_cpu_predictions`, `run_parity_gate`) was smoke-tested end-to-end against a real
resnet8-cifar10 IR + a synthetic recimg (8 records, self-consistent mock hw-log) but the actual
gate — a real hardware log compared against real hardware predictions — needs #18 (board bring-up)
and is not runnable in this sandbox. See the issue #21 PR's "Hardware handoff" section.
