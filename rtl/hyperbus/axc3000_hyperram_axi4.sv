// axc3000_hyperram_axi4 — PH3 HyperRAM memory subsystem: ONE reduced-AXI4 slave (the drop-in
// replacement for CoreDLA's LPDDR4 EMIF) + a SPLIT HyperBus pin conduit. See docs/ph3_integration.md.
//
// Internally: axi4_hbmc_bridge (AXI4 slave <-> Avalon-MM master, UNCHANGED) -> hyperram_avalon (the
// third_party/hyperram submodule's Avalon-MM slave + protocol engine + PHY). The AXI4 slave here is
// byte-for-byte the CoreDLA "DDR" master contract from docs/ph3_interfaces.md (DATA=256, ADDR=32,
// WRITE_ID=5, READ_ID=2, AxSIZE const 3'd5, AxBURST const INCR, AxLEN<=15; reduced AXI4 — master
// ignores BRESP/BID and RRESP/RLAST but the bridge still runs valid/ready handshakes and echoes
// arid->rid). The Platform Designer component wrapper
// quartus/ip/axc3000_hyperram_axi4/axc3000_hyperram_axi4_hw.tcl points at THIS file.
//
// ============================ PHY STATUS (PH3 blocker #1: CLOSED) ============================
// This wrapper no longer contains a PHY stub. `hyperram_avalon` (third_party/hyperram, pinned
// submodule, commit c6f5d2b) supplies a real, silicon-proven HyperBus PHY: measured 96.8-342 MB/s
// on THIS board (Arrow AXC3000, Agilex 3 A3CY100BM16AE7S + Winbond W957D8NB) via its SDR PHY variant
// (PHY_VARIANT="SDR"); a behavioral GENERIC PHY (PHY_VARIANT="GENERIC", the default here) is used for
// simulation runs under Verilator. DO NOT modify anything under third_party/hyperram/ — it is a
// pinned submodule owned by another session; use it as-is.
//
// SPLIT-PIN convention (decisive design choice, do not deviate): this module exposes hb_dq_o/oe/i
// and hb_rwds_o/oe/i as SEPARATE signals, NOT `inout`. hyperram_avalon itself has no inout ports, so
// keeping the split all the way out of this module keeps it Verilator-clean and lets a testbench
// resolve the shared bus against a second driver (the golden device model) with two split drivers —
// the old `inout` tristate stub could not be resolved against a second driver in Verilator, which is
// exactly why this module switched away from it. The `inout` board balls live in a separate tiny
// wrapper, rtl/hyperbus/axc3000_hyperram_pads.sv, which instantiates THIS module and is what the PD
// component / board top.sv / the standalone Quartus char build put at the pins.
//
// Remaining gaps (honest, PLAN §3 LV6 / docs/ph3_integration.md "What remains"): the PD clock plan
// still needs to actually supply `clk2x` from the board IOPLL; the board pinout + 25 MHz IOPLL
// reparam for the real clk/clk2x pair; .sdc closure for the HyperBus pins; the CoreDLA CSR
// start/done handshake; and the structural HyperRAM bandwidth ceiling relative to CoreDLA's DDR
// bandwidth need (PLAN §4/§5). This module's AXI4<->Avalon datapath is sim-proven
// (sim/hyperbus/tb_axc3000_hyperram_axi4.sv, against the submodule's golden hyperram_model); the
// wrapper as a whole has not been run on hardware yet.
// ================================================================================================
//
// Clocking: `clk` is the word/CK-rate system clock (== hyperram_avalon's `clk`, == the bridge's
// `clk`). `clk2x` is the 2x byte clock and is wired to hyperram_avalon's `clk90` port (for
// PHY_VARIANT="GENERIC" this is a genuine +90 degree phase at the same rate as clk; for
// PHY_VARIANT="SDR" hyperram_avalon repurposes this same port as the 2x byte clock at 0 degrees —
// see third_party/hyperram/rtl/hyperram_avalon.sv header and docs/ph3_submodule.md). `clk_ref` is
// tied to `clk` (both PHY_VARIANT="GENERIC" and "SDR" ignore/tie it). `reset_n` is active-low,
// synchronous (from the PD reset_handler); `hyperram_avalon.rst` wants active-high, so it is
// inverted here.
//
// RTL discipline (AGENTS.md / PLAN §3 LV1): the sub-modules (bridge, hyperram_avalon and its
// internals) own their sync-reset/reset-less split; this wrapper is pure structural wiring. No async
// reset, no clock gating.
`ifndef AXC3000_HYPERRAM_AXI4_SV
`define AXC3000_HYPERRAM_AXI4_SV
module axc3000_hyperram_axi4
  import hyperbus_pkg::*;
#(
    parameter int    DATA_W            = 256,       // CoreDLA AXI data width (bits)
    parameter int    ADDR_W            = 32,        // CoreDLA AXI byte-address width
    parameter int    WID_W             = 5,         // AXI write-ID width (awid/bid)
    parameter int    RID_W             = 2,         // AXI read-ID width (arid/rid)
    parameter int    LEN_W             = 8,         // AXI AxLEN width (AXI4)
    parameter int    HB_ADDR_W         = 23,        // hyperram_avalon word-address width used (of 32)
    parameter int    HB_BURST_W        = 8,         // bridge av_burstcount width

    // ---- forwarded to hyperram_avalon (third_party/hyperram/rtl/hyperram_avalon.sv) ----
    parameter        PHY_VARIANT       = "GENERIC", // sim TB uses GENERIC; Quartus board/char: SDR
    parameter bit    DIFF_CK           = 1'b1,      // drive hb_ck_n
    parameter int    LATENCY_CLOCKS    = 6,         // CA1 -> data, clocks (fixed-latency, POR default)
    parameter int    POR_DELAY_CYCLES  = 0,         // POR init delay clocks (0 = sim; ~150us on HW)
    parameter int    RD_PREAMBLE_SKIP  = 0,         // SDR PHY: read-strobe preamble edges to ignore
    parameter int    MAX_BURST_WORDS   = 0          // 0 = no chop (sim); set tCSM/tCK-derived on HW
) (
    input  logic                 clk,       // word/CK-rate system clock (hyperram_avalon .clk)
    input  logic                 clk2x,     // 2x byte clock (hyperram_avalon .clk90)
    input  logic                 reset_n,   // active-low, synchronous (from PD reset_handler)

    // ---- AXI4 slave: write address (AW) ----
    input  logic [WID_W-1:0]     s_axi_awid,
    input  logic [ADDR_W-1:0]    s_axi_awaddr,
    input  logic [LEN_W-1:0]     s_axi_awlen,
    input  logic [2:0]           s_axi_awsize,
    input  logic [1:0]           s_axi_awburst,
    input  logic                 s_axi_awvalid,
    output logic                 s_axi_awready,

    // ---- AXI4 slave: write data (W) ----
    input  logic [DATA_W-1:0]    s_axi_wdata,
    input  logic [DATA_W/8-1:0]  s_axi_wstrb,
    input  logic                 s_axi_wlast,
    input  logic                 s_axi_wvalid,
    output logic                 s_axi_wready,

    // ---- AXI4 slave: write response (B) ----
    output logic [WID_W-1:0]     s_axi_bid,
    output logic [1:0]           s_axi_bresp,
    output logic                 s_axi_bvalid,
    input  logic                 s_axi_bready,

    // ---- AXI4 slave: read address (AR) ----
    input  logic [RID_W-1:0]     s_axi_arid,
    input  logic [ADDR_W-1:0]    s_axi_araddr,
    input  logic [LEN_W-1:0]     s_axi_arlen,
    input  logic [2:0]           s_axi_arsize,
    input  logic [1:0]           s_axi_arburst,
    input  logic                 s_axi_arvalid,
    output logic                 s_axi_arready,

    // ---- AXI4 slave: read data (R) ----
    output logic [RID_W-1:0]     s_axi_rid,
    output logic [DATA_W-1:0]    s_axi_rdata,
    output logic [1:0]           s_axi_rresp,
    output logic                 s_axi_rlast,
    output logic                 s_axi_rvalid,
    input  logic                 s_axi_rready,

    // ---- HyperBus device pins (SPLIT; no inout here — see header) ----
    output logic                 hb_ck,      // clock (real PHY-generated, per PHY_VARIANT)
    output logic                 hb_ck_n,    // complementary clock (DIFF_CK)
    output logic                 hb_cs_n,    // chip select (active low)
    output logic                 hb_rst_n,   // device reset (active low)
    output logic [7:0]           hb_dq_o,    // data, drive side
    output logic                 hb_dq_oe,   // data, output-enable
    input  logic [7:0]           hb_dq_i,    // data, sample side
    output logic                 hb_rwds_o,  // read/write data strobe, drive side
    output logic                 hb_rwds_oe, // read/write data strobe, output-enable
    input  logic                 hb_rwds_i,  // read/write data strobe, sample side

    // ---- status ----
    output logic                 init_done,           // POR + CR0 programming complete (hyperram_avalon)

    // ---- sticky status (integration hooks; safe to leave unconnected) ----
    output logic                 wstrb_partial_seen, // a non-all-ones WSTRB was seen (RMW needed!)
    output logic                 hi_addr_seen        // an address above the 16 MB window was seen
);
  // active-high synchronous reset for hyperram_avalon (reset_n is already sync'd in the PD system)
  logic rst;
  assign rst = ~reset_n;

  // ---- internal Avalon-MM: bridge master (av_*) <-> hyperram_avalon slave (avs_*), 16-bit words ----
  logic [HB_ADDR_W-1:0]  av_address;
  logic [HB_BURST_W-1:0] av_burstcount;
  logic                  av_read, av_write;
  logic [15:0]           av_writedata, av_readdata;
  logic                  av_readdatavalid, av_waitrequest;

  // hyperram_avalon defaults: DQ_WIDTH=8, DATA_WIDTH=16, ADDR_WIDTH=32, LEN_WIDTH=16 (all left at
  // their package defaults; HB_ADDR_W/HB_BURST_W above only cover what the bridge actually drives).
  localparam int AVS_ADDR_W  = 32;
  localparam int AVS_LEN_W   = 16;

  // hyperram_avalon's own INIT_CR0 default is HB_CR0_RESET (the device's raw, un-reprogrammed POR
  // image: fixed-latency bit set, latency CODE 0000 = 5 clocks) -- it is NOT derived from that same
  // module's LATENCY_CLOCKS default (6), so instantiating hyperram_avalon with PROGRAM_CR=1 (its
  // default) and leaving INIT_CR0 at its default would program the DEVICE to 5-clock latency while
  // the controller's own FSM waits LATENCY_CLOCKS cycles -- a one-clock read-data misalignment.
  // hyperbus_ctrl.sv's OWN local default builds INIT_CR0 correctly from LATENCY_CLOCKS (and
  // sim/tb_avalon.sv works around the same top-level gap the same way); mirror that computation here
  // so this wrapper's forwarded LATENCY_CLOCKS always matches what actually gets programmed into the
  // device's CR0, whatever LATENCY_CLOCKS is instantiated with.
  localparam logic [15:0] HB_INIT_CR0 =
      {1'b1, 3'b000, 4'b1111, hb_clocks_to_latency_code(LATENCY_CLOCKS), 1'b1, 3'b111};
  logic [AVS_ADDR_W-1:0] avs_address;
  logic [15:0]            avs_writedata, avs_readdata;
  logic [1:0]              avs_byteenable;
  logic [AVS_LEN_W-1:0]   avs_burstcount;
  logic                    avs_read, avs_write, avs_readdatavalid, avs_waitrequest;

  // ---- AXI4 -> Avalon-MM bridge (UNCHANGED; datapath proven in sim/hyperbus/tb_axi4_hbmc_bridge.sv
  //      and, through this wrapper, in sim/hyperbus/tb_axc3000_hyperram_axi4.sv) ----
  axi4_hbmc_bridge #(
      .DATA_W(DATA_W), .ADDR_W(ADDR_W), .WID_W(WID_W), .RID_W(RID_W),
      .LEN_W(LEN_W), .HB_ADDR_W(HB_ADDR_W), .HB_BURST_W(HB_BURST_W)
  ) u_bridge (
      .clk(clk), .rst(rst),
      .awid(s_axi_awid), .awaddr(s_axi_awaddr), .awlen(s_axi_awlen), .awsize(s_axi_awsize),
      .awburst(s_axi_awburst), .awvalid(s_axi_awvalid), .awready(s_axi_awready),
      .wdata(s_axi_wdata), .wstrb(s_axi_wstrb), .wlast(s_axi_wlast),
      .wvalid(s_axi_wvalid), .wready(s_axi_wready),
      .bid(s_axi_bid), .bresp(s_axi_bresp), .bvalid(s_axi_bvalid), .bready(s_axi_bready),
      .arid(s_axi_arid), .araddr(s_axi_araddr), .arlen(s_axi_arlen), .arsize(s_axi_arsize),
      .arburst(s_axi_arburst), .arvalid(s_axi_arvalid), .arready(s_axi_arready),
      .rid(s_axi_rid), .rdata(s_axi_rdata), .rresp(s_axi_rresp), .rlast(s_axi_rlast),
      .rvalid(s_axi_rvalid), .rready(s_axi_rready),
      .av_address(av_address), .av_burstcount(av_burstcount),
      .av_read(av_read), .av_write(av_write),
      .av_writedata(av_writedata), .av_readdata(av_readdata),
      .av_readdatavalid(av_readdatavalid), .av_waitrequest(av_waitrequest),
      .wstrb_partial_seen(wstrb_partial_seen), .hi_addr_seen(hi_addr_seen));

  // ---- av_* -> avs_* 1:1 mapping (docs/ph3_submodule.md): zero-extend the word address, keep the
  //      register-select MSB = 0 (memory space); byteenable tied to all-ones because the bridge has
  //      no byte-enable output (writes are always full 16-bit words) -- exactly as
  //      third_party/hyperram/fpga/axc3000/top.sv does. ----
  assign avs_address    = {{(AVS_ADDR_W-1-HB_ADDR_W){1'b0}}, 1'b0, av_address};
  assign avs_burstcount = {{(AVS_LEN_W-HB_BURST_W){1'b0}}, av_burstcount};
  assign avs_read       = av_read;
  assign avs_write      = av_write;
  assign avs_writedata  = av_writedata;
  assign avs_byteenable = 2'b11;
  assign av_readdata      = avs_readdata;
  assign av_readdatavalid = avs_readdatavalid;
  assign av_waitrequest   = avs_waitrequest;

  // ---- issue #13 fix-set + read-eye-cal ties (third_party/hyperram bumped to commit b544bb7,
  //      "Merge issue-13-instrumented: the W957D8NB write defect root-caused and FIXED on silicon
  //      (issue #13)"). hyperram_avalon's cal_*/dbg_*/wrap_en ports have NO port defaults (Verilator
  //      rejects them; docs/INTERFACES.md v9 + the submodule's own port comments: "every instantiation
  //      ties them"), so leaving them unconnected -- as this wrapper did until this edit, for both the
  //      pre-existing cal_* set and the brand-new issue-#13 dbg_*/wrap_en set -- lets Quartus silently
  //      tie every one of them to 0. For the dbg_* set that means FIX OFF: a build was mid-compile in
  //      exactly that state when it was killed for this retest.
  //
  //      Values below are the SILICON-PROVEN fix-set from the ERR_COUNT=0, 25-run 8192-word soak
  //      (third_party/hyperram/fpga/axc3000/README.md "Performance & test status": runtime fix set
  //      REG_DBG=0x0007_1263) and the board's own POR seeds (fpga/axc3000/top.sv: DBG_RESET=
  //      0x0000_0063, REG_CAL=0x0000_0002) -- decoded per fpga/axc3000/sysconsole/dbg_poke.tcl's
  //      REG_DBG bit map:
  //        REG_DBG = 0x0007_1263 = wrtrim=3 lat=6 prewin=1 pn=4 marker=0 posthold=0 ckstretchoff=0
  //                                contig=1 endcw=1 defuse=1
  //      i.e. the fix set is {prewin_drive, prewin_n=4, prewin_contig, end_cwrite, spray_defuse} ON;
  //      {prewin_marker (attribution-only diagnostic, never a fix), postwin_hold (unused by this fix),
  //       cr0_reprog (one-shot re-init strobe -- never auto-fire it), wrap_en (REG_WRAP wrapped-write
  //       PROBE, test-only, no meaning in a real memory build)} OFF. dbg_ck_stretch_off has no
  //      equivalent in this GENERIC/SDR hyperbus_phy codepath (it is a hyperbus_gpio_io-only knob; see
  //      rtl/hyperbus/axc3000_hyperram_pads.sv's DDIO_GPIO branch instead).
  localparam logic [3:0] DBG_WR_LAT_TRIM_PROVEN = 4'd3;   // matches top.sv / axc3000_hyperram_pads.sv
      // DDIO_GPIO's CTRL_WR_LAT_TRIM=3 ("device write window opens 3 CK early", silicon-measured).
      // hyperram_avalon does NOT forward a WR_LAT_TRIM parameter override of its own (hyperbus_ctrl's
      // own default is 0 and is unreachable through hyperram_avalon's parameter list) -- this runtime
      // dbg_wr_lat_trim tie is the ONLY way to apply the proven trim value in this codepath.
  localparam logic [3:0] DBG_LAT_CLOCKS_PROVEN = 4'(LATENCY_CLOCKS);
      // = this wrapper's own LATENCY_CLOCKS parameter (POR default 6, board-proven), so a future
      // recompile with a different LATENCY_CLOCKS stays self-consistent instead of silently drifting.
  localparam logic [2:0] DBG_PREWIN_N_PROVEN = 3'd4;   // "pn=4" -- soak-proven trailing-CK heal depth
  localparam logic [HB_CAL_PREAMBLE_SKIP_WIDTH-1:0] CAL_PREAMBLE_SKIP_PROVEN =
      HB_CAL_PREAMBLE_SKIP_WIDTH'(RD_PREAMBLE_SKIP);
      // Reproduces this wrapper's own RD_PREAMBLE_SKIP parameter (board: 1, W957D8NB read-strobe
      // preamble) as the runtime cal_preamble_skip seed: hyperbus_phy_sdr.sv resets its internal
      // cal_preamble_skip tracking FROM this same RD_PREAMBLE_SKIP parameter at reset, so tying the
      // port to a *constant* equal to the parameter exactly reproduces that POR behavior with no live
      // retune wired here (REG_CAL=0x2 on the board bandwidth-test build encodes this same
      // preamble_skip=1; cal_capture_phase/cal_rx_tap/cal_pair_skew are all 0 there too -- see below).

  // ---- HyperRAM controller + real PHY (third_party/hyperram submodule, pinned; DO NOT edit) ----
  /* verilator lint_off PINCONNECTEMPTY */
  hyperram_avalon #(
      .PHY_VARIANT      (PHY_VARIANT),
      .DIFF_CK          (DIFF_CK),
      .LATENCY_CLOCKS   (LATENCY_CLOCKS),
      .INIT_CR0         (HB_INIT_CR0),
      .POR_DELAY_CYCLES (POR_DELAY_CYCLES),
      .RD_PREAMBLE_SKIP (RD_PREAMBLE_SKIP),
      .MAX_BURST_WORDS  (MAX_BURST_WORDS)
  ) u_hyperram (
      .clk     (clk),
      .clk90   (clk2x),   // GENERIC: real +90deg; SDR: repurposed as the 2x byte clock, 0deg
      .clk_ref (clk),     // tie for GENERIC/SDR (both ignore/tie this port)
      .rst     (rst),
      // ---- runtime PHY read-eye calibration (mandatory, no defaults; see localparam block above) --
      .cal_capture_phase (1'b0),                    // = CAPTURE_PHASE parameter's own default (0)
      .cal_preamble_skip (CAL_PREAMBLE_SKIP_PROVEN),
      .cal_rx_tap        ('0),                      // unused in SDR (DDIO tap select); 0 in board REG_CAL
      .cal_pair_skew     (1'b0),                    // unused in SDR (DDIO byte-pairing); 0 in board REG_CAL
      // ---- issue #13 live controller knobs (mandatory, no defaults; see localparam block above) ----
      .dbg_wr_lat_trim   (DBG_WR_LAT_TRIM_PROVEN),
      .dbg_lat_clocks    (DBG_LAT_CLOCKS_PROVEN),
      .dbg_cr0_reprog    (1'b0),                    // one-shot re-init strobe; never auto-fire
      .dbg_prewin_drive  (1'b1),                    // fix set: ON
      .dbg_prewin_n      (DBG_PREWIN_N_PROVEN),      // fix set: pn=4
      .dbg_prewin_marker (1'b0),                    // diagnostic-only marker; OFF (real data, not 0xA5xx)
      .dbg_postwin_hold  (1'b0),                    // not part of the proven fix set; OFF
      .dbg_prewin_contig (1'b1),                    // fix set: ON
      .dbg_end_cwrite    (1'b1),                    // fix set: ON
      .dbg_spray_defuse  (1'b1),                    // fix set: ON
      .wrap_en           (1'b0),                    // REG_WRAP probe is test-only; OFF for real traffic
      .avs_address       (avs_address),
      .avs_read          (avs_read),
      .avs_write         (avs_write),
      .avs_writedata     (avs_writedata),
      .avs_byteenable    (avs_byteenable),
      .avs_burstcount    (avs_burstcount),
      .avs_readdata      (avs_readdata),
      .avs_readdatavalid (avs_readdatavalid),
      .avs_waitrequest   (avs_waitrequest),
      .hb_ck      (hb_ck),
      .hb_ck_n    (hb_ck_n),
      .hb_cs_n    (hb_cs_n),
      .hb_rst_n   (hb_rst_n),
      .hb_dq_o    (hb_dq_o),
      .hb_dq_oe   (hb_dq_oe),
      .hb_dq_i    (hb_dq_i),
      .hb_rwds_o  (hb_rwds_o),
      .hb_rwds_oe (hb_rwds_oe),
      .hb_rwds_i  (hb_rwds_i),
      .init_done  (init_done),
      .err_underrun (/* unused: no sticky status output in this wrapper's port list to fold a write-
                      * data-underrun pulse into without changing the AXI4/PD-component interface (out
                      * of scope for this fix-set wiring); left visibly dangling, same convention as
                      * dbg_bus below. See axc3000_hyperram_pads.sv's DDIO_GPIO branch, which ties its
                      * hyperbus_ctrl .err_underrun() the same way. */),
      .dbg_bus    (/* unused: bring-up debug tap, see hyperram_avalon.sv */));
  /* verilator lint_on PINCONNECTEMPTY */
endmodule
`endif
