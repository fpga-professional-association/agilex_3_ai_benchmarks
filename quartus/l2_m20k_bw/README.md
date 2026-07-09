# L2 aggregate-M20K-bandwidth board harness (issue #12, PLAN §7 L2 / §3 LV3)

On-hardware bring-up of the L2 microbench (`rtl/microbench/l2_m20k_bw/m20k_bw.sv`) on the **Arrow
AXC3000** (Agilex 3 `A3CY100BM16AE7S`). This is an **ON-CHIP-ONLY** benchmark: NO external memory,
NO HyperBus pins. NUM_BANKS independent M20K banks each retire K back-to-back reads into their own
XOR-fold checksum sink; a host reads the counters back over JTAG (control plane only, PLAN §8
method E) and computes the achieved aggregate GB/s.

Mirrors the silicon-proven HyperBus bandwidth-test template at
`/home/tcovert/projects/hyperram/fpga/axc3000` (`make_bw_sys.tcl` / `top.sv` / `bw_read.tcl` idiom),
simplified: one clock domain (no clk2x), no I/O periphery beyond the JTAG-Avalon master + 3 LEDs.

## Clock plan

| Clock | Source | Freq | Drives |
|-------|--------|------|--------|
| board XO | `CLK_25M_C` (A7) | 25 MHz | IOPLL refclk |
| `clk` | IOPLL `outclk0` | **~300 MHz, 0°** | m20k_bw + JTAG-Avalon master + Qsys backbone (single domain) |

300 MHz is PLAN §4's "M20K on-chip ... ~330 GB/s aggregate @300 MHz" operating point, so the
harness's achieved-GB/s number is directly comparable to that PLAN figure without a clock-scaling
fudge. **The actual post-fit Fmax may be lower** — always pass `sysconsole/l2_read.tcl`'s
`fclk_MHz` argument the frequency you actually constrained/achieved (Timing Analyzer report), never
this planning wish.

## What's here

| File | Role |
|------|------|
| `qsys/make_l2_sys.tcl` | qsys-script that builds `qsys/l2_sys.qsys` (IOPLL + reset + JTAG-to-Avalon master) |
| `qsys/l2_sys.qsys` | generated Platform Designer system (regenerate with the flow below) |
| `top.sv` | board top: `l2_sys` (clock/reset/JTAG master) → `m20k_bw` CSR slave |
| `pins.tcl` | pin + I/O-standard assignments (copied from `/home/tcovert/projects/hyperram/fpga/axc3000/pins.tcl`, itself sourced from `quartus/constraints/axc3000_board.tcl`) |
| `l2_m20k_bw.qsf` / `l2_m20k_bw.qpf` | Quartus project (`FAMILY "Agilex 3"`, `DEVICE A3CY100BM16AE7S`, `TOP top`) |
| `l2_m20k_bw.sdc` | timing constraints (25 MHz create_clock, derive_pll_clocks, false_path on button/LEDs) |
| `sysconsole/l2_read.tcl` | System Console script: program K, poll done, print achieved/theoretical GB/s + checksums |

Top-level ports (board signal names, matching `pins.tcl`): `CLK_25M_C`, `USER_BTN` (active-low
reset), and LEDs `LED1` (STATUS.done), `RLED` (tied off — m20k_bw has no error status), `GLED` (PLL
locked) — all active-low. **No HyperBus/memory pins** — this design has none.

## Config matrix (issue #12 step 3/4)

`top`'s `L2_GEOMETRY` / `L2_OUTPUT_REG` parameters select the hardware config (default = config a,
set in `l2_m20k_bw.qsf` via `set_parameter`):

| Config | GEOMETRY | OUTPUT_REG | What it shows |
|--------|----------|------------|----------------|
| a (default) | BANKED (`L2_GEOMETRY=0`) | ON (`L2_OUTPUT_REG=1`) | PLAN §3 LV3 "good" geometry — the ~330 GB/s ceiling |
| b | SHARED (`L2_GEOMETRY=1`) | ON (`L2_OUTPUT_REG=1`) | round-robin single-port anti-pattern (same bytes, far more cycles) |
| c | BANKED (`L2_GEOMETRY=0`) | OFF (`L2_OUTPUT_REG=0`) | output-registers-off fmax-cost geometry |

To build a non-default config, edit the two `set_parameter` lines at the bottom of
`l2_m20k_bw.qsf` (or copy the .qsf to a per-config variant, same convention as
`quartus/l0_tensor_chain`'s `l0_tensor_chain_n*.qsf`) and recompile — this is a synthesis-time
parameter, not a runtime switch, so each config is its own `.sof`.

## Build

Everything runs headless in the Quartus-Pro 26.1 Docker image. Define a helper once (adjust the
bind-mount path to wherever this worktree lives):

```bash
QL2() { docker run --rm -i --user $(id -u):$(id -g) -e HOME=/tmp \
  -v <WORKTREE_ROOT>:/workspace \
  -v /dev/null:/usr/lib/x86_64-linux-gnu/libudev.so.1 \
  -w /workspace/quartus/l2_m20k_bw alterafpga/quartus-pro:26.1-agilex3 "$@"; }
```

1. **Build + generate the Qsys system** (produces `qsys/l2_sys/` HDL and the per-instance IP under
   `qsys/ip/l2_sys/`). `--quartus-project=l2_m20k_bw` is REQUIRED on **both** `qsys-script` and
   `qsys-generate` (verified against this exact Docker image, 26.1 build 110) — without it on
   `qsys-script`, project auto-creation fails outright ("Failed to create Quartus Project", wrong
   default device); without it on `qsys-generate` the sub-IP (IOPLL, JTAG master, reset controller)
   are emitted as empty "Generic Component" black boxes and synthesis fails with "instantiates
   undefined entity". `qsys-script --quartus-project=...` also auto-appends the `IP_FILE`/
   `QSYS_FILE` assignments to `l2_m20k_bw.qsf` (already reflected in the committed file):

   ```bash
   QL2 qsys-script --script=qsys/make_l2_sys.tcl --quartus-project=l2_m20k_bw
   QL2 qsys-generate qsys/l2_sys.qsys --synthesis=VERILOG --quartus-project=l2_m20k_bw --rev=l2_m20k_bw
   ```

2. **Synthesize / compile**:

   ```bash
   QL2 quartus_syn l2_m20k_bw -c l2_m20k_bw       # synthesis only (fast iteration)
   QL2 quartus_sh --flow compile l2_m20k_bw -c l2_m20k_bw   # full compile (fit + assemble + timing)
   ```

   Bitstream lands in `output_files/l2_m20k_bw.sof`. Run the tensor-mode/RAM audit as usual
   (`scripts/audit_tensor_mode.py`) and check the Fitter's RAM summary confirms one M20K per bank
   (32 separate M20K blocks for NUM_BANKS=32) — the issue's "do not let all readers hit one
   physical bank via optimizer merging" warning.

## Program the board

Program over the on-board USB-Blaster III (see the project memory note on the AXC3000 JTAG path —
program via a root + `--privileged` + `/dev/bus/usb` container, NOT the compile container):

```bash
QPRO quartus_pgm -c 1 -m jtag -o "p;output_files/l2_m20k_bw.sof"
```

`GLED` lights when the IOPLL locks. Press `USER_BTN` (S2) to reset the fabric.

## Run the benchmark

```bash
QPRO system-console --script=sysconsole/l2_read.tcl 100000 300.0
#   args: <K_reads_per_reader> <fclk_MHz>   (defaults: K=100000, fclk=300.0 -- pass the REAL
#   achieved clock from the Timing Analyzer report, not the IOPLL target)
```

It opens the JTAG-Avalon master, reads DIMS, programs K, pulses `CTRL.start`, polls `STATUS.done`,
then prints CYCLES, per-bank + aggregate checksums, and achieved/theoretical GB/s + efficiency.
`LED1` (done) mirrors `STATUS` each time the host polls it. Cross-check the printed checksums
against `scripts/l2_golden.py` (the script prints the exact invocation to run) before trusting the
GB/s number — issue #12 do-not: never report bandwidth from a run whose checksum failed.

Equivalently from the host side, `sw/host/run_l2.py` drives the same flow over
`SystemConsoleTransport` and emits a schema-valid `results/` JSON:

```bash
python3 sw/host/run_l2.py --k 100000 --fclk-mhz 300.0 --verify-golden \
    --out results/l2_m20k_bw_a_banked_outreg.json
```

## Build status

See the PR description for what has been verified without hardware (Verilator TB, pytest,
qsys-generate, quartus_syn) vs. what remains hardware-gated (full fit/timing closure, RAM-summary
audit, program, and the 3-config GB/s measurement).
