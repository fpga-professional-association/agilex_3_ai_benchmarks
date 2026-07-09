// axc3000_hyperram_pads — tiny board/synth wrapper: turns axc3000_hyperram_axi4's SPLIT HyperBus
// pins (hb_dq_o/oe/i, hb_rwds_o/oe/i) into real bidirectional `inout` balls.
//
// axc3000_hyperram_axi4.sv (and everything it instantiates, down to hyperram_avalon and its PHY) is
// deliberately `inout`-free so it stays Verilator-clean and its testbench can resolve the shared
// HyperBus bus against a second split driver (the golden device model) — see the header of
// axc3000_hyperram_axi4.sv. This module is the ONE place the tristate is reintroduced, and only for
// synthesis/board use: it is what the Platform Designer component, a board top.sv, or the standalone
// Quartus char build (quartus/ph3_hyperram_char/) instantiate at the actual HyperRAM package pins.
//
// Pure wiring: no logic, no registers, no clock gating, no reset. Under VERILATOR the tristate
// releases to `0` instead of `'z` (Verilator has no true tristate net); this module is not expected
// to be exercised by the Verilator TBs (which drive axc3000_hyperram_axi4 directly, split pins), but
// it must still elaborate/lint cleanly under `--lint-only` since it is on the Quartus fileset list.
`ifndef AXC3000_HYPERRAM_PADS_SV
`define AXC3000_HYPERRAM_PADS_SV
module axc3000_hyperram_pads #(
    parameter int    DATA_W            = 256,
    parameter int    ADDR_W            = 32,
    parameter int    WID_W             = 5,
    parameter int    RID_W             = 2,
    parameter int    LEN_W             = 8,
    parameter int    HB_ADDR_W         = 23,
    parameter int    HB_BURST_W        = 8,
    parameter        PHY_VARIANT       = "SDR",   // board/char build: real SDR PHY (not GENERIC)
    parameter bit    DIFF_CK           = 1'b1,
    parameter int    LATENCY_CLOCKS    = 6,
    parameter int    POR_DELAY_CYCLES  = 0,
    parameter int    RD_PREAMBLE_SKIP  = 0,
    parameter int    MAX_BURST_WORDS   = 0
) (
    input  logic                 clk,
    input  logic                 clk2x,
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

    // ---- HyperBus device balls (real inout; board pins) ----
    inout  wire  [7:0]           hb_dq,
    inout  wire                  hb_rwds,
    output logic                 hb_ck,
    output logic                 hb_ck_n,
    output logic                 hb_cs_n,
    output logic                 hb_rst_n,

    output logic                 init_done,
    output logic                 wstrb_partial_seen,
    output logic                 hi_addr_seen
);
  logic [7:0] hb_dq_o;
  logic       hb_dq_oe;
  logic [7:0] hb_dq_i;
  logic       hb_rwds_o;
  logic       hb_rwds_oe;
  logic       hb_rwds_i;

  axc3000_hyperram_axi4 #(
      .DATA_W(DATA_W), .ADDR_W(ADDR_W), .WID_W(WID_W), .RID_W(RID_W), .LEN_W(LEN_W),
      .HB_ADDR_W(HB_ADDR_W), .HB_BURST_W(HB_BURST_W),
      .PHY_VARIANT(PHY_VARIANT), .DIFF_CK(DIFF_CK), .LATENCY_CLOCKS(LATENCY_CLOCKS),
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
      .hb_ck(hb_ck), .hb_ck_n(hb_ck_n), .hb_cs_n(hb_cs_n), .hb_rst_n(hb_rst_n),
      .hb_dq_o(hb_dq_o), .hb_dq_oe(hb_dq_oe), .hb_dq_i(hb_dq_i),
      .hb_rwds_o(hb_rwds_o), .hb_rwds_oe(hb_rwds_oe), .hb_rwds_i(hb_rwds_i),
      .init_done(init_done),
      .wstrb_partial_seen(wstrb_partial_seen), .hi_addr_seen(hi_addr_seen));

  // ---- tristate balls: drive when output-enabled, else release. VERILATOR has no real tristate net
  //      (this module is not driven by the Verilator TBs, but must still elaborate/lint), so release
  //      to 0 there instead of 'z, matching the convention used elsewhere in this tree. ----
`ifdef VERILATOR
  assign hb_dq   = hb_dq_oe   ? hb_dq_o   : 8'h00;
  assign hb_rwds = hb_rwds_oe ? hb_rwds_o : 1'b0;
`else
  assign hb_dq   = hb_dq_oe   ? hb_dq_o   : 8'bz;
  assign hb_rwds = hb_rwds_oe ? hb_rwds_o : 1'bz;
`endif
  assign hb_dq_i   = hb_dq;
  assign hb_rwds_i = hb_rwds;
endmodule
`endif
