# rtl/microbench/l0_tensor_chain/ — L0 tensor-mode DSP dot-product chain (issue #9)

PLAN §7 L0 + §3 LV2. Measures achieved INT8 MACs/DSP/cycle and tensor-chain fmax with an N-block
cascaded dot-product chain, and is the reference input for `scripts/audit_tensor_mode.py`, the
fitter-report merge gate every later Quartus build must pass.

**Bottom line up front: tensor-mode capture could not be achieved on this device with this
toolchain.** Quartus Prime Pro 26.1.0 Build 110 refuses to elaborate the tensor-mode WYSIWYG
primitive for `FAMILY "Agilex 3"` — this is a real, demonstrated toolchain gap (evidence below), not
a hardware problem or a fixable RTL coding-pattern problem. Everything else the issue asks for
(parameterized N-block chain, LFSR stimulus + golden-model checksum self-check, the fitter-report
audit script + its pytest, real Quartus compiles, fmax) is implemented and verified in classic
(non-tensor) DSP mode, which **is** real, compileable, and is exactly what makes the "10x loss" LV2
warns about *visible* rather than hypothetical (see "What actually got measured" below).

## Investigation: why this is not tensor-mode RTL

Step 2 of this issue is "start with N=1: compile, open the fitter report, confirm the block is in
tensor mode. Iterate the RTL pattern until it is." This section is that iteration's record.

1. **The DSP UG's real interface**, fetched live from `docs.altera.com/r/docs/813968` (Variable
   Precision DSP Blocks User Guide, Agilex 5 FPGAs and SoCs — Agilex 3's DSP fabric is
   Agilex-5-derived per PLAN §1 and architecture brief 776602): Tensor Fixed-point Mode computes
   "A signed 20-bit fixed-point DOT product ... performs 10 signed 8x8 multiplications" per column,
   two columns per DSP (`fxp32_col_1`, `fxp32_col_2`, each 32-bit), fed by shared `data_in_{1..10}`
   against two independently-preloaded ping-pong weight buffers (`load_bb_one`/`load_bb_two`/
   `load_buf_sel`), with `cascade_data_in_col_{1,2}`/`cascade_data_out_col_{1,2}` (32-bit each)
   chaining blocks together and `acc_en`/`zero_en` selecting add-cascade vs. accumulate vs.
   pass-through. This matches PLAN §1's "20 INT8 MACs/DSP/cycle, tensor mode, two-column structure".
   The user-facing instantiation path is **IP Catalog → Library → DSP → Primitive DSP → Native AI
   Optimized DSP Agilex FPGA IP** (`docs.altera.com/.../parameterizing-the-native-ai-optimized-dsp-
   agilextm-fpga-ip`), explicitly walked through targeting **an Agilex 5 device** in that doc page.

2. **The underlying WYSIWYG atom exists in this Quartus install** but only for one specific,
   undocumented internal family tag. Found by inspection inside the `quartus-pro` Docker image:
   - `/opt/altera/quartus/libraries/megafunctions/xml_info/inteleighteena_synth_tensor_mac_info.xml`
     — the atom's port/parameter list: `dsp_mode` (`TENSOR_FXP`), `dsp_chain_tensor`,
     `dsp_side_feed_ctrl`, `dsp_fp32_sub_en`; ports `data_in[95:0]`, `cascade_data_in/out[63:0]`,
     `result_h[36:0]`/`result_l[37:0]`, `load_bb_one/two`, `load_buf_sel`, `acc_en`, `zero_en`,
     `clr[1:0]`, `ena[3:0]`, `shared_exponent[7:0]` — a 1:1 parameter-name match to the DSP UG's IP,
     confirming this is that IP's underlying primitive.
   - Direct instantiation test (`inteleighteena_synth_tensor_mac`, `FAMILY "Agilex 3"`,
     `DEVICE A3CY100BM16AE7S`) via `quartus_syn`:
     ```
     Error (16666): WYSIWYG primitive "u_mac" is not compatible with the current device family
     ```
   - The **plain classic-mode MAC atom from the same family** (`inteleighteena_synth_mac`) fails
     **identically** for `FAMILY "Agilex 3"` — ruling out a parameter/port mistake on this project's
     part; the whole `inteleighteena` atom family is gated away from this device, not just the
     tensor variant.
   - Quartus's own message catalog explains why (`common/help/webhelp/msgs/msgs/
     ecut_cut_dsp_prime_use_illegal_device.htm`, message **ID 24863**):
     > CAUSE: The native AI-optimized DSP (DSP Prime block) only supports **Stratix 10 NX and
     > Agilex 5 devices**.
     Agilex 3 is not in that list. This is Quartus's own documented restriction, not a guess.

3. **The IP-Catalog path (`tensor_agilex_edge`, "Native AI Optimized DSP Agilex FPGA IP") was also
   tried**, since its `SUPPORTED_DEVICE_FAMILIES` (`common/ip/altera/native_mac/tensor_agilex_edge/
   tensor_agilex_edge_hw.tcl`) lists `{"SUNDANCEMESA" "Liberty Mesa" "LIBERTYHEIGHTS"}` —
   `LIBERTYHEIGHTS` is confirmed (via `devices/dev_install/agilex3.sha256` listing
   `devices/10nm/ddb_libertyheights_*.ddb`) to be Agilex 3's own internal device-database codename,
   so on paper this IP claims Agilex 3 support that the raw WYSIWYG check above denies. In practice:
   - No headless generation path exists in this Docker image: `ip-generate` is absent entirely;
     `qsys-generate` rejects a hand-written legacy Tcl `.ip` file with a generic
     `Error opening system` (no further detail); `qsys-script`'s `create_system`/`add_instance` flow
     (the modern Platform Designer scripting API — confirmed working syntax:
     `create_ip agilex_native_tensor_dsp l0_tensor_mac_n1`, `set_instance_parameter_value ...`)
     **silently downgrades the instance to an inert Generic Component placeholder** on `save_system`
     (`Info: Replacing top.l0_tensor_mac_n1 with generic component` / `All modules have been
     converted to Generic Components`) rather than erroring or generating real HDL.
   - Even setting the headless-generation gap aside, `tensor_agilex_edge_hw_extra.tcl`'s own
     `gen_terp` proc resolves the atom name to instantiate as `tennm_dsp_prime` for any
     `DEVICE_FAMILY` other than the literal string `"Liberty Mesa"` (LIBERTYHEIGHTS falls into that
     "else" branch). `tennm` is Agilex 7's 10nm-node family tag, and **no `synth_tensor_mac.xml` (or
     any `*mac*` atom) exists anywhere under `common/devxml/cag/tennm/`** in this install — Agilex 7
     has no AI tensor block at all (correctly, per PLAN). So this code path would instantiate a
     nonexistent atom for Agilex 3 even if IP generation worked end-to-end. This looks like a
     genuine, unfinished rough edge in this specific Quartus 26.1 IP script for a device family that
     shipped in 2025 (PLAN §7: "third-party performance literature is effectively empty" — this
     project is finding first-party tooling immaturity too).

**Net finding, worth flagging loudly per AGENTS.md**: PLAN §1/§3 characterize Agilex 3's DSP as
having a directly-usable 20-MAC/cycle AI tensor block; on Quartus Prime Pro 26.1.0 Build 110, that
block's *WYSIWYG/IP-Catalog user-facing entry points are hard-restricted to Stratix 10 NX and Agilex
5 only*, and the Agilex-3-labeled IP-Catalog metadata that suggests otherwise does not actually work
end-to-end in this toolchain version. This does not contradict PLAN's characterization of the
*silicon* (Agilex 3's DSP fabric is genuinely Agilex-5-derived per the architecture brief) — it is a
**tooling** gap. FPGA AI Suite's `dla_compiler` may reach tensor mode through its own internal,
non-public RTL/netlist generation (that's its whole value proposition), but that is a different,
proprietary code path, not something available to hand-written RTL — and this issue explicitly asks
for hand-written RTL that "provably captures tensor mode."

## What actually got measured (classic-mode fallback)

Given the above, `l0_tensor_chain.sv` is **classic-mode RTL**: plain inferred `+`/`*` (no WYSIWYG
primitive), which Quartus's ordinary DSP inference maps onto classic 18×19 DSP blocks (confirmed:
`quartus_syn` on a simple `signed(a)*signed(b)+acc` design reports real, non-zero classic DSP usage
on this device/family — inference itself is not blocked, only the primitive-name/IP-Catalog paths
to tensor mode are). Each `l0_mac_block` only exercises **one** of the tensor block's two columns
(10 MACs/cycle), so this fallback tops out at **half** PLAN §1's 20 MACs/DSP/cycle target — that gap
*is* the "silent 10x loss" LV2 warns about, made concrete: for N=1, real DSP resource usage from an
actual `quartus_sh --flow compile l0_tensor_chain -c l0_tensor_chain_n1` run in this session:

```
; DSP Blocks Needed [=A+B+C-D]                                ; 5 / 138            ; 4 %   ;
;     [A] Total Fixed Point DSP Blocks                        ; 5                  ;       ;
;     [B] Total Floating Point DSP Blocks                     ; 0                  ;       ;
;     [C] Total DSP_PRIME Blocks                              ; 0                  ;       ;
```

5 classic (`[A]`) DSP blocks for a 10-wide dot product (2 multiplies/block), **0** tensor (`[C]`)
blocks — `scripts/audit_tensor_mode.py` correctly FAILs this report (`scripts/tests/
fixtures/classic_mode_n1.fit.rpt` is this exact excerpt, committed as the audit script's real
regression fixture). fmax at this revision (300 MHz aggressive target, PLAN §2): **59.63 MHz**
(Quartus Timing Analyzer Fmax Summary; worst-case setup slack -13.438 ns at 300 MHz) — unsurprising,
since a single unpipelined 10-wide dot product is a lot of combinational depth for one cycle; this
microbench does not attempt internal pipelining (PLAN §3 LV1 retiming disciplines matter far more
once there's a production datapath to retime — see `l1_pe_array` for that level).

At **N=8** (`quartus/l0_tensor_chain/l0_tensor_chain_n8`), N ⋅ 10 = 80 multiplies would need up to 40
classic DSP blocks (2 multiplies/block) — well inside the 138-DSP budget. At **N=32**, the naive
classic-mode analogue would need up to 160 DSP blocks, **exceeding the device's entire 138-DSP
budget** — i.e. reproducing PLAN's tensor-mode density in classic mode for the largest requested N
does not even physically fit on this chip. That arithmetic alone is a second, independent
illustration of the "10x loss" this issue is about (see the compile log referenced in the PR / final
session report for which of N∈{8,16,32} were actually compiled this session vs. left for later).

## Register map (Avalon-MM, byte offsets, 32-bit; mirrors `bench_pkg::L0_ADDR_*`)

| Addr | Register | Access | Function |
|---|---|---|---|
| 0x00 | CTRL | RW | bit0 START (self-clearing) — reseeds the LFSRs + MAC accumulators and arms a new run |
| 0x04 | N_VECTORS | RW | number of retired vectors to run before stopping |
| 0x08 | CYCLES_LO | RO | low 32 bits of the cycle span; **reading this latches the atomic snapshot** |
| 0x0C | CYCLES_HI | RO | high bits of the cycle span (from snapshot) |
| 0x10 | DONE_COUNT | RO | vectors retired (from snapshot) |
| 0x14 | CHECKSUM | RO | XOR-accumulated checksum of every retired combined result (from snapshot) |
| 0x18 | STATUS | RO | bit0 RUNNING · bit1 DONE |
| 0x1C | N_BLOCKS | RO | compile-time N — cross-check against which `.sof` is loaded before trusting anything else |

Snapshot semantics mirror `docs/register_map.md`'s scoreboard convention (issue #15): reading
CYCLES_LO atomically latches CYCLES_HI/DONE_COUNT/CHECKSUM together. `CTRL.START` both reseeds the
stimulus LFSRs to their fixed seeds and zeroes the cascade accumulators — the same requirement the
real tensor primitive's own `clr0`/`clr1` ports exist for (PLAN §3 LV1 "reset-less pipeline
register" is about not sprinkling a *system-wide* reset net through a retiming-sensitive datapath;
this is a *local*, algorithmically-required clear tied to the START event, the same distinction the
real hardware itself makes with dedicated `clr`/`zero_en` inputs rather than folding it into a
generic device reset).

## Stimulus and the golden-model checksum self-check

`l0_lfsr.sv`: a parameterized Galois LFSR (`state <= (state>>1) ^ (state[0] ? TAPS : 0)`) — `TAPS`
is an arbitrary fixed nonzero mask, **not** a claimed maximal-length polynomial; the only property
that matters here is that the RTL and `sw/host/l0_golden.py` compute the identical sequence from the
identical seed. One shared "data" LFSR feeds every block; each block has its own independent
"weight" LFSR (`seed_for_role(block_index)`). Both free-run every cycle (never clock-gated, per LV1)
so that no operand is ever a compile-time-constant literal Quartus could const-fold away from a real
multiplier (confirmed the hard way: an early probe multiplying live data against **literal** 8-bit
weight constants synthesized to **0 DSP blocks** — Quartus's constant-coefficient-multiply
optimization folded the whole thing into LUTs. Every operand in the committed RTL is a live,
externally-unknowable register for exactly this reason).

`sw/host/l0_golden.py` replays the exact same cycle-by-cycle update order (not a closed-form
formula — see its docstring for the precise "what does `chain_out` read this cycle" reasoning) and
is checked bit-for-bit against a real Verilator simulation of `l0_tensor_chain` in
`sim/l0_tensor_chain/tb_l0_tensor_chain.sv` (N_BLOCKS=3, N_VECTORS=5: cycles=12, done=5,
checksum=0x00001909 — regenerate with
`python3 sw/host/l0_golden.py --n-blocks 3 --n-taps 10 --n-vectors 5`) and independently at N_BLOCKS=1
(N_VECTORS=7: cycles=12, checksum=0xFFFF47E0). Both PASS under `sim/l0_tensor_chain/run.sh`. This
satisfies the issue's "Checksum self-check passes in hardware (or handed off with sim/compile
evidence)" criterion via simulation evidence — no hardware exists to run this on (see Hardware
handoff below), and no tensor-mode netlist exists to run the "real" self-check against regardless.

## Files

| File | Role |
|---|---|
| `l0_lfsr.sv` | Parameterized Galois LFSR stimulus generator |
| `l0_mac_block.sv` | One N_TAPS=10-wide INT8 dot-product-and-cascade-accumulate stage (classic-mode RTL) |
| `l0_tensor_chain.sv` | Top: N cascaded `l0_mac_block`s + LFSRs + the Avalon-MM CSR slave above |
| `../../../quartus/l0_tensor_chain/l0_tensor_chain_top.sv` | Compile-only harness (power-on CSR sequencer + observable outputs) — not a hardware bring-up top level, see its header comment |
| `../../../sim/l0_tensor_chain/tb_l0_tensor_chain.sv` | Verilator self-checking testbench (checksum self-check) |
| `../../../sw/host/l0_golden.py` | Golden cycle-accurate Python model |
| `../../../sw/host/l0_regs.py`, `run_l0.py` | Host register wrapper + runner |
| `../../../scripts/audit_tensor_mode.py` | Fitter-report tensor-vs-classic DSP mode audit (the merge gate) |

## Hardware handoff

No AXC3000 board is available in this environment (needs issue #7's JTAG/System Console flow, which
is itself not closed at the time of writing — see the workflow-level report for how that dependency
was treated). The following remain genuinely hardware-gated regardless of the toolchain finding
above:

- **Measured MACs/DSP/cycle with cycle counts** — `sw/host/run_l0.py --verify-golden` is written and
  unit-tested against a mock transport, but has never talked to real silicon; PLAN §10 requires a
  real f_clk and a real cycle count, and this microbench's own formula
  (`known_MAC_count / (cycles × N)`) needs an actual `CYCLES_64` reading.
- **Board-measured fmax** — the `fmax_mhz` results below are Quartus Timing Analyzer static-timing
  estimates (`kind: "estimate"`, exactly as the issue text specifies: "from timing report, `kind:
  "estimate"` until hardware-run"), not silicon measurements.
- **Tensor-mode block count == N** (acceptance criterion #1) — cannot be satisfied at all on this
  toolchain/device combination, independent of hardware; would need either a future Quartus/FPGA AI
  Suite release with Agilex 3 tensor-DSP WYSIWYG/IP-Catalog support, or routing the design through
  FPGA AI Suite's own `dla_compiler` netlist generation instead of hand-written RTL (out of scope for
  "RTL that provably captures tensor mode").
