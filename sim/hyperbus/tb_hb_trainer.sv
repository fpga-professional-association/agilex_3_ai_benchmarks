// tb_hb_trainer — self-checking Verilator testbench for hb_trainer (issue #14).
//
// Wires hb_trainer as hbmc_core's CSR+Avalon master (its real integration point, see hb_trainer.sv
// header) with the W957D8NB BFM as the device model, exactly like sim/hyperbus/tb_hyperbus.sv wires
// hbmc_core itself. A small TB-side mux lets this testbench ALSO drive hbmc_core's CSR bus directly
// for the one-time LATENCY/CONFIG setup a real host would do before ever starting a training sweep
// (hb_trainer's own CSR master only ever touches CSR_CAPDELAY).
//
// TESTBENCH-ONLY synthetic capture-delay error model: hbmc_core's BFM has no AC-timing behavior at
// all (it's a byte-per-beat protocol model, docs/hyperbus.md), so nothing about hb_capture_delay
// actually changes what a read returns in simulation. To exercise hb_trainer's window-search
// algorithm under Verilator, this TB taps the controller's view of read data (BFM -> controller
// direction only; writes/CA are untouched, and the BFM's own memory always sees the true bus) and
// flips one bit whenever hb_capture_delay is OUTSIDE a fixed "good" tap range [GOOD_LO, GOOD_HI].
// This is a deliberate test fixture, not a timing model, and must not be read as one.
`timescale 1ns/1ps
module tb_hb_trainer;
  localparam int LAT = 6;
  localparam int DELAY_TAPS = 32;
  localparam int MIN_WINDOW = 2;
  localparam int TEST_WORDS = 8;
  localparam logic [22:0] TEST_ADDR = 23'd40;

  // "good" capture-delay window this fixture models -- width 6, comfortably >= MIN_WINDOW
  localparam int GOOD_LO = 10;
  localparam int GOOD_HI = 15;

  // mirror hb_trainer's own CSR map (docs/hyperbus.md #14 addendum) for readability
  localparam logic [4:0] T_CTRL       = 5'h00;
  localparam logic [4:0] T_STATUS     = 5'h04;
  localparam logic [4:0] T_WIN_LO     = 5'h08;
  localparam logic [4:0] T_WIN_HI     = 5'h0C;
  localparam logic [4:0] T_WIN_WIDTH  = 5'h10;
  localparam logic [4:0] T_WIN_CENTER = 5'h14;
  localparam logic [4:0] T_LAST_TAP   = 5'h1C;

  logic clk = 0; always #5 clk = ~clk;
  logic rst;

  // ---- hb_trainer <-> TB (own CSR block) ----
  logic [4:0]  t_csr_address; logic t_csr_read, t_csr_write;
  logic [31:0] t_csr_readdata, t_csr_writedata;

  // ---- hb_trainer -> hbmc_core CSR master signals ----
  logic [5:0]  tr_csr_address; logic tr_csr_write; logic [31:0] tr_csr_writedata;

  // ---- TB's own direct CSR access to hbmc_core (one-time LATENCY/CONFIG setup) ----
  logic [5:0]  tb_csr_address; logic tb_csr_write; logic [31:0] tb_csr_writedata;

  // ---- muxed CSR bus actually driving hbmc_core ----
  logic [5:0]  hbmc_csr_address; logic hbmc_csr_write; logic [31:0] hbmc_csr_writedata;
  assign hbmc_csr_write     = tb_csr_write || tr_csr_write;
  assign hbmc_csr_address   = tb_csr_write ? tb_csr_address   : tr_csr_address;
  assign hbmc_csr_writedata = tb_csr_write ? tb_csr_writedata : tr_csr_writedata;

  // ---- hb_trainer <-> hbmc_core Avalon data path (direct, 1:1) ----
  logic [22:0] av_address; logic [7:0] av_burstcount;
  logic        av_read, av_write; logic [15:0] av_writedata, av_readdata;
  logic        av_readdatavalid, av_waitrequest;

  logic [31:0] hbmc_csr_readdata_unused;

  hb_trainer #(
      .DELAY_TAPS(DELAY_TAPS), .MIN_WINDOW(MIN_WINDOW), .TEST_WORDS(TEST_WORDS), .TEST_ADDR(TEST_ADDR)
  ) dut (
      .clk(clk), .rst(rst),
      .t_csr_address(t_csr_address), .t_csr_read(t_csr_read), .t_csr_readdata(t_csr_readdata),
      .t_csr_write(t_csr_write), .t_csr_writedata(t_csr_writedata),
      .csr_address(tr_csr_address), .csr_write(tr_csr_write), .csr_writedata(tr_csr_writedata),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read), .av_write(av_write),
      .av_writedata(av_writedata), .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid),
      .av_waitrequest(av_waitrequest));

  // ---- HyperBus nets ----
  logic       core_cs_n, core_dq_oe, core_rwds_o, core_rwds_oe;
  logic [7:0] core_dq_o;
  logic       bfm_dq_oe, bfm_rwds_o, bfm_rwds_oe;
  logic [7:0] bfm_dq_o;
  logic [7:0] cur_capdelay;

  // BFM's true view of the bus (writes/CA unaffected by the synthetic error model)
  wire [7:0] dq_bus_true = core_dq_oe ? core_dq_o : (bfm_dq_oe ? bfm_dq_o : 8'h00);
  wire       rwds_bus    = core_rwds_oe ? core_rwds_o : (bfm_rwds_oe ? bfm_rwds_o : 1'b0);
  wire       good_window = (cur_capdelay >= 8'(GOOD_LO)) && (cur_capdelay <= 8'(GOOD_HI));
  // controller's view: flip a bit on BFM->controller (read) data outside the good window
  wire [7:0] dq_bus_to_core = (bfm_dq_oe && !good_window) ? (bfm_dq_o ^ 8'h01) : dq_bus_true;

  hbmc_core #(.LAT_BEATS_DEFAULT(LAT)) u_hbmc (
      .clk(clk), .rst(rst),
      .csr_address(hbmc_csr_address), .csr_read(1'b0), .csr_readdata(hbmc_csr_readdata_unused),
      .csr_write(hbmc_csr_write), .csr_writedata(hbmc_csr_writedata),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read), .av_write(av_write),
      .av_writedata(av_writedata), .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid),
      .av_waitrequest(av_waitrequest),
      .hb_cs_n(core_cs_n), .hb_dq_o(core_dq_o), .hb_dq_oe(core_dq_oe), .hb_dq_i(dq_bus_to_core),
      .hb_rwds_o(core_rwds_o), .hb_rwds_oe(core_rwds_oe), .hb_rwds_i(rwds_bus),
      .hb_capture_delay(cur_capdelay));

  w957d8nb_bfm #(.MEM_BYTES(65536), .LAT_BEATS(LAT), .ROW_BYTES(128), .ROW_PENALTY(4)) u_bfm (
      .clk(clk), .cs_n(core_cs_n), .dq_i(dq_bus_true), .rwds_i(rwds_bus),
      .dq_o(bfm_dq_o), .dq_oe(bfm_dq_oe), .rwds_o(bfm_rwds_o), .rwds_oe(bfm_rwds_oe),
      .collision(1'b0));

  int errors = 0;
  task automatic check(input logic cond, input string msg);
    if (!cond) begin $display("FAIL: %s", msg); errors++; end
  endtask

  task automatic tb_csr_wr(input logic [5:0] a, input logic [31:0] d);
    @(negedge clk); tb_csr_address = a; tb_csr_writedata = d; tb_csr_write = 1'b1;
    @(negedge clk); tb_csr_write = 1'b0;
  endtask

  task automatic t_csr_wr(input logic [4:0] a, input logic [31:0] d);
    @(negedge clk); t_csr_address = a; t_csr_writedata = d; t_csr_write = 1'b1;
    @(negedge clk); t_csr_write = 1'b0;
  endtask
  task automatic t_csr_rd(input logic [4:0] a, output logic [31:0] d);
    @(negedge clk); t_csr_address = a; t_csr_read = 1'b1;
    @(posedge clk); #1 d = t_csr_readdata;
    @(negedge clk); t_csr_read = 1'b0;
  endtask

  task automatic run_one_sweep(output logic [31:0] lo, output logic [31:0] hi,
                               output logic [31:0] width, output logic [31:0] center,
                               output logic [31:0] status);
    int guard;
    t_csr_wr(T_CTRL, 32'd1);
    guard = 0;
    do begin
      t_csr_rd(T_STATUS, status);
      guard++;
      if (guard > 200000) begin $display("FAIL: training sweep never finished"); $finish; end
    end while (!status[1]);  // wait DONE
    t_csr_rd(T_WIN_LO, lo);
    t_csr_rd(T_WIN_HI, hi);
    t_csr_rd(T_WIN_WIDTH, width);
    t_csr_rd(T_WIN_CENTER, center);
  endtask

  logic [31:0] lo, hi, width, center, status;
  initial begin
    rst = 1;
    t_csr_address = 0; t_csr_read = 0; t_csr_write = 0; t_csr_writedata = 0;
    tb_csr_address = 0; tb_csr_write = 0; tb_csr_writedata = 0;
    repeat (4) @(negedge clk);
    rst = 0;

    // one-time host setup: match the BFM's default fixed-latency mode
    tb_csr_wr(6'h04, 32'd6);   // hbmc_core CSR_LATENCY
    tb_csr_wr(6'h00, 32'd1);   // hbmc_core CSR_CONFIG (fixed)

    // ---- 1. first sweep: find the window ----
    run_one_sweep(lo, hi, width, center, status);
    check(status[2], "WINDOW_VALID not set after sweep 1");
    check(lo == GOOD_LO, $sformatf("sweep1 WIN_LO got %0d exp %0d", lo, GOOD_LO));
    check(hi == GOOD_HI, $sformatf("sweep1 WIN_HI got %0d exp %0d", hi, GOOD_HI));
    check(width == (GOOD_HI - GOOD_LO + 1),
          $sformatf("sweep1 WIN_WIDTH got %0d exp %0d", width, GOOD_HI - GOOD_LO + 1));
    check(center == GOOD_LO + (GOOD_HI - GOOD_LO + 1) / 2,
          $sformatf("sweep1 WIN_CENTER got %0d exp %0d", center, GOOD_LO + (GOOD_HI - GOOD_LO + 1) / 2));
    check(cur_capdelay == center[7:0],
          $sformatf("hbmc_core CAPDELAY not parked at center: got %0d exp %0d", cur_capdelay, center));

    // ---- 2. re-run the sweep twice more (no reset) -- window must be stable/repeatable ----
    for (int rep = 0; rep < 2; rep++) begin
      logic [31:0] lo2, hi2, width2, center2, status2;
      run_one_sweep(lo2, hi2, width2, center2, status2);
      check(lo2 == lo && hi2 == hi && width2 == width && center2 == center,
            $sformatf("repeat %0d: window not stable (got lo=%0d hi=%0d w=%0d c=%0d)",
                       rep, lo2, hi2, width2, center2));
    end

    if (errors == 0) $display("PASS");
    else             $display("FAIL: %0d error(s)", errors);
    $finish;
  end

  initial begin #2000000; $display("FAIL: timeout"); $finish; end
endmodule
