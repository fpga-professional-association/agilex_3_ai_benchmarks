# hl_jtag_axc3000 — AXC3000 adaptation of the FPGA AI Suite hostless-JTAG example (issue #7)

**Status: not a compiling project yet in this directory.** This directory documents the adaptation
as an overlay on top of AI-Suite-generated files (per the issue's own "whichever keeps the diff
smallest" option), rather than committing a full vendor-generated project tree, because the
adaptation cannot complete without first solving the memory-subsystem gap in `docs/board_bringup.md`
§2f (PLAN §9 PH3 scope, not this issue's). Read `docs/board_bringup.md` in full before touching this
directory — it has the evidence and reasoning behind every line below.

**PH3 status (see `docs/ph3_status.md` / `docs/ph3_coredla_nextsteps.md`):** the §2f memory-subsystem
gap this directory is blocked on is now **structurally closed** — a real, silicon-proven HyperRAM
AXI4 bridge (`rtl/hyperbus/axc3000_hyperram_axi4.sv`, measured 342.4 MB/s write / 337.3 MB/s read on
this exact board) exists and generates/synthesizes clean against the CoreDLA example system in a
separate, uncommitted working tree (`_ph3_ed_hyperram/`) — not in this directory. Committing a
compiling project *here* still needs the remaining PH3 items (clk2x IOPLL wiring, board pinout/SDC,
CoreDLA CSR handshake — see `docs/ph3_coredla_nextsteps.md` for the ordered list).

## How to regenerate the stock baseline (verified this session)

```
source scripts/env.sh
dla_build_example_design.py build -o <builddir> -f agx3c_jtag \
    $COREDLA_ROOT/example_architectures/AGX3_Small_NoSoftmax.arch --skip-compile
dla_build_example_design.py quartus-compile <builddir>
```

This is example ID `agx3c_jtag` (`dla_build_example_design.py list`), targeting the Altera "Agilex
3C-Series Development Kit" (device `A3CY135BM16AE6S`) — this session compiled it clean (placement +
routing succeeded, 0 errors) as the issue's step-1 baseline. Not committed here: it's pure AI-Suite
output, regenerable by the two commands above (same policy as `models/ir/`, see `.gitignore`).

## Overlay: changes that DO carry over to AXC3000 (device `A3CY100BM16AE7S`)

Apply on top of the regenerated `<builddir>/hw/top.qsf` / `pin_assignments.sdc`:

| Stock (C-series devkit) | AXC3000 replacement | Source |
|---|---|---|
| `DEVICE A3CY135BM16AE6S` | `DEVICE A3CY100BM16AE7S` | Arrow User Guide v1.2.1 §2.3.1 |
| `BOARD "Agilex 3 FPGA C-Series 135B Development Kit"` | drop the assignment (no Arrow preset in Quartus) | this repo's own `quartus/smoke`, `quartus/hyperbus_smoke` |
| `i_fpga_core_resetn` @ devkit-specific pin | `USER_BTN` @ PIN_A12, 1.2 V, active-low | `quartus/constraints/axc3000_board.tcl` |
| `i_pll_ref_clk` @ devkit-specific pin, 100 MHz | `CLK_25M_C` @ PIN_A7, 1.2 V, **25 MHz** (not 100 MHz — see below) | `quartus/constraints/axc3000_board.tcl`, `docs/board_bringup.md` §2c |
| `pin_assignments.sdc`'s LPDDR4 block | **no replacement — see below** | `docs/board_bringup.md` §2f |

The 25 MHz vs. 100 MHz reference-clock delta means `qsys/ed_zero.tcl`'s `dla_pll`/`jtag_pll` IOPLL
instantiations need their M/N/C divider parameters regenerated for the new reference frequency
(ordinary IOPLL re-parameterization) — not attempted this session (`docs/board_bringup.md`,
"What this session did not attempt").

## Blocked: LPDDR4 global memory has no AXC3000 equivalent

The stock design's *entire* non-JTAG, non-clock/reset top-level I/O is a 32-bit LPDDR4 interface
(`io_lpddr4_comp1_*` in `top.sv`), hard-wired in `qsys/ed_zero.tcl` as CoreDLA's global/AXI4 "DDR"
memory. The AXC3000 has no LPDDR4 — only a 128 Mb HyperRAM (8-bit HyperBus DDR, entirely different
pins/protocol) — and the installed FPGA AI Suite 2026.1.1 ships no Agilex-3 DDR-free hostless
example to fall back to (verified: `dla_build_example_design.py list`, `example_architectures/`).

Making this compile for real requires replacing `ed_zero.tcl`'s LPDDR4 EMIF instantiation with a
Platform-Designer-integrated, AXI4-or-wider HyperBus (or on-chip-RAM) global memory — PLAN §9 PH3
scope, and dependent on issue #13's HyperBus controller first being wrapped as a Platform Designer
component and itself Quartus-verified (neither has happened yet). See `docs/board_bringup.md` for
the full evidence trail and why a shortcut (tie-off stub, undersized on-chip RAM never validated
against CoreDLA's memory contract) was deliberately not taken here — it would satisfy the letter of
"compiles" while being functionally meaningless on real hardware.

**Do not add a `.sof`, IP-generated RTL, or a `top.qsf` claiming a full AXC3000 build to this
directory until the above is resolved** — it would not be a working design, and AGENTS.md rules out
"looks done" over "is done."

## What issue #7 could and did verify without this gap being resolved

See `docs/board_bringup.md` and the top-level PR/report for the full account:
- Stock C-series build: clean compile, verified (issue's step 1).
- Real AXC3000 pinout (clock/reset/LEDs/HyperRAM): `quartus/constraints/axc3000_board.tcl` + `.sdc`.
- `sw/host/smoke_infer.py`: System Console push/read-back script against the stock design's actual
  JTAG-Avalon memory map (from the AI Suite runtime source, not guessed); CoreDLA's own CSR
  start/done handshake is left `NotImplementedError` (undocumented vendor-internal protocol).
