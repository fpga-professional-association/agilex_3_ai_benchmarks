# HyperRAM DDIO write-path bug — hyperram-track response (2026-07-12)

Response to `docs/hyperram_ddio_write_bug_handoff.md` (f19a7fc). Investigated on the hyperram
repo side with desk analysis, a dedicated simulation (`third_party/hyperram/sim/tb_edrepro.sv`,
scratch), and silicon runs on the proven seed-4 bitstream. Verdict first, evidence after.

## Verdict

**This is NOT an RTL defect in the submodule DDIO write path. It is a fit-level launch-timing
defect in the CoreDLA ED build — and the specific cause is almost certainly that the ED project
is missing the CK eye-centring hard pin delay that every proven hyperram bitstream carries:**

```
# fpga/axc3000/bw.qsf:67 (proven build)          # ED top.qsf / pins.tcl (retest branch)
set_instance_assignment -name D5_DELAY 15 \      #   << NO delay assignment on hb_ck at all >>
    -to hb_ck -entity top
```

A full diff of every `hb_*` pin assignment between the proven build and the ED shows this as the
**only** functional difference (the ED's extra lines are its two debug LEDs). Without `D5_DELAY 15`
on `hb_ck`, CK launches uncentred in the DQ data eye at the device — a deterministic, digital-looking,
**knob-independent** failure, because no REG_DBG/REG_CAL knob touches a hard per-pin fit-time delay.
This is the same failure class as the hyperram repo's seed-3 incident (a fit that met STA yet was
silicon-marginal, knob-independent): the DQ/CK pad launch is trim-calibrated per fit and NOT
SDC-constrained, so any new fit — especially a huge CoreDLA fit — must be silicon-validated, and the
ED fit additionally lost the one hard calibration the trim scheme depends on.

## Evidence

1. **Silicon, exact failing shape, proven fit (seed-4, same RTL @ `b544bb7`-equivalent, same
   CK=175 MHz, same fix set `REG_DBG=0x0007_1263`, `D5_DELAY` present):**
   `bw_read.tcl 16 0x18080 16 175` — a 16-word write burst + readback at the same beat the ED's
   `wstrb_abc.tcl` case B uses (word addr `0x18080` = byte `0x30100`) — run **twice** (i.e.
   W,R,W,R same beat: the second write is an identical-value rewrite of word 0, exactly the ED's
   corrupted case): **run 2 `ERR_COUNT=0`**. Changed-data rewrite (`pat_set.tcl 3` addr-echo over
   the LFSR image): **`ERR_COUNT=0`**. Ten more same-beat rewrite runs: **all `ERR_COUNT=0`**.
   The ED symptom does not exist on a correctly calibrated fit of the same RTL.

2. **Simulation (`tb_edrepro.sv`, hyperram repo, ED controller parameterization + wound-semantics
   device model):** on the ED's exact traffic (R16@B immediately followed by W16@B, per host write),
   the controller's pin launch for the second same-beat write is **byte-for-byte structurally
   identical** to a virgin write (same CA shape, same CK count, correct data at the pins). The fast
   R->W turnaround leaves a 5-CK CS#-high gap → CS#-rise→CA1 ≈ 40 ns at 175 MHz ≥ the 35 ns tRWR
   minimum. The controller is exonerated at the functional level; no characterized device law
   reproduces the `0x02000202` word-0 signature.

3. **Why the 32-combination knob sweep couldn't matter:** (a) the fix-set knobs (prewin/contig/
   end-cwrite/defuse) act only at row-aligned closes and contiguous reopens — for a LEN=16 mid-row
   beat write the entire fix set is inert **by design**, so knob-independence was expected, not
   diagnostic; (b) nothing runtime-pokeable moves a hard pin delay.

4. **The pre-RMW vs post-RMW garbage change (`0xa5.. -> 0x02..`) tracks a mechanism change:** the
   pre-RMW pure-W;W corruption was fully explained by the bridge lane-clobber bug (since fixed);
   the post-RMW residue is the fit-level launch corruption above.

## Corrections to the handoff

- "The issue-13 runtime fix-set does NOT fix it" — true but uninformative: the fix set was never
  in play for this shape (point 3 above).
- "Recommended diagnostic: arm the on-chip hyperbus_capture" — the ED build has no capture fabric;
  that instrumentation lives in the hyperram board build. With the root cause identified it should
  not be needed; if the D5_DELAY fix does not clear the symptom, the forensic path is the hyperram
  repo's instrumented bitstream, not a new ED build.
- **Branch hazard (important):** the failing v4 bitstream came from the *retest* branch, which pins
  the submodule at `b544bb7` (issue-13 fix merge) with the knob wiring done correctly. But the
  current `ph3-coredla-hyperram-onboard` line (where the handoff landed) still pins `f2b9dea` —
  **pre-issue-13, no fix-set RTL, no knob ports in the wrapper**. If that line ships without merging
  the retest integration (submodule bump + pads wrapper + cal CSR), even perfectly streaming write
  shapes regress to the pre-fix row-transition losses.

## A second, fit-independent defect your tests haven't seen yet

The device wounds the array at **`[B-4, B)` — the 4 words (8 bytes) below the CA base — on every
write CS# open, standalone writes included** (hyperram board README, "Multi-burst writes" / issue-13
laws; on-silicon marker-proven). The fix set heals this only for contiguous/row-boundary reopens
(streaming shapes); a fresh, non-contiguous open commits zeros there — this is the accepted
"already-dead below-base zone" of the bench contract.

Consequence for the ED: **every RMW'd host write is an isolated open, so every host 32-bit write
zeroes the last 4 words of the *previous* 32-byte beat.** `wstrb_abc.tcl` never reads that zone
(case B checks only the written beat), and it predicts ≥25 % loss in the bulk test *even on a
perfect fit*. After the D5_DELAY fix, expect the surgical cases to go clean but bulk/neighbour
corruption to remain until the traffic shape is fixed:

- **Host uploads: use full-strobe, 32-byte-aligned block writes** (`master_write_memory` with
  aligned buffers), not per-word `master_write_32`. Full-strobe beats skip the bridge RMW, arrive
  contiguous, and the controller coalesces them into row-aligned streams — the shape the fix set
  heals to zero loss. This is likely all the CoreDLA benchmarks need (DLA-side traffic is already
  bulk/streaming).
- Truly random isolated 32-byte writes remain outside the current device contract. An "open-heal"
  extension (controller-internal pre-read of `[B-4,B)` + prewin-drive on every non-contiguous open)
  is a hyperram-repo work item; until then, keep such writes off the datapath or reserve the 8 bytes
  below any independently-written region.

## Action list (ordered)

1. Add the CK centring delay to the ED project (adjust/drop `-entity` for the ED top):
   `set_instance_assignment -name D5_DELAY 15 -to hb_ck` — rebuild.
2. Re-run `wstrb_abc.tcl`: expect A/B/C/D all clean.
3. Re-run the bulk tests with the host upload switched to aligned block writes (no RMW).
4. Adopt the refit discipline for every ED rebuild: `wstrb_abc.tcl` + `wound_retest.tcl` are your
   shape-suite equivalent. A knob sweep is not a substitute — the launch path is calibrated at fit
   time, not at runtime.
5. Merge the retest-branch integration (submodule `b544bb7` or newer + knob-wired pads wrapper +
   cal CSR) into the main line before anything ships from it.
6. Board state: the devkit currently holds the hyperram seed-4 bitstream
   (`third_party/hyperram/fpga/axc3000/bitstreams/ddio_row_175_issue13_fixset_seed4_20260711.sof`)
   with the fix set poked; reprogram your v4/v5 sof when you resume (6 MHz JtagClock gotcha applies).

On the hyperram side I'm tracking: (a) the doc gap that caused this — the integration guide never
stated the `D5_DELAY` QSF requirement (being fixed); (b) SDC-constraining the pad launch path so a
fit is correct by construction (the standing seed-3 debt); (c) the open-heal extension for
non-contiguous write opens.
