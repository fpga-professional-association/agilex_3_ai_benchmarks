// hyperbus_top — smoke-compile wrapper for the HyperBus controller on the AXC3000 (issue #13).
//
// Exposes the CSR + Avalon-MM data slave as top-level ports (a real system connects these to the
// JTAG-Avalon master / mSGDMA) and maps the controller's PHY-facing tri-state signals to the
// bidirectional HyperRAM pins. The tri-states here are BEHAVIORAL placeholders that synthesize to IO
// buffers; the real design substitutes an Agilex DDR-IO PHY with delay taps driven by hb_capture_delay
// (docs/hyperbus.md). Not compiled in CI — this is the Quartus/timing handoff.
`default_nettype none
module hyperbus_top (
    input  wire        clk,           // ~100 MHz for the first pass
    input  wire        rst,

    // CSR slave
    input  wire [5:0]  csr_address,
    input  wire        csr_read,
    output wire [31:0] csr_readdata,
    input  wire        csr_write,
    input  wire [31:0] csr_writedata,

    // Avalon-MM data slave
    input  wire [22:0] av_address,
    input  wire [7:0]  av_burstcount,
    input  wire        av_read,
    input  wire        av_write,
    input  wire [15:0] av_writedata,
    output wire [15:0] av_readdata,
    output wire        av_readdatavalid,
    output wire        av_waitrequest,

    // HyperRAM pins
    output wire        hb_cs_n,
    output wire        hb_ck,
    output wire        hb_rst_n,
    inout  wire [7:0]  hb_dq,
    inout  wire        hb_rwds
);
  wire [7:0] dq_o, dq_i;
  wire       dq_oe, rwds_o, rwds_oe, rwds_i;
  wire [7:0] capture_delay;

  // behavioral tri-states -> replaced by an Agilex DDR-IO PHY during bring-up
  assign hb_dq   = dq_oe   ? dq_o   : 8'hzz;
  assign dq_i    = hb_dq;
  assign hb_rwds = rwds_oe ? rwds_o : 1'bz;
  assign rwds_i  = hb_rwds;
  assign hb_ck   = clk;                 // placeholder single-ended clock; PHY generates the real HB clock
  assign hb_rst_n = ~rst;

  hbmc_core #(.LAT_BEATS_DEFAULT(6)) u_hbmc (
      .clk(clk), .rst(rst),
      .csr_address(csr_address), .csr_read(csr_read), .csr_readdata(csr_readdata),
      .csr_write(csr_write), .csr_writedata(csr_writedata),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read), .av_write(av_write),
      .av_writedata(av_writedata), .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid),
      .av_waitrequest(av_waitrequest),
      .hb_cs_n(hb_cs_n), .hb_dq_o(dq_o), .hb_dq_oe(dq_oe), .hb_dq_i(dq_i),
      .hb_rwds_o(rwds_o), .hb_rwds_oe(rwds_oe), .hb_rwds_i(rwds_i),
      .hb_capture_delay(capture_delay));
endmodule
`default_nettype wire
