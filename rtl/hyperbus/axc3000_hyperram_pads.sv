// axc3000_hyperram_pads — the board/synth-only layer for the PH3 HyperRAM memory subsystem: turns
// the AXI4 slave + `clk`/`clk2x` into real AXC3000 HyperBus pins, and is the ONE place that picks
// WHICH physical HyperBus PHY/I-O implementation drives those pins. See axc3000_hyperram_axi4.sv's
// header for why the split-vs-inout boundary exists; this file is the inout side of that boundary
// and is NOT expected to be exercised by any Verilator TB (only lint-checked there).
//
// IO_VARIANT selects the implementation (generate-if, only the selected branch elaborates):
//
//   "SPLIT_PHY" (legacy): axc3000_hyperram_axi4 (bridge -> hyperram_avalon -> hyperbus_phy dispatch,
//     PHY_VARIANT param) -> this module's own tristate reintroduction. This is the path exercised by
//     sim/hyperbus/tb_axc3000_hyperram_axi4.sv (PHY_VARIANT="GENERIC") and by the standalone
//     quartus/ph3_hyperram_char/ char build (PHY_VARIANT="SDR"). UNCHANGED by this file's rewrite.
//
//   "DDIO_GPIO" (default; THE proven AXC3000 200 MHz-class board build): bridge -> hyperbus_avalon
//     -> hyperbus_ctrl -> hyperbus_gpio_io, exactly mirroring third_party/hyperram/fpga/axc3000/
//     top.sv (docs/ph3_submodule.md, docs/ph3_integration.md). hyperbus_gpio_io owns the real
//     `inout` pads itself (vendor altera_gpio CK cell + raw tennm_ph2 DDIO DQ/RWDS TX atoms) and is
//     NOT Verilator-simulable (device primitives) -- exactly like hyperbus_phy_altera before it, this
//     branch is synthesis-only. It measured 341.1/332.3 MB/s write/read at CK=175 MHz on THIS board
//     (third_party/hyperram/README.md "Performance & test status", DDR x8 row) -- this is the
//     wiring that number came from, not the SPLIT_PHY/hyperbus_phy_altera.sv path (which is capped
//     around ~176 MHz by a different, inferior CK-generation scheme; see hyperbus_gpio_io.sv header
//     "why this exists"). Single-ended CK only: the AXC3000 HyperRAM ball-out has no hb_ck_n pin
//     (third_party/hyperram/fpga/axc3000/top.sv, third_party/hyperram/fpga/axc3000/pins.tcl), so
//     THIS module's port list has none either -- DIFF_CK/hb_ck_n only exist inside the legacy
//     SPLIT_PHY branch's internal wiring to axc3000_hyperram_axi4, tied off, never exported.
//
// CTRL_* parameters below are the third_party/hyperram/fpga/axc3000/top.sv silicon-tuned constants
// for the DDIO_GPIO branch's hyperbus_ctrl instance (device-row-aligned chop boundary, write
// coalescing, latency trim) -- cited from that file, not invented (AGENTS.md).
`ifndef AXC3000_HYPERRAM_PADS_SV
`define AXC3000_HYPERRAM_PADS_SV
module axc3000_hyperram_pads
  import hyperbus_pkg::*;
#(
    parameter int    DATA_W            = 256,
    parameter int    ADDR_W            = 32,
    parameter int    WID_W             = 5,
    parameter int    RID_W             = 2,
    parameter int    LEN_W             = 8,
    parameter int    HB_ADDR_W         = 23,
    parameter int    HB_BURST_W        = 8,

    // ---- board/PHY variant select (see header) ----
    parameter        IO_VARIANT        = "DDIO_GPIO",  // "DDIO_GPIO" (board-proven) | "SPLIT_PHY" (legacy)

    // ---- SPLIT_PHY branch only: forwarded to axc3000_hyperram_axi4 unchanged ----
    parameter        PHY_VARIANT       = "SDR",   // board/char build: real SDR PHY (not GENERIC)
    parameter int    POR_DELAY_CYCLES  = 0,
    parameter int    MAX_BURST_WORDS   = 0,

    // ---- shared latency / read-preamble knobs (both branches) ----
    parameter int    LATENCY_CLOCKS    = 6,
    parameter int    RD_PREAMBLE_SKIP  = 1,        // W957D8NB: 1 (third_party/hyperram/fpga/axc3000/top.sv)

    // ---- DDIO_GPIO branch only: hyperbus_ctrl silicon-tuned constants (top.sv defaults, cited) ----
    parameter int          CTRL_MAX_BURST_WORDS      = 1024,      // = one device ROW
    parameter logic [15:0] CTRL_BURST_BOUNDARY_WORDS = 16'h0400,  // = the 1024-word device ROW
    parameter bit          CTRL_WR_COALESCE          = 1'b1,      // CS#-coalescing (issue #1 #4)
    parameter int          CTRL_WR_COALESCE_WAIT     = 8,
    parameter int          CTRL_WR_LAT_TRIM          = 3,         // device write window opens 3 CK early

    // ---- DDIO_GPIO branch only: hyperbus_gpio_io alignment knobs (top.sv defaults, cited) ----
    parameter bit          IO_TX_B_DLY   = 1'b1,
    parameter bit          IO_CK_DIN_HI  = 1'b1,
    parameter              IO_CK_GEN     = "FABRIC2X"  // proven-clean CK generator at CK<=175 MHz
) (
    input  logic                 clk,      // word/CK-rate clock (controller + all TX launches)
    input  logic                 clk2x,    // DDIO_GPIO: 2x-CK core-only clock (hyperbus_gpio_io.clk_smp);
                                            // SPLIT_PHY: forwarded to axc3000_hyperram_axi4 unchanged
    input  logic                 reset_n,

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

    // ---- HyperBus device balls (real inout; board pins). Single-ended CK only -- no hb_ck_n. ----
    inout  wire  [7:0]           hb_dq,
    inout  wire                  hb_rwds,
    output logic                 hb_ck,
    output logic                 hb_cs_n,
    output logic                 hb_rst_n,

    output logic                 init_done,
    output logic                 wstrb_partial_seen,
    output logic                 hi_addr_seen
);

  generate
    if (IO_VARIANT == "DDIO_GPIO") begin : g_ddio_gpio
      // =========================================================================================
      // Board-proven chain (third_party/hyperram/fpga/axc3000/top.sv, mirrored 1:1):
      //   axi4_hbmc_bridge -> av_*/avs_* (16-bit word Avalon-MM) -> hyperbus_avalon (front end)
      //   -> cmd/wr/rd -> hyperbus_ctrl (protocol engine) -> phy_* -> hyperbus_gpio_io (real pins)
      // =========================================================================================
      logic rst;
      assign rst = ~reset_n;

      logic [HB_ADDR_W-1:0]  av_address;
      logic [HB_BURST_W-1:0] av_burstcount;
      logic                  av_read, av_write;
      logic [15:0]           av_writedata, av_readdata;
      logic                  av_readdatavalid, av_waitrequest;

      localparam int AVS_ADDR_W = 32;
      localparam int AVS_LEN_W  = 16;

      logic [AVS_ADDR_W-1:0] avs_address;
      logic [15:0]           avs_writedata, avs_readdata;
      logic [1:0]            avs_byteenable;
      logic [AVS_LEN_W-1:0]  avs_burstcount;
      logic                  avs_read, avs_write, avs_readdatavalid, avs_waitrequest;

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

      // av_* -> avs_* 1:1 mapping (docs/ph3_submodule.md): zero-extend the word address, keep the
      // register-select MSB = 0 (memory space); byteenable tied all-ones (bridge writes full words).
      assign avs_address    = {{(AVS_ADDR_W-1-HB_ADDR_W){1'b0}}, 1'b0, av_address};
      assign avs_burstcount = {{(AVS_LEN_W-HB_BURST_W){1'b0}}, av_burstcount};
      assign avs_read       = av_read;
      assign avs_write      = av_write;
      assign avs_writedata  = av_writedata;
      assign avs_byteenable = 2'b11;
      assign av_readdata      = avs_readdata;
      assign av_readdatavalid = avs_readdatavalid;
      assign av_waitrequest   = avs_waitrequest;

      // hyperbus_ctrl's own INIT_CR0 default is the device's raw POR image (5-clock latency), not
      // derived from LATENCY_CLOCKS -- compute it here so the programmed CR0 always matches the
      // controller's own wait count, mirroring axc3000_hyperram_axi4.sv's HB_INIT_CR0 and
      // hyperbus_ctrl.sv's own local default.
      localparam logic [15:0] HB_INIT_CR0 =
          {1'b1, 3'b000, 4'b1111, hb_clocks_to_latency_code(LATENCY_CLOCKS), 1'b1, 3'b111};

      // ---- issue #13 fix-set ties (third_party/hyperram bumped to commit b544bb7, "Merge
      //      issue-13-instrumented: the W957D8NB write defect root-caused and FIXED on silicon
      //      (issue #13)"). THIS is the branch that actually elaborates on real hardware: IO_VARIANT
      //      defaults to "DDIO_GPIO" (see the module header and axc3000_hyperram_axi4_hw.tcl's "NOTE
      //      (honest, updated)"), and neither ed_zero.tcl's instantiate_hyperram nor the PD component's
      //      HDL_PARAMETER list (quartus/ip/axc3000_hyperram_axi4/axc3000_hyperram_axi4_hw.tcl) expose
      //      IO_VARIANT, so this generate-if branch is the one Quartus actually synthesizes for the
      //      CoreDLA-integrated system -- NOT the g_split_phy branch below (axc3000_hyperram_axi4.sv /
      //      hyperram_avalon), which only elaborates if a caller explicitly overrides IO_VARIANT. This
      //      branch's hyperbus_ctrl (u_ctrl below) and hyperbus_gpio_io (u_io below) instances had NO
      //      dbg_* port connections at all before this edit -- every one of hyperbus_ctrl's dbg_wr_
      //      lat_trim/dbg_lat_clocks/.../dbg_spray_defuse ports (and hyperbus_gpio_io's
      //      dbg_ck_stretch_off) has no port default (Verilator rejects one; see hyperbus_ctrl.sv's
      //      port comments), so leaving them unconnected let Quartus tie every one of them to 0 = FIX
      //      OFF: this is very likely the root cause behind the "DDIO_GPIO HyperRAM path corrupts
      //      CONTIGUOUS writes" CoreDLA-on-silicon hang this retest is chasing.
      //
      //      Same silicon-proven fix-set values as axc3000_hyperram_axi4.sv (see that file's header
      //      for the full derivation): fpga/axc3000/README.md's ERR_COUNT=0, 25-run 8192-word soak
      //      REG_DBG=0x0007_1263 = wrtrim=3 lat=6 prewin=1 pn=4 marker=0 posthold=0 ckstretchoff=0
      //      contig=1 endcw=1 defuse=1. wrtrim/lat here are tied to this module's own CTRL_WR_LAT_TRIM
      //      / LATENCY_CLOCKS parameters (already 3 / 6 by default, i.e. board-proven) rather than
      //      re-declared as bare constants, so a future reparam stays self-consistent.
      localparam logic [3:0] DBG_WR_LAT_TRIM_PROVEN = 4'(CTRL_WR_LAT_TRIM);
      localparam logic [3:0] DBG_LAT_CLOCKS_PROVEN  = 4'(LATENCY_CLOCKS);
      localparam logic [2:0] DBG_PREWIN_N_PROVEN    = 3'd4;   // "pn=4" -- soak-proven heal depth

      logic        cmd_valid, cmd_ready, cmd_read, cmd_reg, cmd_wrap;
      logic [31:0] cmd_addr;
      logic [15:0] cmd_len;
      logic        wr_valid, wr_ready, wr_last;
      logic [15:0] wr_data;
      logic [1:0]  wr_strb;
      logic        rd_valid, rd_ready, rd_last;
      logic [15:0] rd_data;
      logic [1:0]  fe_dbg_state;

      hyperbus_avalon #(
          .DQ_WIDTH(8), .DATA_WIDTH(16), .ADDR_WIDTH(AVS_ADDR_W), .LEN_WIDTH(AVS_LEN_W)
      ) u_fe (
          .clk(clk), .rst(rst),
          .avs_address(avs_address), .avs_read(avs_read), .avs_write(avs_write),
          .avs_writedata(avs_writedata), .avs_byteenable(avs_byteenable),
          .avs_burstcount(avs_burstcount),
          .avs_readdata(avs_readdata), .avs_readdatavalid(avs_readdatavalid),
          .avs_waitrequest(avs_waitrequest),
          // issue #13: wrap_en has no port default (mandatory tie, see the localparam block above);
          // OFF here (linear bursts only) -- the REG_WRAP wrapped-write probe is test-only and has no
          // CSR/host path wired into this memory-subsystem build to ever assert it.
          .wrap_en(1'b0),
          .cmd_valid(cmd_valid), .cmd_ready(cmd_ready), .cmd_read(cmd_read), .cmd_reg(cmd_reg),
          .cmd_wrap(cmd_wrap), .cmd_addr(cmd_addr), .cmd_len(cmd_len),
          .wr_valid(wr_valid), .wr_ready(wr_ready), .wr_data(wr_data), .wr_strb(wr_strb),
          .wr_last(wr_last),
          .rd_valid(rd_valid), .rd_ready(rd_ready), .rd_data(rd_data), .rd_last(rd_last),
          .dbg_state(fe_dbg_state));

      logic        phy_cs_n, phy_rst_n, phy_ck_en, phy_dq_oe, phy_rwds_oe, phy_rd_arm;
      logic [15:0] phy_dq_o;
      logic [1:0]  phy_rwds_o;
      logic [15:0] phy_dq_i;
      logic        phy_dq_i_valid, phy_rwds_i;
      logic [3:0]  ctrl_dbg_state;
      logic [5:0]  ctrl_dbg_rem, ctrl_dbg_seg;

      hyperbus_ctrl #(
          .DQ_WIDTH(8), .DATA_WIDTH(16), .ADDR_WIDTH(32), .LEN_WIDTH(16),
          .LATENCY_CLOCKS(LATENCY_CLOCKS), .FIXED_LATENCY(1'b1),
          .MAX_BURST_WORDS(CTRL_MAX_BURST_WORDS), .PROGRAM_CR(1'b1),
          .POR_DELAY_CYCLES(POR_DELAY_CYCLES), .INIT_CR0(HB_INIT_CR0),
          .BURST_BOUNDARY_WORDS(CTRL_BURST_BOUNDARY_WORDS), .WR_COMMIT_READ(1'b0),
          .WR_COALESCE(CTRL_WR_COALESCE), .WR_COALESCE_WAIT(CTRL_WR_COALESCE_WAIT),
          .WR_CHOP_REPLAY(1'b0), .WR_CHOP_PAUSE_CYCLES(0), .WR_CHOP_PAUSE_CK(1'b0),
          .WR_LAT_TRIM(CTRL_WR_LAT_TRIM)
      ) u_ctrl (
          .clk(clk), .rst(rst),
          .cmd_valid(cmd_valid), .cmd_ready(cmd_ready), .cmd_read(cmd_read), .cmd_reg(cmd_reg),
          .cmd_wrap(cmd_wrap), .cmd_addr(cmd_addr), .cmd_len(cmd_len),
          .wr_valid(wr_valid), .wr_ready(wr_ready), .wr_data(wr_data), .wr_strb(wr_strb),
          .wr_last(wr_last),
          .rd_valid(rd_valid), .rd_ready(rd_ready), .rd_data(rd_data), .rd_last(rd_last),
          .busy(), .init_done(init_done), .err_underrun(), .err_timeout(),
          .phy_cs_n(phy_cs_n), .phy_rst_n(phy_rst_n), .phy_ck_en(phy_ck_en),
          .phy_dq_o(phy_dq_o), .phy_dq_oe(phy_dq_oe),
          .phy_rwds_o(phy_rwds_o), .phy_rwds_oe(phy_rwds_oe), .phy_rd_arm(phy_rd_arm),
          .phy_dq_i(phy_dq_i), .phy_dq_i_valid(phy_dq_i_valid), .phy_rwds_i(phy_rwds_i),
          .dbg_state(ctrl_dbg_state), .dbg_rd_wptr(ctrl_dbg_rem), .dbg_rd_rptr(ctrl_dbg_seg),
          // issue #13 live controller knobs (mandatory, no defaults; see localparam block above).
          .dbg_wr_lat_trim(DBG_WR_LAT_TRIM_PROVEN), .dbg_lat_clocks(DBG_LAT_CLOCKS_PROVEN),
          .dbg_cr0_reprog(1'b0),          // one-shot re-init strobe; never auto-fire
          .dbg_prewin_drive(1'b1),        // fix set: ON
          .dbg_prewin_n(DBG_PREWIN_N_PROVEN),  // fix set: pn=4
          .dbg_prewin_marker(1'b0),       // diagnostic-only marker; OFF
          .dbg_postwin_hold(1'b0),        // not part of the proven fix set; OFF
          .dbg_prewin_contig(1'b1),       // fix set: ON
          .dbg_end_cwrite(1'b1),          // fix set: ON
          .dbg_spray_defuse(1'b1));       // fix set: ON

      hyperbus_gpio_io #(
          .DQ_WIDTH(8), .RD_PREAMBLE_SKIP(RD_PREAMBLE_SKIP),
          .TX_B_DLY(IO_TX_B_DLY), .CK_DIN_HI(IO_CK_DIN_HI), .CK_GEN(IO_CK_GEN)
      ) u_io (
          .clk(clk), .clk_smp(clk2x), .rst(rst),
          .phy_cs_n(phy_cs_n), .phy_rst_n(phy_rst_n), .phy_ck_en(phy_ck_en),
          .phy_dq_o(phy_dq_o), .phy_dq_oe(phy_dq_oe),
          .phy_rwds_o(phy_rwds_o), .phy_rwds_oe(phy_rwds_oe), .phy_rd_arm(phy_rd_arm),
          .dbg_ck_stretch_off(1'b0),   // issue #13 L-E knob; OFF in the proven fix set (REG_DBG[15]=0)
          .phy_dq_i(phy_dq_i), .phy_dq_i_valid(phy_dq_i_valid), .phy_rwds_i(phy_rwds_i),
          .hb_ck(hb_ck), .hb_cs_n(hb_cs_n), .hb_rst_n(hb_rst_n),
          .hb_dq(hb_dq), .hb_rwds(hb_rwds));

    end else begin : g_split_phy
      // =========================================================================================
      // Legacy path (unchanged): axc3000_hyperram_axi4 (split pins) + tristate reintroduction here.
      // Exercised by sim/hyperbus/tb_axc3000_hyperram_axi4.sv (PHY_VARIANT="GENERIC") and the
      // standalone quartus/ph3_hyperram_char/ build (PHY_VARIANT="SDR").
      // =========================================================================================
      logic [7:0] hb_dq_o;
      logic       hb_dq_oe;
      logic [7:0] hb_dq_i;
      logic       hb_rwds_o;
      logic       hb_rwds_oe;
      logic       hb_rwds_i;
      logic       hb_ck_n_unused;

      axc3000_hyperram_axi4 #(
          .DATA_W(DATA_W), .ADDR_W(ADDR_W), .WID_W(WID_W), .RID_W(RID_W), .LEN_W(LEN_W),
          .HB_ADDR_W(HB_ADDR_W), .HB_BURST_W(HB_BURST_W),
          .PHY_VARIANT(PHY_VARIANT), .DIFF_CK(1'b0), .LATENCY_CLOCKS(LATENCY_CLOCKS),
          .POR_DELAY_CYCLES(POR_DELAY_CYCLES), .RD_PREAMBLE_SKIP(RD_PREAMBLE_SKIP),
          .MAX_BURST_WORDS(MAX_BURST_WORDS)
      ) u_wrapper (
          .clk(clk), .clk2x(clk2x), .reset_n(reset_n),
          .s_axi_awid(s_axi_awid), .s_axi_awaddr(s_axi_awaddr), .s_axi_awlen(s_axi_awlen),
          .s_axi_awsize(s_axi_awsize), .s_axi_awburst(s_axi_awburst), .s_axi_awvalid(s_axi_awvalid),
          .s_axi_awready(s_axi_awready),
          .s_axi_wdata(s_axi_wdata), .s_axi_wstrb(s_axi_wstrb), .s_axi_wlast(s_axi_wlast),
          .s_axi_wvalid(s_axi_wvalid), .s_axi_wready(s_axi_wready),
          .s_axi_bid(s_axi_bid), .s_axi_bresp(s_axi_bresp), .s_axi_bvalid(s_axi_bvalid),
          .s_axi_bready(s_axi_bready),
          .s_axi_arid(s_axi_arid), .s_axi_araddr(s_axi_araddr), .s_axi_arlen(s_axi_arlen),
          .s_axi_arsize(s_axi_arsize), .s_axi_arburst(s_axi_arburst), .s_axi_arvalid(s_axi_arvalid),
          .s_axi_arready(s_axi_arready),
          .s_axi_rid(s_axi_rid), .s_axi_rdata(s_axi_rdata), .s_axi_rresp(s_axi_rresp),
          .s_axi_rlast(s_axi_rlast), .s_axi_rvalid(s_axi_rvalid), .s_axi_rready(s_axi_rready),
          .hb_ck(hb_ck), .hb_ck_n(hb_ck_n_unused), .hb_cs_n(hb_cs_n), .hb_rst_n(hb_rst_n),
          .hb_dq_o(hb_dq_o), .hb_dq_oe(hb_dq_oe), .hb_dq_i(hb_dq_i),
          .hb_rwds_o(hb_rwds_o), .hb_rwds_oe(hb_rwds_oe), .hb_rwds_i(hb_rwds_i),
          .init_done(init_done),
          .wstrb_partial_seen(wstrb_partial_seen), .hi_addr_seen(hi_addr_seen));

`ifdef VERILATOR
      assign hb_dq   = hb_dq_oe   ? hb_dq_o   : 8'h00;
      assign hb_rwds = hb_rwds_oe ? hb_rwds_o : 1'b0;
`else
      assign hb_dq   = hb_dq_oe   ? hb_dq_o   : 8'bz;
      assign hb_rwds = hb_rwds_oe ? hb_rwds_o : 1'bz;
`endif
      assign hb_dq_i   = hb_dq;
      assign hb_rwds_i = hb_rwds;
    end
  endgenerate

endmodule
`endif
