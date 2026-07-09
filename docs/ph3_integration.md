# PH3 integration: swapping CoreDLA's LPDDR4 EMIF for the HyperRAM AXI4 subsystem

This is the third PH3 document. `docs/ph3_interfaces.md` reverse-engineered CoreDLA's AXI4 "DDR"
master; `docs/ph3_bridge_design.md` specified the sim-proven `axi4_hbmc_bridge`. This one **packages
that bridge as a Platform Designer memory subsystem and drops it into the FPGA AI Suite example
design in place of the LPDDR4 EMIF** — the exact swap `docs/board_bringup.md` §2f identified as the
real blocker to an AXC3000 build (issue #7, merged to `main`).

It records three artifacts and one bounded compile attempt. Read the honest scope box first.

> ### Scope / honesty box (read this)
> - The AXI4↔Avalon **datapath** is proven in simulation (`sim/hyperbus/tb_axi4_hbmc_bridge.sv`,
>   real `hbmc_core` + W957D8NB BFM). The bridge+controller **logic** closes timing at 250 MHz
>   standalone (`quartus/ph3_bridge_char`, setup slack +0.345 ns).
> - **This document (and the qsys/quartus attempt log below) describes an EARLIER session's Qsys
>   swap, done against the OLD tristate-stub-PHY wrapper.** A later session (see
>   `docs/ph3_submodule.md`) replaced that stub with the `third_party/hyperram` submodule's real,
>   silicon-proven SDR PHY inside `rtl/hyperbus/axc3000_hyperram_axi4.sv`, and re-verified the
>   AXI4↔Avalon datapath in simulation against it (`sim/hyperbus/run_hyperram_axi4.sh` → PASS). The
>   Qsys/`quartus_syn` swap narrated below has **not** been re-run against that new wrapper — the PD
>   component `.tcl`, `ed_zero.tcl`, and this synthesis attempt still reflect the pre-submodule stub.
>   Treat everything below this box as historical/structural evidence for the swap *mechanics*, not
>   as current PHY status. Current PHY status lives in `docs/ph3_status.md` and
>   `docs/ph3_submodule.md`.
> - The HyperBus PHY described in the wrapper **at the time of this session's Qsys/synthesis
>   attempt** was a **thin tristate stub, not a datasheet-timed DDR-IO PHY**. It synthesized and
>   drove the bidirectional balls, but would **not** have clocked a real W957D8NB correctly on
>   hardware. That gap is now closed at the RTL/sim level by the submodule adoption above; it is
>   **not yet re-verified through this Qsys/synthesis path**.
> - This session produced a design that **generates (qsys) and synthesizes (quartus_syn) clean on
>   the real AXC3000 device `A3CY100BM16AE7S`, 0 errors**, and enters the Fitter. It did **not**
>   produce a signed-off bitstream, and even if it did, it would not run inference on hardware
>   without the real PHY (at the time, still a stub). No result JSON is claimed. Nothing here is
>   committed; the working tree is `_ph3_ed_hyperram/` (a copy — the pristine `_ph3_ed/` was not
>   touched).

---

## Artifacts

| Artifact | Path | State |
|---|---|---|
| Integration wrapper (AXI4 slave + HyperBus conduit + tristate PHY stub) | `rtl/hyperbus/axc3000_hyperram_axi4.sv` | Verilator-lint clean; synthesizes on A3CY100BM16AE7S |
| Platform Designer component | `quartus/ip/axc3000_hyperram_axi4/axc3000_hyperram_axi4_hw.tcl` | Parses (`ip-make-ipx`) + elaborates + `validate_system` clean |
| ed_zero.tcl swap recipe + attempt | this doc + `_ph3_ed_hyperram/` (uncommitted working copy) | qsys-generate OK, quartus_syn 0 errors, fit entered |

The wrapper instantiates `axi4_hbmc_bridge` → `hbmc_core` and adds a **thin single-data-rate tristate
PHY**: `assign hb_dq = hb_dq_oe ? hb_dq_o : 'z;` (+ `hb_rwds`), `hb_cs_n` straight from the
controller, `hb_ck = clk` (placeholder free-running clock), `hb_rst_n = reset_n`. The hbmc **CSR
slave is tied to power-on defaults** (`cfg_fixed=1`, `cfg_lat=6` → fixed-latency mode) so the
component is a self-contained AXI4↔conduit drop-in with no extra bus. A `` `ifdef VERILATOR`` guard
swaps `'z` for `0` so existing sims stay clean.

---

## The `_hw.tcl` component (drop-in for the EMIF)

`add_interface` set: **clock** sink `clk`, **reset** sink `reset` (active-low, `associatedClock clk`,
`synchronousEdges DEASSERT`), **axi4** slave `s_axi`, **conduit** `hyperbus`
(`hb_dq`/`hb_rwds`/`hb_cs_n`/`hb_ck`/`hb_rst_n`), **conduit** `status`
(`wstrb_partial_seen`/`hi_addr_seen`). Two modelling notes that cost real debugging:

1. **PD's `axi4` interface models a single ID width** (awid==arid==bid==rid). CoreDLA is asymmetric
   (AWID=5, ARID=2). Exactly like the stock `emif_data_bridge_0.s0` (`S0_ID_WIDTH=5`) it replaces, we
   present a **unified ID**; CoreDLA's 2-bit read ID rides the low bits, the top.sv connection
   zero-pads. `rid` still echoes `arid`, so read-data routing is preserved.
2. **A slave shared by >1 AXI manager needs extra ID bits.** In the system, *two* managers drive this
   slave (CoreDLA's `emif_data_bridge.m0` **and** the host `jtag_address_span_extender`), so the
   interconnect needs `max_manager_id(5) + ceil(log2(2)) = 6` ID bits. The instance therefore sets
   `WID_W=RID_W=6` (and `ADDR_W=33` to match `emif_data_bridge.m0`). The stock EMIF's slave
   auto-sized to absorb this; a fixed-width custom slave must be told.

Validation (no GUI needed):
```
# parse/index
ip-make-ipx --source-directory=quartus/ip/axc3000_hyperram_axi4        # -> Found 1 components
# elaborate + validate (instantiate in a scratch qsys)
qsys-script --search-path="quartus/ip/axc3000_hyperram_axi4,$" --script=<add_instance+validate_system>
#   -> clk/reset/s_axi/hyperbus/status all build; validate_system reports only the expected
#      "must be connected to a clock/reset" for a standalone instance. No property/role errors.
```

---

## The ed_zero.tcl swap recipe (exact)

All line numbers are the **pristine** `_ph3_ed/hw/qsys/ed_zero.tcl`. The applied result is in
`_ph3_ed_hyperram/hw/qsys/ed_zero.tcl`.

### 0. Device (line 11)
```
- set_project_property DEVICE {A3CY135BM16AE6S}
+ set_project_property DEVICE {A3CY100BM16AE7S}
```

### 1. Instantiations (in `do_create_system`, lines 49-65)
```
- instantiate_emif_sideband_driver      ;# only polled LPDDR4 calibration -> gone with the EMIF
...
- instantiate_emif                       ;# the LPDDR4x32 EMIF
+ instantiate_hyperram                    ;# new proc; add_component of axc3000_hyperram_axi4
```
New proc (persist an `.ip` via `add_component`, **not** `add_instance` — see "The generic-component
trap" below), setting `WID_W=6 RID_W=6 ADDR_W=33`:
```tcl
proc instantiate_hyperram {} {
    upvar hyperram hyperram
    add_component ${hyperram} ip/ed_zero/ed_zero_hyperram_0.ip axc3000_hyperram_axi4 axc3000_hyperram_axi4_inst
    load_component ${hyperram}
    set_component_parameter_value WID_W {6}
    set_component_parameter_value RID_W {6}
    set_component_parameter_value ADDR_W {33}
    set_component_project_property HIDE_FROM_IP_CATALOG {false}
    save_component
}
```
plus `set hyperram "hyperram_0"` in the variable block (~line 36).

### 2. Datapath connections — DELETE (lines 69-72, 85-86)
```
- ${jtag_address_span_extender_inst}.expanded_master / ${emif}.s0_axi4
- ${emif_sideband_driver}.axil_driver_axi4_lite / ${emif}.s0_axi4lite
- ${emif_data_bridge}.m0 / ${emif}.s0_axi4          (the else branch, non-PMON)
```
ADD (both AXI managers now target the HyperRAM slave):
```
+ ${jtag_address_span_extender_inst}.expanded_master / ${hyperram}.s_axi   baseAddress 0x0000
+ ${emif_data_bridge}.m0 / ${hyperram}.s_axi                              baseAddress 0x0000
```
The `${emif_data_bridge}` (an `altera_axi_bridge`, s0 exported to CoreDLA in top.sv) is **kept** — only
its `m0` is re-pointed. That is why the CoreDLA-facing top.sv wiring is unchanged.

### 3. Clock connections — the crux (lines 92-100)
The EMIF supplied `emif.s0_axi4_clock_out` (~200 MHz), which clocked the CSR/JTAG-data domain **and**
became CoreDLA's `clk_ddr`. With no EMIF there is no such clock. DELETE all `emif.s0_axi4_clock_out`
/ `emif.s0_axi4lite_clock` / `jtag_pll…/emif_sideband_driver` connections and drive the whole
global-memory + CSR-side domain (and `clk_ddr` via `emif_clk_bridge.in_clk`) from **`jtag_pll.outclk0`
(100 MHz)**:
```
+ jtag_pll.outclk0 -> {csr_data_bridge.clk, emif_data_bridge.clk, jtag_address_span_extender.clock,
+                      emif_clk_bridge.in_clk, hyperram_0.clk, jtag_master.clk, hw_timer_bridge.s0_clk}
  dla_pll.outclk0  -> {dla_clk_bridge.in_clk, reset_bridge.clk, reset_handler.clk}   (unchanged)
```
Single clock across the whole memory path ⇒ the bridge is single-clock, no CDC (matches
`docs/ph3_bridge_design.md` v1). The bridge closes 250 MHz, so 100 MHz is comfortable. **This is the
frequency knob**: to run `clk_ddr` faster than the HyperRAM clock, re-insert an AXI clock-crossing
(`altera_axi_bridge` reclock or `rtl/common/async_fifo`) — never hand-rolled (AGENTS.md).

> **Update (this session — submodule adoption, see `docs/ph3_submodule.md`).** The connections above
> describe the *prior* single-clock `hyperram_0` component (old tristate-stub PHY). The rewritten
> `rtl/hyperbus/axc3000_hyperram_axi4.sv` wraps the `third_party/hyperram` submodule's
> `hyperram_avalon`, whose real SDR PHY needs **two related clocks**, not one: the existing word-rate
> `clk` (still `jtag_pll.outclk0`, unchanged) **and a new `clk2x`** — the 2× byte clock the SDR PHY
> uses in place of a true +90° phase (see `axc3000_hyperram_axi4.sv` header and
> `docs/ph3_submodule.md` "Clock plan"). `clk_ref` continues to tie to `clk` (both PHY_VARIANT=
> "GENERIC" and "SDR" ignore/tie it), so it is not a new PD connection. Concretely, the PD swap
> recipe above needs one more step, **not yet applied to `ed_zero.tcl` or
> `quartus/ip/axc3000_hyperram_axi4/axc3000_hyperram_axi4_hw.tcl`**:
> - Add a second `clock sink` interface `clk2x` to the `_hw.tcl` component (Deliverable 4 in
>   `PH3_SUBMODULE_SPEC.md`, not done this session).
> - Generate a 2×-rate output from `jtag_pll` (a second IOPLL output clock, phase 0, twice the `clk`
>   frequency — this is *not* the old generic-PHY "+90° at the same rate" idea; the SDR PHY variant
>   repurposes the same port name for a genuinely faster clock) and connect it to `hyperram_0.clk2x`
>   alongside the existing `jtag_pll.outclk0 -> hyperram_0.clk` connection.
> - This is unattempted PD/Qsys work — see `docs/ph3_status.md` "What remains" #1 and
>   `docs/ph3_submodule.md` for the full clk/clk2x/clk_ref rationale.

### 4. Reset connections — DELETE (lines 109-116)
The EMIF's `emif_sideband_driver.cal_done_rst_n` (gated on calibration) is gone. DELETE the four
`cal_done_rst_n` connections and the three `reset_handler.reset_n_out -> emif*/sideband` connections;
drive the memory-domain resets from `reset_handler.reset_n_out` directly (it already ANDs
`rrip.ninit_done` + user reset + both PLL locks):
```
+ reset_handler.reset_n_out -> {jtag_address_span_extender.reset, csr_data_bridge.clk_reset,
+                               emif_data_bridge.clk_reset, reset_bridge.in_reset, hyperram_0.reset}
```

### 5. Exports — DELETE (lines 129-133), ADD
```
- emif ref_clk / mem_0 / oct_0 / mem_ck_0 / mem_reset_n     (the 5 LPDDR4 conduit exports)
+ set_interface_property hyperram_hb     EXPORT_OF hyperram_0.hyperbus
+ set_interface_property hyperram_status EXPORT_OF hyperram_0.status
```
Keep `emif_data_bridge_0_s0` (CoreDLA), `emif_clk_bridge_0_out_clk` (= `clk_ddr`), the PLL refclks,
csr_data_bridge m0, reset exports, hw_timer exports.

### The generic-component trap (why `add_component`, not `add_instance`)
A fileset `_hw.tcl` component added with **`add_instance`** from a `--search-path` is downgraded to a
Qsys **"Generic Component"** (black box) on `save_system`: qsys-generate then emits only a
port-footprint module `shell_hyperram_1`, **not your RTL**, and the design has an unresolved black
box. Using **`add_component <name> <ip>.ip <type> <inst>`** (like every other IP in ed_zero.tcl)
persists an `.ip`, so the component survives save and qsys-generate copies your four SV files into
`qsys/ip/ed_zero/ed_zero_hyperram_0/axc3000_hyperram_axi4_10/synth/` and lists them in its `.qip`
with top-level `axc3000_hyperram_axi4`. This was the single biggest integration gotcha.

---

## top.sv port swap

Stock top.sv's entire non-JTAG/clock/reset I/O is the LPDDR4 bus. Replace it (lines 24-37) with the
HyperBus conduit, names matching `quartus/constraints/axc3000_board.tcl`:
```
- input i_lpddr4_comp1_refclk_p, i_lpddr4_comp1_rzq; output o_lpddr4_comp1_reset_n/ck_p/ck_n/ca/cs/cke;
- inout io_lpddr4_comp1_dmi[3:0]/dqs_p[3:0]/dqs_n[3:0]/dq[31:0];
+ inout  [7:0] hb_dq;  inout hb_rwds;  output hb_cs_n, hb_ck, hb_rst_n;
+ output hb_wstrb_partial_seen, hb_hi_addr_seen;     ;# optional status trip-wires
```
In the `shell pd (...)` instance: DELETE the 10 `emif_0_mem_*`/`oct`/`ck`/`reset_n` port maps (lines
206-216) and the `emif_0_ref_clk_0_clk (i_lpddr4_comp1_refclk_p)` map (line 276); ADD:
```
+ .hyperram_hb_dq(hb_dq), .hyperram_hb_rwds(hb_rwds), .hyperram_hb_cs_n(hb_cs_n),
+ .hyperram_hb_ck(hb_ck), .hyperram_hb_rst_n(hb_rst_n),
+ .hyperram_status_wstrb_partial_seen(hb_wstrb_partial_seen), .hyperram_status_hi_addr_seen(hb_hi_addr_seen),
```
The `emif_data_bridge_0_s0_*` maps (CoreDLA's DDR master) are **unchanged**. top.qsf: device →
`A3CY100BM16AE7S`, drop the C-series `BOARD`, swap the `ed_zero_emif_io96b_lpddr4_0.ip` IP_FILE for
`ed_zero_hyperram_0.ip`, and (for fit) replace `pin_assignments.sdc` with the board pinout.

### 25 MHz IOPLL reparam note (required for a real board fit)
The example assumes a **100 MHz** `i_pll_ref_clk`; both IOPLLs (`dla_pll`, `jtag_pll` in ed_zero.tcl)
are parameterized with `gui_reference_clock_frequency = user_ref_clk_freq_mhz = 100`. The AXC3000 has
only a fixed **25 MHz** oscillator (`CLK_25M_C` @ A7, 1.2 V — `axc3000_board.tcl`,
`board_bringup.md` §2c). Set `user_ref_clk_freq_mhz 25` (line ~1087) so the IOPLL M/N/C dividers
regenerate for a 25 MHz input while holding the same 100 MHz (jtag) / DLA outputs — ordinary IOPLL
re-parameterization, not attempted for the synth-only milestone below (it changes no netlist, only
the PLL divider values and the achievable Fmax the design's own `dla_adjust_pll.tcl` retunes to).

---

## The bounded compile attempt (what actually ran)

Worked on the copy `_ph3_ed_hyperram/` (pristine `_ph3_ed/` untouched). Flow mirrors the vendor's
`generate_sof.tcl` (`qsys-script … ed_zero.tcl` → `qsys-generate -syn shell.qsys` → `quartus_syn`).

| Stage | Command | Result |
|---|---|---|
| Qsys construct | `qsys-script --search-path="quartus/ip/axc3000_hyperram_axi4,$" --cmd="set system_name shell;" --script=ed_zero.tcl` | **OK** — `shell.qsys` built, `validate_system` clean (after widening ID to 6) |
| Qsys generate | `qsys-generate -syn --part=A3CY100BM16AE7S shell.qsys` | **OK** — "Finished: Platform Designer system generation", 0 errors; emitted **`ed_zero_hyperram_0`** with the real SV (not a black box) |
| Synthesis | `quartus_syn top` | **`Quartus Prime Synthesis was successful. 0 errors, 51 warnings`** on `A3CY100BM16AE7S` |
| Fit | `quartus_fit top -c top` | **Entered and progressed** (I/O planning + periphery placement, 0 errors). HyperBus balls placed with I/O buffers tracing to `pd|hyperram_0|axc3000_hyperram_axi4_inst|u_hbmc|hb_dq_oe` — the tristate stub resolves to the real controller. |

The 51 synthesis warnings are all pre-existing **CoreDLA vendor-IP** DSP WYSIWYG advisories
(`ENA driven by GND`, `FP32_RESULT` width on `dla_aux_*_AGX.sv`) — identical class to the stock
issue-7 baseline, none from the HyperRAM subsystem.

**Milestones hit:** the LPDDR4→HyperRAM swap **generates and synthesizes clean on the real AXC3000
device with no unresolved black box** — the memory-subsystem gap that `board_bringup.md` §2f flagged
as *the* blocker to any AXC3000 build is structurally closed. Two concrete integration bugs were
found and fixed along the way (shared-slave ID width; `add_component` vs `add_instance` generic-
component downgrade).

**Not claimed:** a signed-off bitstream, a timing result, or any on-hardware behavior. Fit was
allowed to enter but not gated to `quartus_asm`; a *meaningful* fit/STA needs the items below first.

### Reproduce
```
source scripts/env.sh
rm -rf _ph3_ed_hyperram && cp -a _ph3_ed _ph3_ed_hyperram   # or regenerate _ph3_ed per docs/ph3_interfaces.md
# (apply the ed_zero.tcl / top.sv / top.qsf edits above — already applied in this session's copy)
cd _ph3_ed_hyperram/hw/qsys
qsys-script --new-quartus-project=/tmp/scr \
  --search-path="<repo>/quartus/ip/axc3000_hyperram_axi4,$" --cmd="set system_name shell;" --script=ed_zero.tcl
qsys-generate -syn --family="Agilex 3" --part=A3CY100BM16AE7S \
  --search-path="<repo>/quartus/ip/axc3000_hyperram_axi4,$" shell.qsys
cd .. && quartus_syn --write_settings_files=off top
```

---

## What remains for a FUNCTIONAL on-hardware system (honest handoff)

> **Update (this session):** item 1 below is now **CLOSED at the RTL/sim level** by the
> `third_party/hyperram` submodule adoption (`docs/ph3_submodule.md`) — its SDR PHY is real,
> silicon-measured hardware, not a stub. It is **not yet closed through this document's Qsys/
> `quartus_syn` path**, since that attempt predates the submodule swap (see the honesty box above).
> Re-running the Qsys swap + synthesis against the new wrapper is unattempted follow-on work.

1. ~~A real DDR-IO HyperBus PHY.~~ **CLOSED (RTL/sim level, this session)** — was the #1 blocker.
   `hbmc_core` was PHY-agnostic; the wrapper's stub was SDR tristate with `hb_ck = clk`. It has been
   replaced by the `third_party/hyperram` submodule's `hyperram_avalon`, whose SDR PHY drives a
   real center-aligned, CS-gated DDR clock and datasheet-timed DDR I/O, measured on this exact board
   (`docs/ph3_submodule.md`). Not yet re-verified through *this* Qsys/synthesis flow (see above).
   (PLAN §3 LV6.)
2. **Board pinout + 25 MHz IOPLL reparam.** Apply `axc3000_board.tcl` (rename the top ports to
   `CLK_25M_C`/`USER_BTN`/`hb_*` to match, or wrap) and set `user_ref_clk_freq_mhz 25`. Then the
   design's own `dla_adjust_pll.tcl` retunes the DLA clock to the achievable E7S Fmax.
3. **Regenerated SDC.** `top.out.sdc` constrains the old `emif`-derived clocks. Regenerate it for the
   `jtag_pll.outclk0` (100 MHz) memory domain + `dla_pll` compute domain before STA means anything.
4. **CoreDLA CSR start/done handshake.** Even with correct memory, driving an inference needs the
   CoreDLA CSR start/done protocol over JTAG-Avalon — left `NotImplementedError` in
   `sw/host/smoke_infer.py` (issue #7), undocumented vendor-internal protocol.
5. **HyperRAM bandwidth ceiling.** Structural, not a bug: the 256-bit DDR port is fed by a 16-bit
   HyperBus — **~16× width starvation**, ~12.8× peak-vs-peak at 250 MHz (`docs/ph3_interfaces.md`
   §d). Inference in this system will be HyperRAM-bandwidth-bound (weights re-streamed per inference).
   Record it in `results/` as an estimate once the PHY exists and it can be measured.
