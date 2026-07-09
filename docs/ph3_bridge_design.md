# PH3 bridge design: `axi4_hbmc_bridge` (CoreDLA AXI4 DDR → HyperRAM Avalon-MM)

Implements the adapter identified in `docs/ph3_interfaces.md` §c: it presents the *reduced AXI4*
slave that the FPGA AI Suite CoreDLA "DDR" master expects, and drives a 16-bit Avalon-MM slave as an
Avalon master. This document is the canonical description of the RTL in
`rtl/hyperbus/axi4_hbmc_bridge.sv`; it is proven in simulation by
`sim/hyperbus/tb_axi4_hbmc_bridge.sv` (real `hbmc_core` + `w957d8nb_bfm`, no abstraction of the
controller). It is a **datapath proof**, not a hardware or CoreDLA-integration result.

> **Bridge is unchanged; its downstream target changed (see `docs/ph3_submodule.md`).** Everything
> below — the FSM, the address/data mapping, the WSTRB handling, the v1 limitations — describes
> `axi4_hbmc_bridge.sv` exactly as built, and none of it changed when the wrapper adopted the
> `third_party/hyperram` submodule. What changed is what sits *behind* the bridge's Avalon master
> port: `rtl/hyperbus/axc3000_hyperram_axi4.sv` now wires the bridge's `av_*` ports 1:1 onto the
> submodule's `hyperram_avalon` (`avs_*`) instead of `hbmc_core`. The bridge's own TB
> (`tb_axi4_hbmc_bridge.sv`, this doc's subject) still targets `hbmc_core` directly and still
> passes — it remains valid as a bridge-FSM regression test — while the new
> `sim/hyperbus/tb_axc3000_hyperram_axi4.sv` proves the same bridge against the submodule.

## The contract (from `docs/ph3_interfaces.md`, treated as given)

CoreDLA AXI4 master: `DATA=256`, `ADDR=32`, `WRITE_ID=5`, `READ_ID=2`. `AxSIZE` is a **constant**
`3'd5` (always full 32-byte / 256-bit beats), `AxBURST` is a **constant** INCR, `AxLEN ≤ 15`
(≤ 16 beats). Reduced AXI4: the master ignores `BRESP`/`BID` and `RRESP`/`RLAST` *values*, but the
bridge must **echo `arid → rid`** so read data routes to the correct CoreDLA reader, and still runs
standard valid/ready handshakes on B and R. The master is multi-outstanding (up to 64) but the bridge
**serializes** — one AXI transaction at a time — because `hbmc_core` is single-outstanding.

`hbmc_core` Avalon-MM data slave (driven as master): `av_address[22:0]` = HyperRAM **word** address,
`av_burstcount[7:0]` = words, `av_read`/`av_write`, `av_writedata[15:0]`/`av_readdata[15:0]`,
`av_readdatavalid`, `av_waitrequest`. Handshake (verified from `hbmc_core.sv`): a command fires the
cycle `av_waitrequest` is low while `av_read|av_write` is asserted; the **first write word is consumed
with the command**, then `hbmc` pulls each subsequent write word by dropping `av_waitrequest` again
(its `need_word`); reads return each word via `av_readdatavalid`. It returns to `IDLE` after each
burst, so a re-asserted command simply waits (with `av_waitrequest` high) until the controller is idle
— which is exactly what makes the bridge's serialization free.

## Address map

AXI byte address is 32-byte aligned (`AxSIZE=5`). `word_addr = byte_addr[23:1]` — only the low 24 bits
(16 MB) are decoded. Each 256-bit AXI beat = **16 consecutive HyperRAM words**; `word_addr` advances
by 16 per beat. `byte_addr[31:24]` must be zero; a non-zero high address is latched into the sticky
status output `hi_addr_seen` (flagged, not decoded) — see `docs/ph3_interfaces.md` §c-3/§d for why
16 MB is the decoded window.

## Data mapping (256 ↔ 16, ×16)

Each AXI beat carries 16 words. Word `i` of a beat is `data[16*i +: 16]`, mapped to word address
`base + i` (ascending, **LSW first** — word 0 = `data[15:0]` at the lowest address) to match
`hbmc_core`'s little-endian word assembly on reads. Writes drain the 256-bit beat as words 0..15;
reads accumulate 16 `av_readdatavalid` words 0..15 back into a 256-bit R beat. This makes readback
bit-exact: a value written at `data[16*i +: 16]` returns at `rdata[16*i +: 16]`.

## Serializing FSM

Single clock `clk` (= the shared HyperRAM/DDR clock; see "v1 limitations"). Synchronous reset for the
architectural state (FSM, counters, sticky flags, `word_addr`); the beat data buffers (`wbeat`,
`rbeat`) and the latched transaction attributes (`awid`/`arid`/`awlen`/`arlen`) are reset-less
datapath registers, always written before read (AGENTS.md Hyperflex/PLAN §3 LV1 discipline).

States:

- **`S_IDLE`** — accept `AW` (write priority) or `AR`. Latch id/len, set `word_addr = AxADDR[23:1]`,
  `beat_cnt = 0`, flag `hi_addr_seen` if `AxADDR[31:24] != 0`. → `S_W_DATA` or `S_R_XFER`.
- **`S_W_DATA`** — `wready` high; on `wvalid`, latch the 256-bit `wdata` into `wbeat`, sample `wstrb`
  (set sticky `wstrb_partial_seen` if `wstrb != all-ones`), reset `wword_idx = 0`. → `S_W_XFER`.
- **`S_W_XFER`** — one hbmc write burst of `av_burstcount = 16` at `word_addr`, feeding
  `av_writedata = wbeat[16*wword_idx +: 16]` with `av_write` held high. Every cycle `av_waitrequest`
  is low a word is consumed (word 0 rides the command; words 1..15 ride each subsequent
  `need_word`), so `wword_idx++`. After word 15: `word_addr += 16`; if this was the last beat
  (`beat_cnt == awlen`) → `S_W_RESP`, else `beat_cnt++` and → `S_W_DATA` for the next W beat.
- **`S_W_RESP`** — `bvalid`, `bid = awid`, `bresp = OKAY`; on `bready` → `S_IDLE`.
- **`S_R_XFER`** — `av_read` high, `av_burstcount = 16` at `word_addr`; on the accept cycle
  (`av_waitrequest` low) drop `av_read`, reset `rword_idx = 0`, → `S_R_COLLECT`.
- **`S_R_COLLECT`** — on each `av_readdatavalid`, `rbeat[16*rword_idx +: 16] = av_readdata`,
  `rword_idx++`. After word 15 → `S_R_RESP`.
- **`S_R_RESP`** — `rvalid`, `rid = arid`, `rdata = rbeat`, `rresp = OKAY`,
  `rlast = (beat_cnt == arlen)`; on `rready`, `word_addr += 16`; if last beat → `S_IDLE`, else
  `beat_cnt++` → `S_R_XFER`.

Serialization is implicit: the bridge issues at most one `hbmc` command at a time and only advances a
channel when `av_waitrequest`/`av_readdatavalid` say the controller is ready, and a new command placed
while the controller is still in `RUN` simply stalls (`av_waitrequest` high) until it returns to
`IDLE`. Write-before-read ordering (needed for write-then-readback) is therefore free: a read command
cannot fire until the preceding write burst has fully drained and `hbmc` is idle.

## WSTRB / partial writes (v1 scope)

v1 implements **full-width writes** (assumes `wstrb` all-ones) and writes all 16 words of every beat.
A non-all-ones `wstrb` is **detected** and raised on the sticky `wstrb_partial_seen` status output —
the bridge does **not** silently corrupt, but it also does **not** implement read-modify-write. RMW is
explicitly out of v1 scope. This is the open risk from `docs/ph3_interfaces.md` §c-7/§e: whether
CoreDLA's feature writer ever asserts a partial `wstrb` is buried in the encrypted `dla_dma_writer`
RTL and can only be resolved by a behavioral sim of the CoreDLA DDR master or a captured AXI trace.
Until then, `wstrb_partial_seen` is the trip-wire that tells us RMW is actually required.

## Throughput note (future work, not implemented)

Each AXI beat is issued as its own 16-word hbmc burst — simplest and always legal (16 ≤ 255 =
`av_burstcount` max). Every hbmc burst re-pays the CA + latency overhead (≈ 12 beats), so a 16-word
beat is ~40–75 % overhead (`docs/ph3_interfaces.md` §d). The **CA-amortization optimization** —
coalescing contiguous AXI beats of one INCR burst into a single ≤ 255-word hbmc burst (e.g. a
16-beat AXI burst → 256 words → 15-beat + 1-beat, or 240 + 16) — is deferred as future work. It does
not change correctness, only overhead.

## v1 limitations (built in on purpose)

1. **Single clock / no CDC.** `clk` is the shared HyperRAM/DDR clock. State CDC (driving CoreDLA's
   `clk_ddr` faster than the HyperRAM clock) is out of scope; at integration it is a
   `rtl/common/async_fifo` AXI clock-crossing wrapper, never hand-rolled in the datapath (AGENTS.md,
   `docs/ph3_interfaces.md` §c-6).
2. **Full-width writes only.** Partial `wstrb` is detected (`wstrb_partial_seen`) but not handled;
   read-modify-write is not implemented.
3. **Serialized / one-outstanding.** One AXI transaction is processed at a time (CoreDLA is
   multi-outstanding up to 64; `hbmc_core` is single-outstanding). No transaction reordering, no
   interleaving of the 2 read IDs / 5 write IDs.
4. **Per-beat 16-word hbmc bursts.** No CA-amortization / burst coalescing (see above).
5. **16 MB decoded window.** Only `byte_addr[23:0]`; higher addresses are flagged (`hi_addr_seen`),
   not serviced.
