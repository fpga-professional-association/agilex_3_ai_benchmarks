# CoreDLA + HyperRAM on-board inference: hang diagnosis (2026-07-10)

On-board CoreDLA inference over JTAG **reaches silicon and triggers**, but hangs at the compute
step. This documents the full diagnosis and the single remaining blocker, with the evidence for each
step so the work isn't repeated.

## What works (verified on the physical AXC3000, 200 MHz `top.sof`)

| Step | Evidence |
|---|---|
| Board programs over JTAG (system-console `design_load`) | `loading sof: .../top.sof`, jtagconfig shows `A3C(W100BM16A\|Y100BM16A)` |
| JTAG-Avalon master reaches CSR **and** HyperRAM | one master `.../phy_0/master`, glob `*phy_0/*master` |
| CoreDLA CSR alive | `CSR[0x80000000] = 0x81c43991` (IP ID) |
| Runtime **matches** the IP | "Runtime arch check passed", "Runtime build version check passed" |
| `.aot` imports, inference **triggers** | reaches "[Step 10/12] Measuring performance", WaitForDla polling starts |
| clk_dla timing clean | retuned 280→**200 MHz**, `top.sta.rpt`: **0 setup violations**, worst clk_dla slack **+0.513 ns** |
| HyperRAM **single/spaced** writes are bit-exact | 5/5 spaced 32-bit writes read back exactly (CAFEBABE/12345678/A5A5A5A5/DEADBEEF/0F0F0F0F) |

The vendor toolchain was built from AI-Suite runtime source for this board:
`dla_aot_splitter` + `dla_benchmark` (`-target_agx3_dev_jtag_system_console`), in
`scratch/rt_jtag/build_Release/`. One privileged `fpgaaisuite:2026.1.1-quartus` container
(root + `/dev/bus/usb`) does program+run; it bundles `quartus_pgm`/`jtagconfig`/`system-console`.

## The hang

`dla_benchmark` triggers inference, then: `WaitForDla polling timeout`, `jobs finished 0`, and the
DLA's LSU counters report **0 filter reads, 0 input reads, 0 output writes**. Since *config* reads
are not in those counters, this means the DLA stalls **during/after reading its config** and never
reaches filter/input.

## Hypotheses ruled out (with evidence)

1. **clk_dla timing** — hangs identically at timing-clean 200 MHz (0 violations). ❌
2. **HyperRAM needs host calibration** — the CoreDLA build's HyperRAM (`IO_VARIANT="DDIO_GPIO"`)
   self-initializes (`init_done` autonomous, no CSR calibration gate). The "runtime read-eye
   calibration" (REG_CAL) belongs to a *different* bitstream (the SDR bandwidth-test top), not this
   one. ❌ (subagent source trace)
3. **Reset / clock wiring** — `clk_ddr` = jtag_pll.outclk0 (175 MHz, proven alive by JTAG);
   `i_resetn_ddr` tied `1'b1` **matches the pristine vendor design** (not a regression). ❌
4. **DLA not wired to HyperRAM** — `ed_zero.tcl:87` `emif_data_bridge.m0 → hyperram.s_axi @0x0`;
   same slave the JTAG master uses. ❌

## Root cause: HyperRAM **contiguous writes corrupt** (DDIO_GPIO PHY)

Measured on the exact CoreDLA bitstream via system-console:

- **Bulk write** `config.bin` (22528 B) at 0x0 via `master_write_from_file`, read back → **73% of
  bytes wrong** (16507/22528). Readback is periodic garbage (even words `0x04040404`, odd words
  `0x00000000`); only the tail survives.
- **Contiguous single writes** (`master_write_32`, 256 words): ascending **1/256** correct,
  descending **33/256**. Isolated/spaced single writes are 100% correct.

So: **isolated writes work; any sustained/contiguous write is systematically mangled** — a DDR
write-phase corruption in the `DDIO_GPIO` HyperRAM PHY, not a sparse 4-word "wound." The vendor
runtime writes config/weights as contiguous blocks → they land corrupt → the DLA reads invalid
config → stalls (0 filter/input/output activity) → timeout.

This is consistent with: the other agent's **bandwidth-test** bitstream uses a *different* PHY
(`SPLIT_PHY`/`SDR`) and does 341 MB/s contiguous writes **correctly**. The CoreDLA build is locked to
`DDIO_GPIO` (`axc3000_hyperram_pads.sv:45`; `axc3000_hyperram_axi4_hw.tcl` doesn't expose
`IO_VARIANT`).

## The fork (both nontrivial)

1. **Fix the HyperRAM write path** (unblocks all 4 HyperRAM models — the main deliverable).
   Either switch the CoreDLA build to the working `SDR`/`SPLIT_PHY` PHY (which then also needs its
   runtime read-eye calibration injected before inference), or fix the `DDIO_GPIO` write timing
   (`TX_B_DLY` etc.). This is the HyperRAM PHY (`rtl/hyperbus/**`, `third_party/hyperram/**`) — the
   other agent's domain.
2. **DDR-free** (2 models, weights in on-chip MIF ROM — no HyperRAM writes). Sidesteps the bug but
   is unbuilt (no `.sof`), experimental (`AGX3_Ddrfree.arch` adapted from AGX7, `FP12AGX`), and needs
   the streaming MMD + streaming driver.

## Reproduce

```
# program + run (hangs): scratch/run_dla_bench.sh via the privileged fpgaaisuite container
# HyperRAM write corruption probe: scratch/woundtest.tcl + scratch/ordertest.tcl (system-console)
```
