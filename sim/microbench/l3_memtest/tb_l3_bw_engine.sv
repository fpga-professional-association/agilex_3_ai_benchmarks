// tb_l3_bw_engine — self-checking Verilator testbench for l3_bw_engine (issue #14).
//
// Wires l3_bw_engine as hbmc_core's sole Avalon-MM master (same pattern as
// tb_l3_memtest_engine.sv); the engine never touches hbmc_core's CSR bus, so the TB drives it
// directly for the one-time LATENCY/CONFIG setup. MAX_SUBBURST is overridden small so a modest
// BURST_WORDS/BURST_COUNT already exercises: (a) a burst size that fits in one hbmc_core sub-burst,
// (b) one that needs several EVEN sub-bursts, (c) one that needs an UNEVEN split (last sub-burst
// shorter) -- covering the "1 KB/4 KB decomposition" path the module header describes. Counts actual
// hbmc_core commands issued (av_write/av_read rising edges) independently of the DUT's own
// BURSTS_DONE bookkeeping, so a bug in one can't hide behind the other.
`timescale 1ns/1ps
module tb_l3_bw_engine;
  import l3_memtest_pkg::*;

  localparam int LAT = 6;
  localparam int SMALL_SUBBURST = 4;

  logic clk = 0; always #5 clk = ~clk;
  logic rst;

  logic [5:0]  hbmc_csr_address; logic hbmc_csr_read, hbmc_csr_write;
  logic [31:0] hbmc_csr_readdata, hbmc_csr_writedata;

  logic [4:0]  csr_address; logic csr_read, csr_write;
  logic [31:0] csr_readdata, csr_writedata;

  logic [22:0] av_address; logic [7:0] av_burstcount;
  logic        av_read, av_write; logic [15:0] av_writedata, av_readdata;
  logic        av_readdatavalid, av_waitrequest;

  l3_bw_engine #(.MAX_SUBBURST(SMALL_SUBBURST)) dut (
      .clk(clk), .rst(rst),
      .csr_address(csr_address), .csr_read(csr_read), .csr_readdata(csr_readdata),
      .csr_write(csr_write), .csr_writedata(csr_writedata),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read), .av_write(av_write),
      .av_writedata(av_writedata), .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid),
      .av_waitrequest(av_waitrequest));

  logic       core_cs_n, core_dq_oe, core_rwds_o, core_rwds_oe;
  logic [7:0] core_dq_o;
  logic       bfm_dq_oe, bfm_rwds_o, bfm_rwds_oe;
  logic [7:0] bfm_dq_o;

  wire [7:0] dq_bus   = core_dq_oe ? core_dq_o : (bfm_dq_oe ? bfm_dq_o : 8'h00);
  wire       rwds_bus = core_rwds_oe ? core_rwds_o : (bfm_rwds_oe ? bfm_rwds_o : 1'b0);

  hbmc_core #(.LAT_BEATS_DEFAULT(LAT)) u_hbmc (
      .clk(clk), .rst(rst),
      .csr_address(hbmc_csr_address), .csr_read(hbmc_csr_read), .csr_readdata(hbmc_csr_readdata),
      .csr_write(hbmc_csr_write), .csr_writedata(hbmc_csr_writedata),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read), .av_write(av_write),
      .av_writedata(av_writedata), .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid),
      .av_waitrequest(av_waitrequest),
      .hb_cs_n(core_cs_n), .hb_dq_o(core_dq_o), .hb_dq_oe(core_dq_oe), .hb_dq_i(dq_bus),
      .hb_rwds_o(core_rwds_o), .hb_rwds_oe(core_rwds_oe), .hb_rwds_i(rwds_bus),
      .hb_capture_delay());

  w957d8nb_bfm #(.MEM_BYTES(65536), .LAT_BEATS(LAT), .ROW_BYTES(128), .ROW_PENALTY(4)) u_bfm (
      .clk(clk), .cs_n(core_cs_n), .dq_i(dq_bus), .rwds_i(rwds_bus),
      .dq_o(bfm_dq_o), .dq_oe(bfm_dq_oe), .rwds_o(bfm_rwds_o), .rwds_oe(bfm_rwds_oe),
      .collision(1'b0));

  // independent command counter: count rising edges of av_write / av_read
  int wr_cmds, rd_cmds;
  logic prev_av_write, prev_av_read;
  always_ff @(posedge clk) begin
    if (rst) begin wr_cmds <= 0; rd_cmds <= 0; prev_av_write <= 1'b0; prev_av_read <= 1'b0; end
    else begin
      if (av_write && !prev_av_write) wr_cmds <= wr_cmds + 1;
      if (av_read  && !prev_av_read)  rd_cmds <= rd_cmds + 1;
      prev_av_write <= av_write; prev_av_read <= av_read;
    end
  end

  int errors = 0;
  task automatic check(input logic cond, input string msg);
    if (!cond) begin $display("FAIL: %s", msg); errors++; end
  endtask

  task automatic hbmc_csr_wr(input logic [5:0] a, input logic [31:0] d);
    @(negedge clk); hbmc_csr_address = a; hbmc_csr_writedata = d; hbmc_csr_write = 1'b1;
    @(negedge clk); hbmc_csr_write = 1'b0;
  endtask

  task automatic eng_csr_wr(input logic [4:0] a, input logic [31:0] d);
    @(negedge clk); csr_address = a; csr_writedata = d; csr_write = 1'b1;
    @(negedge clk); csr_write = 1'b0;
  endtask
  task automatic eng_csr_rd(input logic [4:0] a, output logic [31:0] d);
    @(negedge clk); csr_address = a; csr_read = 1'b1;
    @(posedge clk); #1 d = csr_readdata;
    @(negedge clk); csr_read = 1'b0;
  endtask

  task automatic run_and_wait(input logic [31:0] base, input logic [31:0] words,
                              input logic [31:0] count, input logic dir_read,
                              output logic [31:0] bdone, output logic [63:0] cyc);
    logic [31:0] status, lo, hi;
    int guard;
    eng_csr_wr(BW_BASE_ADDR, base);
    eng_csr_wr(BW_BURST_WORDS, words);
    eng_csr_wr(BW_BURST_COUNT, count);
    eng_csr_wr(BW_CTRL, {30'd0, dir_read, 1'b1});
    guard = 0;
    do begin
      eng_csr_rd(BW_STATUS, status);
      guard++;
      if (guard > 2_000_000) begin $display("FAIL: bw run never finished"); $finish; end
    end while (!status[1]);
    eng_csr_rd(BW_BURSTS_DONE, bdone);
    eng_csr_rd(BW_CYCLES_LO, lo);
    eng_csr_rd(BW_CYCLES_HI, hi);
    cyc = {hi, lo};
  endtask

  logic [31:0] bdone; logic [63:0] cyc;
  initial begin
    rst = 1;
    hbmc_csr_address = 0; hbmc_csr_read = 0; hbmc_csr_write = 0; hbmc_csr_writedata = 0;
    csr_address = 0; csr_read = 0; csr_write = 0; csr_writedata = 0;
    repeat (4) @(negedge clk);
    rst = 0;

    hbmc_csr_wr(6'h04, 32'd6);  // CSR_LATENCY
    hbmc_csr_wr(6'h00, 32'd1);  // CSR_CONFIG (fixed)

    // ---- 1. WRITE direction: burst_words=16 (4 even 4-word sub-bursts), burst_count=3 ----
    wr_cmds = 0; rd_cmds = 0;
    run_and_wait(32'd0, 32'd16, 32'd3, 1'b0, bdone, cyc);
    check(bdone == 32'd3, $sformatf("write run bursts_done got %0d exp 3", bdone));
    check(wr_cmds == 12, $sformatf("write run sub-burst count got %0d exp 12 (16/4 x 3)", wr_cmds));
    check(cyc > 64'd0, "write run elapsed cycles should be > 0");

    // ---- 2. READ direction: same shape ----
    wr_cmds = 0; rd_cmds = 0;
    run_and_wait(32'd0, 32'd16, 32'd3, 1'b1, bdone, cyc);
    check(bdone == 32'd3, $sformatf("read run bursts_done got %0d exp 3", bdone));
    check(rd_cmds == 12, $sformatf("read run sub-burst count got %0d exp 12 (16/4 x 3)", rd_cmds));
    check(cyc > 64'd0, "read run elapsed cycles should be > 0");

    // ---- 3. UNEVEN split: burst_words=10 with a 4-word sub-burst cap -> 4,4,2 per logical burst;
    //         burst_count=2 -> 6 sub-bursts total. Models the 1 KB/4 KB > 255-word decomposition. ----
    wr_cmds = 0; rd_cmds = 0;
    run_and_wait(32'd2000, 32'd10, 32'd2, 1'b0, bdone, cyc);
    check(bdone == 32'd2, $sformatf("uneven run bursts_done got %0d exp 2", bdone));
    check(wr_cmds == 6, $sformatf("uneven run sub-burst count got %0d exp 6 (ceil(10/4)=3 x 2)", wr_cmds));

    // ---- 4. burst_words fits in a single sub-burst (BURST_WORDS <= MAX_SUBBURST): 1:1 mapping ----
    wr_cmds = 0; rd_cmds = 0;
    run_and_wait(32'd3000, 32'd4, 32'd5, 1'b0, bdone, cyc);
    check(bdone == 32'd5, $sformatf("1:1 run bursts_done got %0d exp 5", bdone));
    check(wr_cmds == 5, $sformatf("1:1 run sub-burst count got %0d exp 5 (1 hbmc cmd/logical burst)", wr_cmds));

    if (errors == 0) $display("PASS");
    else             $display("FAIL: %0d error(s)", errors);
    $finish;
  end

  initial begin #5000000; $display("FAIL: timeout"); $finish; end
endmodule
