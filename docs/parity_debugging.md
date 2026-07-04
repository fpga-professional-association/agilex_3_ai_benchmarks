# Parity gate debugging playbook

Issue #21 / PLAN §9 PH5 / §10 risk register ("quantization drift"): the CPU-INT8 reference runs on
**bit-identical inputs** to hardware (guaranteed by the packer, #5). Any per-record mismatch is
therefore a bug — layout, scale, or DMA — never "FPGA error", and never accepted as an aggregate
rounding difference. `sw/host/parity_gate.py` gates on **100% per-record match**; a single
mismatch fails the build.

This file is the diagnosis order once `parity_gate.py` reports mismatches. Written from the
failure signatures the issue names — filled in with real symptom/cause pairs as they're found
during hardware bring-up (issue #18).

## 1. Margin ≈ 0 mismatches → tie-breaking / argmax order

**Symptom:** mismatches are a small minority of records, and every mismatch's `cpu_top2_margin`
(from `parity_gate.py`'s mismatch list) is near the quantization step size (`scale` in
`quant_manifest.json`) — i.e. the top-2 logits are within one INT8 quantization level of each
other.

**Cause:** the CPU (numpy/OpenVINO argmax) and the hardware scoreboard's argmax tree
(`rtl/scoreboard/scoreboard.sv`) can tie-break differently when two logits land on the *exact*
same quantized value (first-index-wins vs last-index-wins, or a different reduction-tree
associativity). This is not a numerical bug — both answers are "correct" for a true tie.

**Fix:** confirm the tie-break rule the RTL argmax tree implements (read the reduction order in
`scoreboard.sv`), then either (a) match the CPU reference's argmax tie-break to it (numpy's
`argmax` returns the *first* max — if the RTL keeps the *last*, flip the compare direction in the
reduction, or reverse the CPU comparison), or (b) fix the RTL to match numpy's convention. Once
aligned, re-run the gate — this class of mismatch should hit exactly 0.

## 2. Systematic class skew → layout transpose

**Symptom:** match rate is far below 100% but not near-zero, and mismatches aren't randomly
distributed across classes — e.g. class 3 predictions on hardware consistently land where class 5
CPU predictions were expected, or a size-N transpose pattern (row/col swapped) is visible when
mismatches are grouped by predicted class.

**Cause:** the packer's "engine-native layout" (`sw/packer/layouts.py`) doesn't actually match what
the compiled inference IP consumes — e.g. NHWC vs NCHW, or a channel-minor blocking scheme from
the `dla_compiler` report that the packer's `layouts.transform()` doesn't implement bit-for-bit.

**Fix:** re-read the `dla_compiler` compile report's layout section (issue #18 step 2 explicitly
calls this out: "read the dla_compiler report, add the layout to `sw/packer/layouts.py`"). Decode
one mismatching record (`sw/packer/inspect_recimg.py --decode K`), manually permute its tensor
bytes by the suspected transpose, and confirm the *corrected* argmax now matches CPU. Fix
`layouts.py`, re-pack, re-run the gate.

## 3. Everything wrong → scale/zero-point drift

**Symptom:** match rate is near 0% (or barely above chance) across every record and every class —
no pattern, just broadly wrong.

**Cause:** the packer quantized with a different scale/zero-point than the deployed model actually
expects — e.g. `quant_manifest.json` was regenerated after re-quantizing (issue #3's
`quantize_int8.py` picks a *different* calibration slice or a different NNCF version) but the
`.recimg` wasn't re-packed with the new values, or the packer read a stale `quant_manifest.json`.

**Fix:** confirm `sw/packer/pack_records.py`'s `--quant-manifest` argument points at the *same*
`models/ir/<id>/quant_manifest.json` the deployed `int8/<id>.xml` was quantized to produce (check
the manifest's `int8_ir_sha256` against the actual deployed IR's sha256 — they must match). If
they were quantized with different NNCF runs, re-pack against the current manifest and re-run.

## 4. Late-records-wrong → DMA stride bug

**Symptom:** early records match perfectly; mismatches start appearing after some record index K
and increase in frequency (or become total) past that point.

**Cause:** an addressing bug in the replay datapath (`rtl/replay/record_framer.sv`) — burst
addressing drifting from the intended `REC_BASE + k*REC_STRIDE`, e.g. an off-by-one in burst
sizing that doesn't matter for small K but accumulates, or a wraparound near a HyperRAM row
boundary the burst engine doesn't handle. Also check the packer side: confirm `REC_STRIDE` in the
host runner's config matches the manifest's `stride` field exactly — a stride mismatch between
packer and replay-framer config produces exactly this "fine at first, garbage later" signature as
the two address sequences drift apart.

**Fix:** binary-search the first bad record index K via `parity_gate.py`'s `--max-mismatches` (or
a bisecting `--recimg` slice); decode records right at the boundary and compare their *expected*
vs *actual* byte offset in the image (`stride * k`). Confirm the framer's burst-address generator
against that arithmetic in simulation (`sim/replay/`) before touching hardware.

## Always

- Never accept "matches on average" — recompute the per-class confusion pattern even at 99.9%.
- Re-run `parity_gate.py`'s exit code in CI (`sw/host/run_bench.py --parity`, once wired) so a
  regression fails the build instead of quietly landing in a results JSON nobody reads closely.
