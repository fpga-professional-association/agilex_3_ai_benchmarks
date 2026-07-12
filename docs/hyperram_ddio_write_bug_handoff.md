# HyperRAM DDIO write-path bug — handoff to the hyperram track (2026-07-12)

## UPDATE 2026-07-12 — worked around at the bridge; no longer on the critical path

A **write-combiner in `rtl/hyperbus/axi4_hbmc_bridge.sv`** (buffer the 8 partial writes of a 32-byte
beat, flush as ONE full-strobe beat write = the proven one-write-per-beat pattern) makes contiguous
host writes bit-exact on silicon (wound_retest 22528 B: 22528/22528, ascending 256/256). So a single
full 16-word beat write DOES land cleanly on the DDIO — the defect is specifically **more than one
write to the same beat**. The bug below still exists and is worth fixing for robustness/bandwidth
(the combiner costs the read-your-writes flush), but it no longer blocks a HyperRAM inference.

## Verdict

**Yes, there is a real HyperRAM bug, and it is in the submodule DDIO write path**
(`third_party/hyperram/rtl/hyperbus_ctrl.sv` + `third_party/hyperram/fpga/axc3000/hyperbus_gpio_io.sv`,
as instantiated by `rtl/hyperbus/axc3000_hyperram_pads.sv`'s `DDIO_GPIO` branch). It is the same class
as the "write-wound law" from issue #13 — **and the issue-13 runtime fix-set does NOT fix it on the
CoreDLA ED fit** (proven knob-independent below). Everything above the DDIO (the AXI→HyperBus bridge)
has been fixed and ruled out.

## The one-sentence symptom

**One write per 32-byte (256-bit) beat is always correct; writing more than one word into the SAME
32-byte beat corrupts the earlier word(s). Writes to different beats are independent.**

## Reproduction (surgical, on the AXC3000, silicon)

Script `scratch/hyperram_retest/wstrb_abc.tcl`, results verbatim (identical across every build below):

```
A  single: w0@0x30000=0xAAAA0000 -> got 0xaaaa0000  OK        <- one write, one beat: correct
B  same-beat: write w0@0x30100=0xB0B00000 then w1@0x30104=0xB1B10001
             -> w0 reads 0x02000202 (CORRUPT), w1 reads 0xb1b10001 (correct)   <- 2 writes, same beat
C  cross-beat: write b0@0x30200=0xC0C00000 then b1@0x30220=0xC2C20020
             -> both correct                                   <- 2 writes, different beats: fine
D  persistence: w0@0x30000 still 0xAAAA0000                    <- far writes never reach earlier addrs
```

The 32-byte beat boundary is `DATA_W/8 = 256/8 = 32` bytes (16 hbmc 16-bit words). In B, `0x30100` and
`0x30104` are in the same 32-byte beat; writing the second corrupts the first. In C, `0x30200` and
`0x30220` are in adjacent beats and are independent.

Bulk consequence (`scratch/hyperram_retest/wound_retest.tcl`, a 22528 B contiguous write/readback and a
256-word ascending write, both of which do many writes per beat):
`BULK_MATCH ~110/22528`, `ASCENDING_MATCH ~3/256` — near-total corruption; essentially only the
last-written word of each beat survives.

The corruption is deterministic and the garbage is characteristic (`0x02000202`, and historically
`0xDEADBEEF -> 0x02000202`) — this reads as a digital/logical defect, not signal-integrity marginality.

## What has been ruled out (do NOT re-investigate these)

1. **The AXI→HyperBus bridge (`rtl/hyperbus/axi4_hbmc_bridge.sv`) — FIXED, not the residual cause.**
   The bridge originally wrote the full 256-bit beat for every write, honoring `WSTRB` only as a
   detect flag (`wstrb_partial_seen`) — so a 32-bit host write clobbered the beat's other 7 words.
   That was fixed with read-modify-write on partial beats (commits `7451a9b` main / `562616f` retest),
   verified in `sim/hyperbus/tb_axi4_hbmc_bridge.sv` through the real `w957d8nb_bfm` + `hbmc_core`
   (word- and byte-granular RMW cases pass). **After this fix the corruption persists on silicon**
   (B still fails; garbage merely changed `0xa5..` -> `0x02..`; BULK 100->112, ASCENDING 1->4). So the
   bridge now hands the DDIO correct full-beat data and the DDIO still corrupts it.
   NOTE: the bridge sim uses `hbmc_core`; the ED uses the submodule DDIO stack — that difference is
   exactly why the sim passes and the board fails.
2. **Every runtime REG_DBG / REG_CAL launch-trim knob.** A 32-combination sweep of
   `wr_lat_trim`, `ck_stretch_off`, `lat_clocks`, `prewin_drive`, `prewin_n(0..7)`, `postwin_hold`,
   `prewin_marker`, `prewin_contig`, `dbg_end_cwrite`, `dbg_spray_defuse` (the issue-13 fix-set knobs,
   now runtime-pokeable via the new cal CSR at `0x8000_0900` — see below) gives the **same failure for
   every combination** (`scratch/hyperram_retest/calibrate_ed.tcl`, `calibrate_broad.tcl`,
   `cal_coalesce.tcl`). Knob-independent.
3. **Write-coalescing.** A dedicated rebuild with `CTRL_WR_COALESCE = 1'b0`
   (`rtl/hyperbus/axc3000_hyperram_pads.sv`, DDIO branch) failed **byte-identically**
   (BULK 111/22528, ASCENDING 3/256, B still `0x02000202`). Coalescing is not the mechanism.

## Where to look (submodule DDIO)

The residual defect is the DDIO controller's handling of **consecutive accesses that touch the same
32-byte device region** — write-after-write and/or read-after-write to the same/overlapping address.
This is the issue-13 "write-wound law" (later write wounds the words at/near a prior write's region),
still active on the CoreDLA ED fit despite the fix-set being ON at reset (`REG_DBG` reset =
`0x0007_1263` = the full fix-set). Prime suspects, in `hyperbus_ctrl.sv` / `hyperbus_gpio_io.sv`:
- the CS#/CK turnaround and DQ/RWDS launch timing at the boundary between two writes to the same row
  (the fix-set's `end_cwrite`/`spray_defuse`/`prewin` machinery is exactly here, and it is not
  healing this fit),
- and the read->write turnaround for the same beat (the bridge's RMW does read-then-write to one
  address; note however that the pre-RMW build corrupts on pure write->write too, so the core defect
  is not RMW-specific).

## Recommended diagnostic (needs the board)

Arm the existing on-chip capture (`third_party/hyperram/fpga/axc3000/hyperbus_capture.sv` +
`sysconsole/cap_arm.tcl`/`cap_dump.tcl`) around the exact B sequence — two partial writes to the same
32-byte beat (`0x30100` then `0x30104`) — and inspect what the DDIO actually launches at the DQ/RWDS/CK
pins for the SECOND write and the tail of the FIRST. The prediction is that the second write's CA/data
phase disturbs the first word's cells (stale-hold or an extra/short DQ beat near CS# reopen). Compare
against the golden `hyperram_model.sv`, which does not reproduce it (the bridge sim is clean), so the
defect is in the real DDIO launch timing that the model doesn't capture.

## Environment / how to reproduce (load-bearing gotchas)

- Program at **6 MHz JtagClock** — `jtagconfig --setparam 1 JtagClock 6000000` BEFORE `quartus_pgm`.
  At the default 15 MHz, configuration silently fails ("Synchronization failed") and the board keeps
  running the last design.
- Privileged container: `alterafpga/fpgaaisuite:2026.1.1-quartus`, `--privileged --user root`,
  `-v /dev/bus/usb:/dev/bus/usb`, `-v /dev/null:/usr/lib/x86_64-linux-gnu/libudev.so.1`; start `jtagd`.
- Claim discipline (single phy_0 JTAG master): HyperRAM via `claim_service master $p {} "{0x0
  0x80000000 EXCLUSIVE}"`; the runtime cal CSR is at `0x8000_0900` via `"{0x80000000 0xA00 EXCLUSIVE}"`.
- **Never read a HyperRAM address whose window-masked offset exceeds the 8 MB device (0x800000)** — an
  out-of-range read wedges the JTAG decode until a power cycle.
- A latest bitstream with the bridge RMW fix + the cal CSR is staged at
  `scratch/hyperram_retest/top_hyperram_v4.sof` (md5 15a312ec). The cal CSR lets you poke the DDIO
  launch-trim knobs live (`0x8000_0908` = REG_DBG) for further sweeps without recompiling.

## What already works (so the agent can scope to just the write path)

Reads are fine; single/spaced writes are bit-exact; the CSR/control plane and the cal CSR all decode
and respond (DLA IP ID `0x81C43991`, cal ID `0x48524331`). The ONLY broken thing is multi-write-per-
beat (contiguous) writes.
