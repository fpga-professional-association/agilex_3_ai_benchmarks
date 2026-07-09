// char_top — PH3 HyperRAM standalone char/bring-up top (branch ph3-hyperram-axi4-coredla,
// PH3_SUBMODULE_SPEC.md DELIVERABLE 5). Unlike quartus/ph3_bridge_char (which characterized the
// OLD hbmc_core/stub datapath with the HyperBus pins virtualized because there was no real PHY),
// this build targets the axc3000_hyperram_axi4 wrapper (rtl/hyperbus/axc3000_hyperram_axi4.sv) with
// the SUBMODULE'S real SDR PHY (PHY_VARIANT="SDR") through the board-pads wrapper
// (rtl/hyperbus/axc3000_hyperram_pads.sv, DELIVERABLE 1b) — so hb_dq/hb_rwds/hb_cs_n/hb_ck/hb_rst_n
// are REAL inout balls at the vetted AXC3000 pinout (quartus/constraints/axc3000_board.tcl), giving
// the Fitter something to actually place-and-route I/O timing against, not virtual pins.
//
// The CoreDLA-facing AXI4 slave is still ~600 functional bits — far more than fit on real package
// pins and there is no traffic generator here (this is I/O + fabric characterization, not a
// bandwidth test; that is qsys/make_bw_sys.tcl's job over in third_party/hyperram/fpga/axc3000/).
// Every AXI4 bit is therefore VIRTUAL_PIN'd in ph3_hyperram_char.qsf, exactly like
// quartus/ph3_bridge_char/char_top.sv did for its bridge/hbmc port list.
//
// Clock plan (see quartus/ph3_hyperram_char/qsys/make_char_clkgen.tcl): a small Platform-Designer
// system char_clkgen (IOPLL + reset controller only, no JTAG master) turns the board's 25 MHz XO
// (CLK_25M_C) into TWO related clocks the SDR PHY needs — clk (50 MHz, CK word rate) and clk2x
// (100 MHz, SDR byte rate, wired to the wrapper's clk2x port) — plus a synchronised active-high
// fabric reset. This is the "PD clock-plan regen to supply clk2x" gap flagged in
// axc3000_hyperram_axi4.sv's header as no longer open, at least for this standalone char build (the
// real CoreDLA-integrated Platform Designer system, quartus/ip/axc3000_hyperram_axi4/, still needs
// its own clk2x sink added — DELIVERABLE 4, tracked separately).
//
// Honesty per AGENTS.md: this is a BRING-UP characterization build. The .sdc false-paths the
// off-chip HyperBus pins (see quartus/constraints/ph3_hyperram_char.sdc) rather than closing real
// I/O timing to the W957D8NB package — that calibration is future hardware-bring-up work, not
// something `quartus_syn`/`quartus_fit` can validate without the physical board. A synthesis/fit
// pass here proves the wrapper + real SDR PHY are structurally sound on A3CY100BM16AE7S and reports
// real ALM/DSP/M20K/fmax numbers for the fabric logic; it does NOT prove the device talks to a real
// HyperRAM (that remains hardware-gated, see docs/ph3_submodule.md).
`default_nettype none
module char_top (
    // ---- board clock / reset (quartus/constraints/axc3000_board.tcl) ----
    input  wire         CLK_25M_C,   // 25 MHz fixed XO (PIN_A7, 1.2 V)
    input  wire         USER_BTN,    // S2, active-low, weak pull-up (PIN_A12, 1.2 V)

    // ---- HyperRAM (Winbond W957D8NB, single-ended x8 HyperBus, 1.2 V) ----
    inout  wire [7:0]   hb_dq,       // DQ[7:0]
    inout  wire         hb_rwds,     // RWDS
    output wire         hb_cs_n,     // chip select
    output wire         hb_ck,       // HyperBus clock (single-ended board: no hb_ck_n pin)
    output wire         hb_rst_n,    // device reset

    // ---- user LEDs (active-low, 3.3-V LVCMOS) — quick visual STATUS ----
    output wire         LED1,        // lit = wrapper init_done
    output wire         RLED,        // lit = wstrb_partial_seen | hi_addr_seen (sticky)
    output wire         GLED,        // lit = char_clkgen PLL locked

    // ---- AXI4 slave: write address (AW) ----
    input  wire [4:0]   awid,
    input  wire [31:0]  awaddr,
    input  wire [7:0]   awlen,
    input  wire [2:0]   awsize,
    input  wire [1:0]   awburst,
    input  wire         awvalid,
    output wire         awready,

    // ---- AXI4 slave: write data (W) ----
    input  wire [255:0] wdata,
    input  wire [31:0]  wstrb,
    input  wire         wlast,
    input  wire         wvalid,
    output wire         wready,

    // ---- AXI4 slave: write response (B) ----
    output wire [4:0]   bid,
    output wire [1:0]   bresp,
    output wire         bvalid,
    input  wire         bready,

    // ---- AXI4 slave: read address (AR) ----
    input  wire [1:0]   arid,
    input  wire [31:0]  araddr,
    input  wire [7:0]   arlen,
    input  wire [2:0]   arsize,
    input  wire [1:0]   arburst,
    input  wire         arvalid,
    output wire         arready,

    // ---- AXI4 slave: read data (R) ----
    output wire [1:0]   rid,
    output wire [255:0] rdata,
    output wire [1:0]   rresp,
    output wire         rlast,
    output wire         rvalid,
    input  wire         rready,

    // ---- sticky status ----
    output wire         wstrb_partial_seen,
    output wire         hi_addr_seen
);
  // ---- clock/reset backbone: char_clkgen (Platform Designer, qsys/char_clkgen.qsys) ----
  wire clk;             // 50 MHz CK word clock
  wire clk2x;            // 100 MHz SDR byte clock (wrapper .clk2x)
  wire pll_locked;
  wire fabric_rst;       // synchronous, active-high

  char_clkgen u_clkgen (
      .clk_ref_clk        (CLK_25M_C),
      .clk_clk             (clk),
      .clk2x_clk           (clk2x),
      .locked_export       (pll_locked),
      .reset_in_reset      (~USER_BTN),   // button pressed (low) => assert active-high reset
      .fabric_reset_reset  (fabric_rst)
  );

  wire reset_n = ~fabric_rst;   // wrapper wants active-low, synchronous (from a PD reset_handler)
  wire init_done;

  // ---- DUT: real SDR PHY through the board-pads wrapper (real inout balls) ----
  axc3000_hyperram_pads #(
      .DATA_W(256), .ADDR_W(32), .WID_W(5), .RID_W(2), .LEN_W(8),
      .HB_ADDR_W(23), .HB_BURST_W(8),
      .PHY_VARIANT("SDR"), .DIFF_CK(1'b1), .LATENCY_CLOCKS(6),
      .POR_DELAY_CYCLES(0), .RD_PREAMBLE_SKIP(0), .MAX_BURST_WORDS(0)
  ) u_pads (
      .clk(clk), .clk2x(clk2x), .reset_n(reset_n),
      .s_axi_awid(awid), .s_axi_awaddr(awaddr), .s_axi_awlen(awlen), .s_axi_awsize(awsize),
      .s_axi_awburst(awburst), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
      .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wlast(wlast), .s_axi_wvalid(wvalid),
      .s_axi_wready(wready),
      .s_axi_bid(bid), .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
      .s_axi_arid(arid), .s_axi_araddr(araddr), .s_axi_arlen(arlen), .s_axi_arsize(arsize),
      .s_axi_arburst(arburst), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
      .s_axi_rid(rid), .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rlast(rlast),
      .s_axi_rvalid(rvalid), .s_axi_rready(rready),
      .hb_dq(hb_dq), .hb_rwds(hb_rwds), .hb_ck(hb_ck), .hb_ck_n(/* open: no board pin, single-ended */),
      .hb_cs_n(hb_cs_n), .hb_rst_n(hb_rst_n),
      .init_done(init_done),
      .wstrb_partial_seen(wstrb_partial_seen), .hi_addr_seen(hi_addr_seen));

  // ---- LED status snoop (all active-low: drive 0 to light) ----
  assign LED1 = ~init_done;
  assign RLED = ~(wstrb_partial_seen | hi_addr_seen);
  assign GLED = ~pll_locked;
endmodule
`default_nettype wire
