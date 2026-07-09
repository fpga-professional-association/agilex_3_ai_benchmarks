# PH3 interfaces: CoreDLA AXI4 "DDR" master ↔ HyperRAM Avalon-MM slave

Reconnaissance for the PH3 bridge (PLAN §9 PH3: "HyperRAM integration ... JTAG-Avalon master +
mSGDMA + HBMC + inference IP"). Goal: pin down the *exact* AXI4 global-memory interface the FPGA AI
Suite CoreDLA IP exposes, so we can build an adapter from it to `hbmc_core.sv` (16-bit
Avalon-MM; now `sim/replay/hbmc_core.sv` — see the superseded note in §(b) below). This is
investigation only — no RTL, no full Quartus compile, no hardware.

## Provenance of every quoted signal

All signal names / widths / Tcl below are quoted from a real IP generation, **not** guessed:

```
source scripts/env.sh
dla_build_example_design.py build -o _ph3_ed -f agx3c_jtag \
    "$COREDLA_ROOT/example_architectures/AGX3_Performance.arch" --skip-compile
```

Ran clean (exit 0, `AGX3_Performance` + `agx3c_jtag` accepted — no fallback needed). The generated
tree `_ph3_ed/` is pure AI-Suite output, regenerable by the command above, and is **gitignored**
(added `_ph3_ed/` to `.gitignore`). Paths below are relative to that tree. Confirms the earlier
board-bringup finding (`git show issue-7-hostless-jtag:docs/board_bringup.md` §2f): the CoreDLA DDR
AXI4 port is architecture-independent; here it is read directly off the generated RTL/Tcl.

Key generated files:
- `_ph3_ed/hw/top.sv` — top level; declares the `ddr_*` nets and wires CoreDLA ↔ EMIF.
- `_ph3_ed/hw/dla_platform_wrapper.sv` — the CoreDLA platform wrapper; authoritative DDR port list.
- `_ph3_ed/coredla_ip/altera_ai_ip/verilog/AGX3_Performance_AGX3/dla_dma_param.svh` — param values.
- `_ph3_ed/coredla_ip/altera_ai_ip/verilog/sequential_ip/dla_dma.sv` — AxSIZE/AxBURST/WSTRB drivers.
- `_ph3_ed/coredla_ip/altera_ai_ip/verilog/sequential_ip/dla_hld_global_load_store.sv` — WSTRB source.
- `_ph3_ed/hw/qsys/ed_zero.tcl` — Platform Designer system: EMIF, AXI bridge, PLLs, connections.

---

## (a) CoreDLA AXI4 "DDR" master port

Authoritative declaration: `dla_platform_wrapper.sv:79-103` (module `dla_platform_wrapper`, ports are
`[MAX_DLA_INSTANCES]=[1]` unpacked arrays; `top.sv` flattens them onto the `ddr_*` nets at
`top.sv:62-81`). Parameter values from `dla_dma_param.svh:4-11`:
`C_DDR_AXI_ADDR_WIDTH=32`, `C_DDR_AXI_DATA_WIDTH=256`, `C_DDR_AXI_READ_ID_WIDTH=2`,
`C_DDR_AXI_WRITE_ID_WIDTH=5`, `C_DDR_AXI_BURST_WIDTH=4`. Burst-field widths are AXI4-standard
(`top.sv:43-45`): `AXI_BURST_LENGTH_WIDTH=8`, `AXI_BURST_SIZE_WIDTH=3`, `AXI_BURST_TYPE_WIDTH=2`.
`top.sv:107-108` hard-asserts `C_DDR_AXI_ADDR_WIDTH==32` and `C_DDR_AXI_DATA_WIDTH==256`.

Direction is from CoreDLA's perspective (it is the AXI **master**). `o_*` = CoreDLA drives, `i_*` =
CoreDLA samples.

| AXI signal | wrapper port | dir | width | notes / driven value |
|---|---|---|---|---|
| **AW** awaddr | `o_ddr_awaddr` | out | 32 | byte address (`top.sv:73,91`) |
| awlen | `o_ddr_awlen` | out | 8 | `= {4'b0, raw_ddr_awlen}`, real length in low 4 bits (`dla_dma.sv:977`); ≤15 ⇒ ≤16 beats |
| awsize | `o_ddr_awsize` | out | 3 | **constant `3'd5`** (`= $clog2(32)`, `dla_dma.sv:980`→`885`) = 32 B/beat, always full width |
| awburst | `o_ddr_awburst` | out | 2 | **constant `2'h1` = INCR** (`dla_dma.sv:981`→`886`); never WRAP/FIXED |
| awid | `o_ddr_awid` | out | 5 | free-running write counter (`dla_dma.sv:1004-1012`); one writer only |
| awvalid | `o_ddr_awvalid` | out | 1 | |
| awready | `i_ddr_awready` | in | 1 | |
| **W** wdata | `o_ddr_wdata` | out | 256 | |
| wstrb | `o_ddr_wstrb` | out | 32 | `= mem_avm_byteenable` — **NOT hard-tied to all-ones** (see §c, WSTRB) |
| wlast | `o_ddr_wlast` | out | 1 | driven (bridge S0 has `USE_S0_WLAST=1`, `ed_zero.tcl:351`) |
| wvalid | `o_ddr_wvalid` | out | 1 | |
| wready | `i_ddr_wready` | in | 1 | |
| **B** bvalid | `i_ddr_bvalid` | in | 1 | CoreDLA's write-ack |
| bready | `o_ddr_bready` | out | 1 | |
| ~~bid~~ | — | — | — | **absent on this boundary**; EMIF-bridge `s0_bid` left unconnected (`top.sv:231`) |
| ~~bresp~~ | — | — | — | **absent**; `USE_S0_BRESP=0` (`ed_zero.tcl:344`) — CoreDLA ignores write response status |
| **AR** araddr | `o_ddr_araddr` | out | 32 | byte address |
| arlen | `o_ddr_arlen` | out | 8 | `= {4'b0, raw_ddr_arlen}` (`dla_dma.sv:882`); ≤15 ⇒ ≤16 beats |
| arsize | `o_ddr_arsize` | out | 3 | **constant `3'd5`** = 32 B/beat (`dla_dma.sv:885`) |
| arburst | `o_ddr_arburst` | out | 2 | **constant `2'h1` = INCR** (`dla_dma.sv:886`) |
| arid | `o_ddr_arid` | out | 2 | tags which of 3 readers issued (`dla_dma.sv:42` comment; config/filter/feature) |
| arvalid | `o_ddr_arvalid` | out | 1 | |
| arready | `i_ddr_arready` | in | 1 | |
| **R** rdata | `i_ddr_rdata` | in | 256 | |
| rid | `i_ddr_rid` | in | 2 | **must be echoed from arid** — CoreDLA demuxes read data to the right reader by rid |
| rvalid | `i_ddr_rvalid` | in | 1 | |
| rready | `o_ddr_rready` | out | 1 | CoreDLA can backpressure read data |
| ~~rresp~~ | — | — | — | **absent**; `USE_S0_RRESP=0` (`ed_zero.tcl:348`) |
| ~~rlast~~ | — | — | — | **absent on this boundary**; EMIF-bridge `s0_rlast` left unconnected (`top.sv:247`); CoreDLA counts beats itself |

**Protocol classification:** full **AXI4** (8-bit AWLEN/ARLEN, per-beat WSTRB, WLAST, INCR bursts,
separate read/write ID) — not AXI3, not AXI4-Lite. It is a **reduced** AXI4 master: it does not
consume BRESP/BID or RRESP/RLAST. In qsys the slave it drives is `altera_axi_bridge` (`AXI_VERSION
{AXI4}`, `ed_zero.tcl:281`), an AXI-MM (not Avalon) interface.

**The physical drop-in point.** `top.sv` does not hand CoreDLA's `o_ddr_*` straight to memory; it
routes them to the exported slave `emif_data_bridge_0.s0` of an `altera_axi_bridge`
(`shell pd` instance, `top.sv:218-249`; exported at `ed_zero.tcl:138`). That S0 slave is 33-bit
address (`ADDR_WIDTH=emif_addr_width=33`, `ed_zero.tcl:280`, `1081`); `top.sv:219,236` pad CoreDLA's
32-bit address with a leading `1'b0`. So our bridge can either (option A) present an AXI4 slave
identical to `emif_data_bridge_0.s0` (33-bit addr, ID 5, data 256, no S0 BRESP/RRESP, WLAST used,
bid/rlast tied off), or (option B) connect directly to `o_ddr_*`/`i_ddr_*` (32-bit addr). Option B
is cleaner and removes the redundant bridge.

---

## (b) hbmc_core Avalon-MM data-path slave

> **Superseded (see `docs/ph3_submodule.md`).** This section documents `hbmc_core.sv` as it existed
> when the bridge (`docs/ph3_bridge_design.md`) was designed against it, and the bridge's Avalon
> master port list below is still accurate — the bridge itself is unchanged. But `axc3000_hyperram_
> axi4.sv` no longer instantiates `hbmc_core`; the datapath now terminates in the `third_party/
> hyperram` submodule's `hyperram_avalon` (a different Avalon-MM slave implementation with the same
> 16-bit-word contract the bridge already targets: `avs_address`/`avs_read`/`avs_write`/
> `avs_writedata`/`avs_byteenable`/`avs_burstcount`/`avs_readdata`/`avs_readdatavalid`/
> `avs_waitrequest`, mapped 1:1 from the bridge's `av_*` ports). `hbmc_core.sv` still exists in the
> tree, relocated to `sim/replay/hbmc_core.sv` and renamed package `hbmc_pkg` during the
> CoreDLA-HyperRAM rename cleanup (test infrastructure for the record-replay integration TB,
> `sim/replay/tb_replay_integ.sv`/`sim/replay/run.sh`). Its original standalone datapath-regression
> TB/script (`sim/hyperbus/tb_axi4_hbmc_bridge.sv`, `sim/hyperbus/run_bridge.sh`) has been removed as
> redundant — `hbmc_core.sv` is **not** in the wrapper's synthesis or the new TB's build
> (`sim/hyperbus/run_hyperram_axi4.sh` explicitly excludes it — see the package-collision caveat in
> `docs/ph3_submodule.md`).

From `sim/replay/hbmc_core.sv:28-36` (+ CSR slave `:21-26`, package `sim/replay/hbmc_pkg.sv`):

| Avalon-MM signal | dir | width | notes |
|---|---|---|---|
| `av_address` | in | 23 | **WORD** address (16-bit words) → 8 M words = 16 MB |
| `av_burstcount` | in | 8 | words per burst → **max 255 words** |
| `av_read` | in | 1 | |
| `av_write` | in | 1 | |
| `av_writedata` | in | 16 | one 16-bit word |
| `av_readdata` | out | 16 | |
| `av_readdatavalid` | out | 1 | one pulse per word read |
| `av_waitrequest` | out | 1 | |

Behavioral facts that constrain the bridge (`hbmc_core.sv`): **single-outstanding** FSM (`IDLE`/`RUN`,
`:58-59`) — accepts a new command only when idle (`can_cmd = (st==IDLE) && !dev_start`, `:88`); no
byte-enable / partial-word write on the data path (writes drive full 16-bit words, RWDS masks *stall*
beats only, `:220-228`); linear bursts only; little-endian word assembly on reads. Separate 6-bit CSR
slave (`csr_address[5:0]`, 32-bit data) for latency/capture-delay/device-register config. Targets
100 MHz today (200 MB/s), see docs/hyperbus.md.

---

## (c) The adaptation gap (what the bridge must do)

**1. Data width 256:16 (16×).** Each CoreDLA AXI beat = 32 bytes = **16 hbmc words**. The bridge
serializes/gathers 16 Avalon word-transfers per AXI data beat: on writes, drain `wdata[255:0]` as 16
words (LSW first to match hbmc little-endian assembly); on reads, accumulate 16 hbmc `readdatavalid`
words into one 256-bit `rdata` beat.

**2. Protocol AXI4 → Avalon-MM.** Split each channel: latch AW+W into an Avalon write burst; latch AR
into an Avalon read burst; generate the AXI **B** response (bvalid) locally when the Avalon write
drains, since hbmc has no write-ack (bid/bresp can be tied off — CoreDLA ignores them). Generate
**R** beats from accumulated hbmc reads. **Echo arid→rid** (2-bit): CoreDLA routes read data to
config/filter/feature reader by rid (`dla_dma.sv:42`), so the bridge must return each read's arid on
its rdata beats. rresp/rlast not required by CoreDLA (but rlast is cheap to drive correctly).

**3. Byte→word address.** CoreDLA drives a 32-bit **byte** address, always 32-byte-aligned
(arsize/awsize = 5). hbmc wants a 23-bit **word** address. Map `av_address = ddr_addr[23:1]`; the low
5 byte-address bits are 0 for an aligned beat, and successive words within a beat increment the word
address by 1. Only `ddr_addr[23:0]` (16 MB) is decoded; `ddr_addr[31:24]` must be 0 (see §d fit).

**4. Burst splitting.** Max AXI burst = 16 beats (`C_DDR_AXI_BURST_WIDTH=4`) × 16 words/beat =
**256 words**, but `av_burstcount` is 8-bit (**max 255 words**). So a full-length AXI burst overflows
one Avalon burst by one word and **must be split** into ≥2 Avalon bursts (e.g. 16×16 → 15-beat +
1-beat, or issue one Avalon burst per AXI beat = 16 words, simplest and always legal). A single
32-byte AXI beat (16 words) always fits one Avalon burst.

**5. Single-outstanding serialization.** hbmc is single-outstanding; CoreDLA's master is
multi-outstanding (bridge S0 `READ/WRITE_ISSUING_CAPABILITY=64`, `ed_zero.tcl:295,356`; up to 4 read
IDs, 32 write IDs). The bridge must accept one AXI transaction at a time into hbmc and hold
awready/arready deasserted (or queue) while hbmc is in `RUN`. CoreDLA also prioritizes writes over
reads and will re-assert an unacked read (`dla_dma.sv:841-903`) — standard AXI, handled by normal
ready/valid backpressure.

**6. CDC (CoreDLA clk_ddr ↔ HyperRAM clk).** CoreDLA's AXI DDR port is synchronous to `clk_ddr`
(`dla_platform_wrapper` `clk_ddr` port; in the stock design `clk_ddr` = the EMIF-generated user clock
`emif.s0_axi4_clock_out` → `emif_clk_bridge` → `top.sv` `clk_ddr`, `ed_zero.tcl:93,95,139`, nominal
**200 MHz** `EX_DESIGN_USER_PLL_OUTPUT_FREQ_MHZ`, `ed_zero.tcl:401`). CoreDLA's **compute** clock
`clk_dla` is separate (`dla_pll.outclk0` → `dla_clk_bridge` → `top.sv` `clk_dla`,
`ed_zero.tcl:99,140`; requested `dla_freq_mhz=600.0`, `ed_zero.tcl:1099`, auto-retuned down to
achievable fmax by `dla_adjust_pll.tcl`). CoreDLA already crosses clk_dla↔clk_ddr **internally**.
**Design lever:** because we own `clk_ddr` in the PH3 system, the simplest path is to drive CoreDLA's
`clk_ddr` **from the HyperRAM controller's own clock domain** — then the AXI4↔Avalon bridge is
single-clock and needs no CDC of its own. If instead we want CoreDLA's DDR port faster than the
HyperRAM clock, the bridge (or a re-inserted `altera_axi_bridge`) must do a proper AXI clock crossing.
Either way, CDC must go through the `rtl/common/` wrappers (AGENTS.md), never hand-rolled in the
datapath. (Note: these frequencies are C-series-E6 devkit numbers from the stock example; AXC3000 is
E7S and its HyperRAM is 1.2 V/250 MHz per board_bringup — re-derive for our device.)

**7. WSTRB / partial writes — the read-modify-write question.** This is the one that decides whether
the bridge needs read-modify-write. Evidence from the generated RTL:
- The DDR write path is **not** the all-ones branch. `dla_dma.sv:826` (`o_ddr_wstrb = all-ones`) is
  guarded by `if (LAYOUT_TRANSFORM_WRITEBACK_MODE)` (`dla_dma.sv:778`), and this config has
  `LAYOUT_TRANSFORM_WRITEBACK_MODE=0` (`dla_dma_param.svh:130`). So the **else** branch is active:
  `assign o_ddr_wstrb = dla_wstrb;` (`dla_dma.sv:834`), sourced from the feature writer, which in
  `dla_hld_global_load_store.sv:904` drives `axi_wstrb = mem_avm_byteenable`.
- So structurally, **WSTRB is byte-enable-driven and can carry a partial (non-all-ones) mask** — it
  is not hard-wired to `32'hFFFFFFFF`. hbmc's data path has **no per-byte write mask** on Avalon
  (`hbmc_core.sv:220-228` drives whole 16-bit words; RWDS only stalls beats). If CoreDLA ever asserts
  a partial WSTRB, the bridge must do **read-modify-write** (read the 256-bit word, merge masked
  bytes, write back) — expensive on 16×-narrow HyperRAM.
- **Unresolved:** whether the feature writer *ever* actually asserts a non-all-ones byteenable is
  determined inside `dla_dma_writer` / the writer's byteenable computation, which is in the
  **encrypted** CoreDLA RTL (`dla_dma_writer` is instantiated at `dla_dma.sv:936` but has no
  plaintext module body in the generated tree). The width alignment is suggestive but not conclusive:
  `FEATURE_WRITER_DATA_BYTES=32 = DDR_DATA_BYTES=32` (`dla_dma_param.svh:15-16`), i.e. the writer is
  naturally full-DDR-word wide, and DM/DBI are disabled on the EMIF because "CoreDLA can't take
  advantage of these" (`ed_zero.tcl:371,377`) — hinting writes are normally full-width. **Confirming
  all-ones WSTRB requires a simulation of the CoreDLA DDR master** (see §e). Until then, design the
  bridge to at least *detect* a partial WSTRB and either RMW or flag it.

---

## (d) Bandwidth reality (stall factor)

| Interface | width | clock | peak BW |
|---|---|---|---|
| CoreDLA DDR AXI4 port | 256 bit = 32 B/beat | 200 MHz (stock `clk_ddr`) | **6.4 GB/s** |
| Stock LPDDR4x32 (what it was sized for) | 32 bit ×2 | 1066 MHz PHY (`ed_zero.tcl:1090`) | ~8.5 GB/s |
| HyperRAM W957D8NB (×8 DDR) @ 100 MHz | 8 bit ×2 = 2 B/clk | 100 MHz (hbmc today) | **200 MB/s** |
| HyperRAM @ 250 MHz (AXC3000 board max) | 2 B/clk | 250 MHz | **500 MB/s** |

- **Width ratio alone = 256/16 = 16×**: CoreDLA consumes 16 hbmc words per AXI beat, so even
  clock-for-clock the port is starved 15 of every 16 cycles.
- **Raw peak-vs-peak:** 6.4 GB/s ÷ 0.5 GB/s ≈ **12.8×** (best case, HyperRAM at 250 MHz); ÷ 0.2 GB/s
  = **32×** (hbmc at today's 100 MHz vs a 200 MHz DDR port).
- **Per-transaction overhead** makes small bursts worse: every HyperRAM access pays 6 CA beats +
  ~6 (up to 12 with a refresh-collision latency doubling) latency beats before data
  (`hbmc_core.sv:78`, docs/hyperbus.md). Amortized over a 256-word transfer that is ~5%; over a
  single 16-word AXI beat it is ~40–75%. So sustained effective HyperRAM BW is ≈ 1.9 B/clk for large
  bursts, far less for scattered small ones.
- **Net:** the bridge caps CoreDLA's global-memory throughput at **≈1/16 of the port's designed
  rate** (worse with small-burst overhead). The estimator already shows `DDR FILTER READS REQUIRED`
  on *every* ad-toycar inference (`models/arch/README.md`; weights are re-streamed from external
  memory each inference, not M20K-resident), so inference latency in the PH3 system will be
  **HyperRAM-bandwidth-bound** — dominated by streaming weights through a 16-bit pipe. This is the
  central performance consequence to record in `results/` (as an estimate) before/after real
  measurement.

---

## (e) Verified-from-generated-RTL vs assumed-pending-simulation

**Verified (read directly off the generated RTL/Tcl, cited above):**
- Port is full AXI4, master, `ADDR=32`, `DATA=256`, `READ_ID=2`, `WRITE_ID=5`, burst-width 4 (≤16
  beats). (`dla_platform_wrapper.sv:79-103`, `dla_dma_param.svh:4-11`, `top.sv:107-108`.)
- AxSIZE constant `3'd5` (32 B/beat, full width) and AxBURST constant INCR — never narrow, never
  WRAP/FIXED. (`dla_dma.sv:885-886,980-981`.)
- Reduced AXI4: CoreDLA does **not** consume BRESP/BID or RRESP/RLAST. (`dla_platform_wrapper.sv`
  B/R groups; `ed_zero.tcl:344,348`; `top.sv:231,247`.)
- rid must be echoed from arid to route read data to the right reader. (`dla_dma.sv:42`.)
- WSTRB path is byte-enable-driven (`= mem_avm_byteenable`), *not* hard-tied all-ones, because
  `LAYOUT_TRANSFORM_WRITEBACK_MODE=0`. (`dla_dma.sv:778,834`; `dla_hld_global_load_store.sv:904`;
  `dla_dma_param.svh:130`.)
- Stock wiring the bridge replaces: `add_connection emif_data_bridge_0.m0 → emif_0.s0_axi4`
  (`ed_zero.tcl:85-86`); CoreDLA master → exported `emif_data_bridge_0.s0` (`ed_zero.tcl:138`,
  `top.sv:218-249`). Clocks: DDR port on EMIF user clock ~200 MHz (`ed_zero.tcl:93,95,401`), compute
  on `dla_pll` req. 600 MHz (`ed_zero.tcl:99,1099`).
- No arch/ed_zero cap on CoreDLA's DDR footprint: the `.arch` `dma` block sets only widths
  (`models/arch/AGX3_Performance.arch:27-35`), no `mem_size`/`ddr_size`; the EMIF is an 8 Gbit (1 GB)
  x32 die (`ed_zero.tcl:412,409`) but that is just the stock DRAM, not a CoreDLA constraint. CoreDLA
  addresses a flat 32-bit (4 GB) space and the compiler packs buffers from low addresses.

**Assumed / pending simulation or a model compile (do NOT treat as measured):**
- **Whether WSTRB is ever partial.** The writer's byteenable computation is in encrypted RTL
  (`dla_dma_writer`, no plaintext body). Width alignment + DM/DBI-disabled hint full-width writes, but
  only a **behavioral sim of the CoreDLA DDR master** (or a captured AXI trace on hardware) can prove
  WSTRB is always `32'hF...F`. **This is the riskiest unknown** — it decides whether the bridge needs
  read-modify-write, which would further gut the already-16×-narrow write path.
- **Actual burst-length distribution** (how often CoreDLA issues the full 16-beat burst vs shorter)
  and **outstanding-transaction depth** in practice — bounds are known (≤16 beats, ≤64 issuing) but
  the real traffic mix needs a sim to size bridge buffering / whether single-outstanding serialization
  is a throughput cliff.
- **Address-map fit for ad-toycar in 16 MB.** Structurally nothing forces >16 MB for a sub-MB model
  (267 KB INT8 weights + activations + config), but the concrete base/offset map CoreDLA's compiler
  assigns must be read from a real `dla_compiler` compile of ad-toycar (the generated `.bin` / mapping)
  to confirm every DDR reference lands in `[0, 16 MB)` and `ddr_addr[31:24]==0`.
- **AXC3000 clock/BW numbers.** All frequencies quoted are the stock C-series-E6 devkit example's
  (200 MHz DDR user clock, 600 MHz requested DLA). AXC3000 is E7S with a 1.2 V/250 MHz HyperRAM
  (board_bringup); the real `clk_ddr` we choose and the achievable DLA fmax must be re-derived and
  measured for our device.
</content>
</invoke>
