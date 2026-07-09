// tb_l1_pe_array — self-checking Verilator testbench for the L1 PE-array microbench (issue #11).
//
// Single parameterised DUT that runs one benchmark pass and prints its result checksum. sim/run.sh
// builds it four times — {RETIME_CLEAN, RESET_HEAVY} x {MERGED, ISOLATED} — and asserts that all
// four print the SAME checksum. That equivalence is the scientific crux of L1: the reset-style and
// clock-domain variants must be functionally identical (only their timing discipline differs), so
// any fmax delta the Quartus sweep finds is attributable to the discipline alone, not to a logic
// change (issue "do not": only the RTL/domain variable moves). This TB also self-checks that the
// run retires exactly N_VECTORS and that the checksum is non-trivial (real work, not a const-folded
// zero).
`timescale 1ns/1ps
module tb_l1_pe_array
  import bench_pkg::*;
#(
    parameter int NUM_ROWS    = 2,
    parameter int NUM_COLS    = 2,
    parameter bit RESET_HEAVY = 1'b0,
    parameter bit ISOLATE     = 1'b0,
    parameter int N_VECTORS   = 40
);
  logic clk = 1'b0, clk_hot = 1'b0, rst_n = 1'b0;
  always #5 clk = ~clk;          // 100 MHz CSR clock
  always #3 clk_hot = ~clk_hot;  // ~167 MHz — genuinely async to clk, exercises the ISOLATED CDC
  wire dut_clk_hot = ISOLATE ? clk_hot : clk;

  logic [7:0]  addr;
  logic        rd, wr;
  logic [31:0] wdata, rdata;
  logic        waitreq;

  l1_pe_top #(
      .NUM_ROWS(NUM_ROWS), .NUM_COLS(NUM_COLS), .RESET_HEAVY(RESET_HEAVY), .ISOLATE(ISOLATE)
  ) dut (
      .clk(clk), .clk_hot(dut_clk_hot), .rst_n(rst_n),
      .csr_address(addr), .csr_read(rd), .csr_readdata(rdata),
      .csr_write(wr), .csr_writedata(wdata), .csr_waitrequest(waitreq)
  );

  task automatic csr_wr(input logic [7:0] a, input logic [31:0] d);
    @(posedge clk); addr = a; wdata = d; wr = 1'b1; rd = 1'b0;
    @(posedge clk); wr = 1'b0;
  endtask

  task automatic csr_rd(input logic [7:0] a, output logic [31:0] d);
    @(posedge clk); addr = a; rd = 1'b1; wr = 1'b0;
    #1 d = rdata;                 // 0-wait slave: readdata is combinational on address
    @(posedge clk); rd = 1'b0;
  endtask

  logic [31:0] st, chk, dn, cyc_lo, cyc_hi, dims;
  int          guard;

  initial begin
    addr = '0; rd = 1'b0; wr = 1'b0; wdata = '0;
    repeat (6) @(posedge clk); rst_n = 1'b1; repeat (3) @(posedge clk);

    csr_wr(L1_ADDR_N_VECTORS, N_VECTORS);
    csr_wr(L1_ADDR_CTRL, 32'h1);            // START

    guard = 0;
    do begin
      csr_rd(L1_ADDR_STATUS, st);
      guard++;
    end while (!st[ST_DONE] && guard < 200000);

    repeat (12) @(posedge clk);             // let the ISOLATED result FIFO drain into the CSR domain

    csr_rd(L1_ADDR_CHECKSUM, chk);
    csr_rd(L1_ADDR_DONE, dn);
    csr_rd(L1_ADDR_CYCLES_LO, cyc_lo);
    csr_rd(L1_ADDR_CYCLES_HI, cyc_hi);
    csr_rd(L1_ADDR_DIMS, dims);

    if (!st[ST_DONE]) begin
      $display("FAIL: timed out waiting for DONE (guard=%0d)", guard); $finish;
    end
    if (dn !== N_VECTORS) begin
      $display("FAIL: retired %0d vectors, expected %0d", dn, N_VECTORS); $finish;
    end
    if (chk === 32'h0) begin
      $display("FAIL: checksum is zero — datapath produced no work (const-folded?)"); $finish;
    end
    if (dims !== {16'(NUM_COLS), 16'(NUM_ROWS)}) begin
      $display("FAIL: DIMS=0x%08x, expected cols=%0d rows=%0d", dims, NUM_COLS, NUM_ROWS); $finish;
    end

    $display("L1TB rows=%0d cols=%0d rst_heavy=%0d isolate=%0d checksum=0x%08x cycles=%0d done=%0d",
             NUM_ROWS, NUM_COLS, RESET_HEAVY, ISOLATE, chk, {cyc_hi, cyc_lo}, dn);
    $display("PASS");
    $finish;
  end

  initial begin
    #500000;
    $display("FAIL: global timeout"); $finish;
  end
endmodule
