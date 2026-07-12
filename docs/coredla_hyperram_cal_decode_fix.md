# CoreDLA HyperRAM cal-CSR decode fix — root cause + silicon verification (2026-07-12)

Closes the HANDOFF-2 blocker (the runtime launch-trim calibration CSR read HyperRAM data instead of
its own registers). Verified on the AXC3000. Session scripts and the long-form write-up are in
`scratch/hyperram_retest/` (gitignored): `HANDOFF3_decode_rootcause.md`, `verify_cal_v3.tcl`,
`calibrate_ed.tcl`, `calibrate_broad.tcl`, `wound_retest.tcl`.

## Root cause

The single `phy_0` JTAG-Avalon host master reaches this design through the
`jtag_address_span_extender`, whose windowed_slave **is** the HyperRAM window at base `0x0`. The
vendor MMD (`coredla_device/mmd/system_console/system_console_script.tcl`) claims that one master as
two disjoint sub-ranges: `{0x0 0x80000000}`→HyperRAM and `{0x80000000 0x900}`→the real CSR decode
(DLA CSR `0x8000_0000..07FF`, hw_timer `0x8000_0800..08FF`).

The cal CSR was placed at `0x4000_0000` (earlier `0x9000_0000`) — **both inside the
`0x0..0x7FFFFFFF` HyperRAM span** — so every access was physically a HyperRAM access. No netlist,
readLatency, or window-shrink change could fix a slave placed there (the HANDOFF-2 512 MB
window-shrink, commit 6056d71, actually broke nothing usefully and is reverted).

## Fix (this commit)

`quartus/coredla_hyperram_ed/platform/hw/qsys/ed_zero.tcl`:
- Move `hyperram_0.cal_csr` from `0x4000_0000` → **`0x8000_0900`** (just above hw_timer, inside the
  DLA-CSR decode window, ABOVE the HyperRAM span). Reached via the DLA-CSR claim widened to
  `{0x80000000 0xA00}`.
- Revert `SLAVE_ADDRESS_WIDTH` to the proven 2 GB window (undo the 512 MB shrink).

RTL (`rtl/hyperbus/hyperram_cal_csr.sv`) unchanged — the readLatency angle was a red herring.

## Silicon verification (v3 .sof)

`verify_cal_v3.tcl` after configuring the v3 bitstream:
- DLA IP ID `@0x8000_0000` = **0x81C43991**, hw_timer `@0x8000_0800` = **0x0** (idle),
- cal ID `@0x8000_0900` = **0x48524331** ("HRC1"), REG_DBG reset `0x00071263`, REG_CAL `0x00000002`.

## Two operational findings (load-bearing for any future board session)

1. **JtagClock must be 6 MHz.** At the default 15 MHz, `quartus_pgm`/`design_load` silently fail to
   reconfigure the FPGA ("Synchronization failed") and the board keeps running the last-loaded
   design — every "new .sof" then reads the same stuck config. `jtagconfig --setparam 1 JtagClock
   6000000` before programming fixes it.
2. **The contiguous-write corruption is knob-INDEPENDENT.** With the cal CSR now driving REG_DBG
   live, a 32-combination sweep of every wound-healing knob leaves a contiguous write at 1/64 correct
   (only the last word survives; 22528 B bulk = 100/22528). Spaced / guard-banded writes are clean
   (14/14). So the runtime cal CSR cannot repair the corruption — a real HyperRAM inference must use
   GUARD-BANDED host writes (`sw/host/hyperram_loader.py`), not the vendor contiguous DMA.
