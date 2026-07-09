# ph3_hyperram_char — synthesis + fit results (DELIVERABLE 5, PH3_SUBMODULE_SPEC.md)

**Status: attempted and COMPLETED** within the 900s time-box (quartus_syn + quartus_fit + quartus_sta
all ran to success; nowhere near the budget — total ~2 min). These are **measured** Quartus Prime Pro
26.1 numbers for `char_top` (axc3000_hyperram_pads → axc3000_hyperram_axi4 → third_party/hyperram's
`hyperram_avalon`, PHY_VARIANT="SDR", DIFF_CK=1) on `A3CY100BM16AE7S`, real HyperRAM board pins per
`quartus/constraints/axc3000_board.tcl`, AXI4 slave bus VIRTUAL_PIN'd (no traffic generator in this
build — see `char_top.sv` header), HyperBus pins false-pathed (bring-up style, not board-timing
closed — see `quartus/constraints/ph3_hyperram_char.sdc`). NOT a hardware measurement; not the
submodule's own measured 96.8-342 MB/s (that number is the submodule's, cited as such in
`docs/ph3_submodule.md`, not reproduced here).

Commands run (headless, via `source scripts/env.sh`):
```
quartus_syn ph3_hyperram_char -c ph3_hyperram_char   # 0 errors, 8 warnings, ~17s
quartus_fit ph3_hyperram_char -c ph3_hyperram_char   # 0 errors, 5 warnings, ~94s, "Timing requirements were met"
quartus_sta ph3_hyperram_char -c ph3_hyperram_char   # 0 errors, 2 warnings, ~6s
```

## Fit summary (`ph3_hyperram_char.fit.summary`)
| Resource | Used | Total | % |
|---|---|---|---|
| ALMs | 977 | 34,000 | 3% |
| Dedicated logic registers | 1,030 | — | — |
| Block memory bits | 512 | 5,365,760 | <1% |
| RAM Blocks (M20K) | 1 | 262 | <1% |
| DSP Blocks | 0 | 276 | 0% |
| PLLs | 1 | 11 | 9% |
| Pins | 17 | 254 | 7% |

Zero DSP usage confirms no accidental classic-mode DSP inference in this datapath (PLAN §3 LV2
tensor-mode concern doesn't apply here — there's no DSP in this design at all).

## Fmax (`ph3_hyperram_char.sta.rpt`, "Fmax Summary" panel — reported per-clock, ignoring the
constrained period; this is NOT the achieved operating frequency, which was fixed at 50/100 MHz by
`qsys/make_char_clkgen.tcl`'s `CK_MHZ=50` and closed with 3.82 ns / 8.71 ns of positive setup slack):

| Clock | Fmax | Restricted Fmax | Note |
|---|---|---|---|
| `clk` (iopll outclk0, CK word rate) | 237.42 MHz | 237.42 MHz | — |
| `clk2x` (iopll outclk1, SDR byte rate) | 423.73 MHz | 352.98 MHz | limit: minimum pulse width restriction |

The 352.98 MHz `clk2x`-restricted Fmax lines up closely with the submodule README's own cited
"byte-clock restricted Fmax ~353 MHz" reference point (PH3_SUBMODULE_SPEC.md DELIVERABLE 5) — i.e.
this build's fabric closes at essentially the same ceiling the submodule's own char/bring-up work
found, on the same device. `clk` at 237 MHz is comfortably above any CK rate this design would
actually run (W957D8NB ceiling is 200 MHz, memory note "w957d8nb-max-clock-is-200mhz" — not
independently re-verified in this pass).

## Honest scope / what this does NOT prove
- This is fabric + real-I/O-pin **structural** closure at CK=50 MHz/clk2x=100 MHz (a conservative
  bring-up point, per `qsys/make_char_clkgen.tcl` header), not a timing-closed board build: the
  HyperBus pins are `set_false_path`'d (see `ph3_hyperram_char.sdc` header), so W957D8NB
  source-synchronous I/O timing (tDSS/tDSH/tCKD, board trace delay) is NOT closed here.
- No traffic generator drives the AXI4 slave in this build (it's VIRTUAL_PIN'd) — this does not
  exercise the datapath at all, only synthesizes/fits/times it. Functional correctness is proven by
  `sim/hyperbus/run_hyperram_axi4.sh` (Verilator, GENERIC PHY), not by this build.
- Never programmed to a board. No `.sof` was produced or requested (no `quartus_asm` run — not
  needed to answer the fmax/resource question this deliverable asked for).
- CK_MHZ=50 in `make_char_clkgen.tcl` was chosen as a first, conservative attempt; bumping it and
  re-running is the natural next step once this scaffold needs to characterize a faster HyperBus
  clock plan (matching the submodule's own progression to CK_MHZ=175 in
  `third_party/hyperram/fpga/axc3000/qsys/make_bw_sys.tcl`).

## Regenerating
```
source scripts/env.sh
cd quartus/ph3_hyperram_char/qsys
qsys-script --script=make_char_clkgen.tcl --quartus-project=../ph3_hyperram_char.qpf
qsys-generate char_clkgen.qsys --synthesis=VERILOG --quartus-project=../ph3_hyperram_char.qpf --rev=ph3_hyperram_char
cd ..
quartus_syn ph3_hyperram_char -c ph3_hyperram_char
quartus_fit ph3_hyperram_char -c ph3_hyperram_char
quartus_sta ph3_hyperram_char -c ph3_hyperram_char
```
`--quartus-project` on **both** the qsys-script and qsys-generate calls is required — see the .qsf
header comment for why (without it on qsys-script, every sub-IP silently becomes an empty "Generic
Component" and synthesis fails with "instantiates undefined entity").
