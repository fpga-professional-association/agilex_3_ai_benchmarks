// char_top — PH3 logic-characterization wrapper (NOT for board bring-up, NOT committed).
//
// Instantiates the novel PH3 datapath standalone: axi4_hbmc_bridge (AXI4 slave <-> Avalon master)
// wired to hbmc_core (Avalon slave <-> HyperBus PHY signals), exactly as sim/hyperbus/
// tb_axi4_hbmc_bridge.sv wires them. The bridge's Avalon master connects to hbmc's Avalon slave
// through internal nets; every other functional port (the AXI4 slave, the CSR slave, the hb_* PHY
// signals, and the sticky status outputs) is exposed at the top so it can be VIRTUAL_PIN'd and the
// combined bridge+controller LOGIC can be synthesized/fitted for a real fmax + resource number on
// A3CY100BM16AE7S.
//
// IMPORTANT: the hb_* signals are the controller's raw tri-state PHY interface, brought straight out
// as ordinary top-level ports. There is deliberately NO DDR-IO PHY here (it is not written yet) and
// no CoreDLA IP — so this characterizes the bridge+controller fabric logic ONLY, not a full system
// and not an on-hardware datapath.
`default_nettype none
module char_top (
    input  wire         clk,
    input  wire         rst,

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

    // ---- hbmc CSR slave ----
    input  wire [5:0]   csr_address,
    input  wire         csr_read,
    output wire [31:0]  csr_readdata,
    input  wire         csr_write,
    input  wire [31:0]  csr_writedata,

    // ---- HyperBus PHY-facing signals (NO real PHY: raw controller I/O brought out) ----
    output wire         hb_cs_n,
    output wire [7:0]   hb_dq_o,
    output wire         hb_dq_oe,
    input  wire [7:0]   hb_dq_i,
    output wire         hb_rwds_o,
    output wire         hb_rwds_oe,
    input  wire         hb_rwds_i,
    output wire [7:0]   hb_capture_delay,

    // ---- sticky status ----
    output wire         wstrb_partial_seen,
    output wire         hi_addr_seen
);
  // ---- internal Avalon-MM: bridge master <-> hbmc slave (16-bit word path) ----
  wire [22:0] av_address;
  wire [7:0]  av_burstcount;
  wire        av_read, av_write;
  wire [15:0] av_writedata, av_readdata;
  wire        av_readdatavalid, av_waitrequest;

  // ---- DUT: AXI4 -> Avalon bridge (wired identically to tb_axi4_hbmc_bridge.sv) ----
  axi4_hbmc_bridge u_bridge (
      .clk(clk), .rst(rst),
      .awid(awid), .awaddr(awaddr), .awlen(awlen), .awsize(awsize), .awburst(awburst),
      .awvalid(awvalid), .awready(awready),
      .wdata(wdata), .wstrb(wstrb), .wlast(wlast), .wvalid(wvalid), .wready(wready),
      .bid(bid), .bresp(bresp), .bvalid(bvalid), .bready(bready),
      .arid(arid), .araddr(araddr), .arlen(arlen), .arsize(arsize), .arburst(arburst),
      .arvalid(arvalid), .arready(arready),
      .rid(rid), .rdata(rdata), .rresp(rresp), .rlast(rlast), .rvalid(rvalid), .rready(rready),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read), .av_write(av_write),
      .av_writedata(av_writedata), .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid),
      .av_waitrequest(av_waitrequest),
      .wstrb_partial_seen(wstrb_partial_seen), .hi_addr_seen(hi_addr_seen));

  // ---- real HyperRAM controller (raw PHY signals brought straight out; no DDR-IO PHY) ----
  hbmc_core #(.LAT_BEATS_DEFAULT(6)) u_hbmc (
      .clk(clk), .rst(rst),
      .csr_address(csr_address), .csr_read(csr_read), .csr_readdata(csr_readdata),
      .csr_write(csr_write), .csr_writedata(csr_writedata),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read), .av_write(av_write),
      .av_writedata(av_writedata), .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid),
      .av_waitrequest(av_waitrequest),
      .hb_cs_n(hb_cs_n), .hb_dq_o(hb_dq_o), .hb_dq_oe(hb_dq_oe), .hb_dq_i(hb_dq_i),
      .hb_rwds_o(hb_rwds_o), .hb_rwds_oe(hb_rwds_oe), .hb_rwds_i(hb_rwds_i),
      .hb_capture_delay(hb_capture_delay));
endmodule
`default_nettype wire
