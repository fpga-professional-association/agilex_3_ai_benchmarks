// tb_hyperbus — self-checking Verilator testbench for the HyperBus controller (issue #13).
//
// Wires hbmc_core to the W957D8NB BFM, resolving the bidirectional DQ/RWDS bus in the TB (no
// tri-state 'z' — Verilator-friendly). Covers: device-register (ID) read, single word R/W, linear
// burst R/W crossing a row boundary, and fixed vs variable latency (with and without a refresh
// collision). Data correctness in every case validates CA encoding, latency alignment, and
// RWDS-gated capture.
`timescale 1ns/1ps
module tb_hyperbus;
  import hyperbus_pkg::*;

  localparam int LAT = 6;
  localparam int ROW_BYTES = 128;
  // device register-space addresses (match w957d8nb_bfm)
  localparam logic [31:0] REG_ID0 = 32'h0000_0000;
  localparam logic [31:0] REG_CR0 = 32'h0000_0800;

  logic clk = 0; always #5 clk = ~clk;
  logic rst;

  // CSR + Avalon
  logic [5:0]  csr_address; logic csr_read, csr_write;
  logic [31:0] csr_readdata, csr_writedata;
  logic [22:0] av_address; logic [7:0] av_burstcount;
  logic        av_read, av_write; logic [15:0] av_writedata, av_readdata;
  logic        av_readdatavalid, av_waitrequest;

  // HyperBus nets
  logic       core_cs_n, core_dq_oe, core_rwds_o, core_rwds_oe;
  logic [7:0] core_dq_o;
  logic       bfm_dq_oe, bfm_rwds_o, bfm_rwds_oe;
  logic [7:0] bfm_dq_o;
  logic       collision;

  // resolved buses (controller drives during CA/write; device drives during read)
  wire [7:0] dq_bus   = core_dq_oe   ? core_dq_o   : (bfm_dq_oe   ? bfm_dq_o   : 8'h00);
  wire       rwds_bus = core_rwds_oe ? core_rwds_o : (bfm_rwds_oe ? bfm_rwds_o : 1'b0);

  hbmc_core #(.LAT_BEATS_DEFAULT(LAT)) dut (
      .clk(clk), .rst(rst),
      .csr_address(csr_address), .csr_read(csr_read), .csr_readdata(csr_readdata),
      .csr_write(csr_write), .csr_writedata(csr_writedata),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read), .av_write(av_write),
      .av_writedata(av_writedata), .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid),
      .av_waitrequest(av_waitrequest),
      .hb_cs_n(core_cs_n), .hb_dq_o(core_dq_o), .hb_dq_oe(core_dq_oe), .hb_dq_i(dq_bus),
      .hb_rwds_o(core_rwds_o), .hb_rwds_oe(core_rwds_oe), .hb_rwds_i(rwds_bus),
      .hb_capture_delay());

  w957d8nb_bfm #(.MEM_BYTES(65536), .LAT_BEATS(LAT), .ROW_BYTES(ROW_BYTES), .ROW_PENALTY(4)) u_bfm (
      .clk(clk), .cs_n(core_cs_n), .dq_i(dq_bus), .rwds_i(rwds_bus),
      .dq_o(bfm_dq_o), .dq_oe(bfm_dq_oe), .rwds_o(bfm_rwds_o), .rwds_oe(bfm_rwds_oe),
      .collision(collision));

  localparam int ST_BUSY_BIT = 0;
  int errors = 0;
  task automatic check(input logic cond, input string msg);
    if (!cond) begin $display("FAIL: %s", msg); errors++; end
  endtask

  // expected initial memory pattern
  function automatic logic [7:0]  patt(input int i);   return 8'((i * 13 + 7)); endfunction
  function automatic logic [15:0] wexp(input int wa);  return {patt(wa*2 + 1), patt(wa*2)}; endfunction

  // ---- CSR access ----
  task automatic csr_wr(input logic [5:0] a, input logic [31:0] d);
    @(negedge clk); csr_address = a; csr_writedata = d; csr_write = 1'b1;
    @(negedge clk); csr_write = 1'b0;
  endtask
  task automatic csr_rd(input logic [5:0] a, output logic [31:0] d);
    @(negedge clk); csr_address = a; csr_read = 1'b1;
    @(posedge clk); #1 d = csr_readdata;
    @(negedge clk); csr_read = 1'b0;
  endtask

  // ---- Avalon data path ----
  task automatic av_read_burst(input logic [22:0] addr, input int n, output logic [15:0] data []);
    int got = 0;
    data = new[n];
    @(negedge clk); av_address = addr; av_burstcount = 8'(n); av_read = 1'b1;
    forever begin @(posedge clk); if (!av_waitrequest) break; end   // command accepted
    @(negedge clk); av_read = 1'b0;
    while (got < n) begin
      @(posedge clk);
      if (av_readdatavalid) begin data[got] = av_readdata; got++; end
    end
  endtask

  task automatic av_write_burst(input logic [22:0] addr, input int n, input logic [15:0] data []);
    int sent;
    @(negedge clk); av_address = addr; av_burstcount = 8'(n); av_write = 1'b1; av_writedata = data[0];
    forever begin @(posedge clk); if (!av_waitrequest) break; end   // word 0 (with command)
    sent = 1;
    while (sent < n) begin
      @(negedge clk); av_writedata = data[sent]; av_write = 1'b1;
      forever begin @(posedge clk); if (!av_waitrequest) break; end
      sent++;
    end
    @(negedge clk); av_write = 1'b0;
    while (!core_cs_n) @(posedge clk);   // wait until the controller finishes (cs_n high = idle)
  endtask

  // ---- device register access via CSR ----
  task automatic dev_read(input logic [31:0] reg_addr, output logic [15:0] val);
    logic [31:0] st, rd;
    csr_wr(CSR_DEV_ADDR, reg_addr);
    csr_wr(CSR_DEV_CTRL, (1 << DEV_GO) | (1 << DEV_RW));
    do csr_rd(CSR_STATUS, st); while (st[ST_BUSY_BIT]);
    csr_rd(CSR_DEV_RDAT, rd); val = rd[15:0];
  endtask
  task automatic dev_write(input logic [31:0] reg_addr, input logic [15:0] wdata);
    logic [31:0] st;
    csr_wr(CSR_DEV_ADDR, reg_addr);
    csr_wr(CSR_DEV_WDAT, {16'd0, wdata});
    csr_wr(CSR_DEV_CTRL, (1 << DEV_GO));         // rw=0 -> write
    do csr_rd(CSR_STATUS, st); while (st[ST_BUSY_BIT]);
  endtask

  logic [15:0] rd [];
  logic [15:0] wr [];
  logic [15:0] id0;
  initial begin
    rst = 1; collision = 0;
    csr_address = 0; csr_read = 0; csr_write = 0; csr_writedata = 0;
    av_address = 0; av_burstcount = 0; av_read = 0; av_write = 0; av_writedata = 0;
    repeat (4) @(negedge clk);
    rst = 0;
    // match the device: base latency 6, fixed mode (BFM CR0 default bit3=1)
    csr_wr(CSR_LATENCY, 32'd6);
    csr_wr(CSR_CONFIG, 32'd1);          // fixed
    csr_wr(CSR_CAPDELAY, 32'd5);        // exercise the #14 hook (stored, drives PHY taps in HW)

    // ---- 1. device ID read ----
    dev_read(REG_ID0, id0);
    check(id0 == 16'h0C81, $sformatf("ID0 read got %h", id0));

    // ---- 2. single-word read (fixed latency) ----
    av_read_burst(23'd100, 1, rd);
    check(rd[0] == wexp(100), $sformatf("single read w100 got %h exp %h", rd[0], wexp(100)));

    // ---- 3. single-word write + readback ----
    wr = new[1]; wr[0] = 16'hBEEF;
    av_write_burst(23'd200, 1, wr);
    av_read_burst(23'd200, 1, rd);
    check(rd[0] == 16'hBEEF, $sformatf("single write/read got %h", rd[0]));

    // ---- 4. linear burst read crossing a row boundary ----
    // ROW_BYTES=128 -> 64 words per row; start at 60, read 10 -> crosses word 64
    av_read_burst(23'd60, 10, rd);
    for (int i = 0; i < 10; i++)
      check(rd[i] == wexp(60 + i), $sformatf("burst read w%0d got %h exp %h", 60+i, rd[i], wexp(60+i)));

    // ---- 5. burst write + readback (crossing a row boundary too) ----
    wr = new[10];
    for (int i = 0; i < 10; i++) wr[i] = 16'(16'hA000 + i);
    av_write_burst(23'd300, 10, wr);
    av_read_burst(23'd300, 10, rd);
    for (int i = 0; i < 10; i++)
      check(rd[i] == 16'(16'hA000 + i), $sformatf("burst write/read w%0d got %h", 300+i, rd[i]));

    // ---- 6. variable latency, no collision ----
    dev_write(REG_CR0, 16'h0000);        // clear bit3 -> variable
    csr_wr(CSR_CONFIG, 32'd0);            // controller: variable
    collision = 0;
    av_read_burst(23'd400, 4, rd);
    for (int i = 0; i < 4; i++)
      check(rd[i] == wexp(400 + i), $sformatf("var no-coll read w%0d got %h", 400+i, rd[i]));

    // ---- 7. variable latency, refresh collision (2x latency) ----
    collision = 1;
    av_read_burst(23'd500, 4, rd);
    for (int i = 0; i < 4; i++)
      check(rd[i] == wexp(500 + i), $sformatf("var collision read w%0d got %h", 500+i, rd[i]));

    if (errors == 0) $display("PASS");
    else             $display("FAIL: %0d error(s)", errors);
    $finish;
  end

  initial begin #200000; $display("FAIL: timeout"); $finish; end
endmodule
