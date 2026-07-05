// tb_l0_tensor_chain — self-checking testbench for rtl/microbench/l0_tensor_chain/ (issue #9).
//
// Drives the Avalon-MM CSR slave exactly as sw/host/run_l0.py would (configure N_VECTORS, pulse
// CTRL.START, poll STATUS.DONE, read the CYCLES_LO/DONE_COUNT/CHECKSUM snapshot) and checks the
// result against sw/host/l0_golden.py's cycle-accurate model for the SAME (N_BLOCKS, N_VECTORS).
// The expected constants below were generated with:
//   python3 sw/host/l0_golden.py --n-blocks 3 --n-taps 10 --n-vectors 5
//   -> cycles=12 done=5 checksum=0x00001909
// This module contains no device primitives (classic-mode inferred `+`/`*` only — see the module
// README for why), so it is fully Verilator-simulatable per AGENTS.md/sim/README.md.
`timescale 1ns/1ps
module tb_l0_tensor_chain;
  import bench_pkg::*;

  localparam int N_BLOCKS  = 3;
  localparam int N_VECTORS = 5;
  localparam logic [63:0] EXP_CYCLES   = 64'd12;
  localparam logic [31:0] EXP_DONE     = 32'd5;
  localparam logic [31:0] EXP_CHECKSUM = 32'h0000_1909;

  logic clk = 0; always #5 clk = ~clk;
  logic rst_n;

  logic [7:0]  csr_address;
  logic        csr_read;
  logic [31:0] csr_readdata;
  logic        csr_write;
  logic [31:0] csr_writedata;
  logic        csr_waitrequest;

  int errors = 0;

  l0_tensor_chain #(.N_BLOCKS(N_BLOCKS)) dut (
      .clk(clk), .rst_n(rst_n),
      .csr_address(csr_address), .csr_read(csr_read), .csr_readdata(csr_readdata),
      .csr_write(csr_write), .csr_writedata(csr_writedata), .csr_waitrequest(csr_waitrequest)
  );

  task automatic csr_wr(input logic [7:0] addr, input logic [31:0] data);
    @(posedge clk);
    csr_address <= addr; csr_writedata <= data; csr_write <= 1'b1; csr_read <= 1'b0;
    @(posedge clk);
    csr_write <= 1'b0;
  endtask

  task automatic csr_rd(input logic [7:0] addr, output logic [31:0] data);
    @(posedge clk);
    csr_address <= addr; csr_read <= 1'b1; csr_write <= 1'b0;
    @(posedge clk);
    data = csr_readdata;
    csr_read <= 1'b0;
  endtask

  task automatic check(input logic cond, input string msg);
    if (!cond) begin $display("FAIL: %s", msg); errors = errors + 1; end
  endtask

  logic [31:0] rd_lo, rd_hi, rd_done, rd_checksum, rd_status, rd_nblocks;

  initial begin
    rst_n = 0; csr_address = '0; csr_read = 0; csr_write = 0; csr_writedata = '0;
    repeat (3) @(posedge clk);
    rst_n = 1;
    @(posedge clk);

    // N_BLOCKS CSR cross-check (host sanity check against the loaded .sof)
    csr_rd(L0_ADDR_N_BLOCKS, rd_nblocks);
    check(rd_nblocks == N_BLOCKS, $sformatf("N_BLOCKS readback %0d != %0d", rd_nblocks, N_BLOCKS));

    csr_wr(L0_ADDR_N_VECTORS, N_VECTORS);
    csr_wr(L0_ADDR_CTRL, 32'(1 << CTRL_START));

    // poll STATUS.DONE
    rd_status = 0;
    for (int i = 0; i < 1000 && !rd_status[ST_DONE]; i++) begin
      csr_rd(L0_ADDR_STATUS, rd_status);
    end
    check(rd_status[ST_DONE], "run never completed (STATUS.DONE timed out)");

    // snapshot: CYCLES_LO latches CYCLES_HI/DONE_COUNT/CHECKSUM together
    csr_rd(L0_ADDR_CYCLES_LO, rd_lo);
    csr_rd(L0_ADDR_CYCLES_HI, rd_hi);
    csr_rd(L0_ADDR_DONE, rd_done);
    csr_rd(L0_ADDR_CHECKSUM, rd_checksum);

    check({rd_hi, rd_lo} == EXP_CYCLES, $sformatf("cycles %0d != expected %0d", {rd_hi, rd_lo}, EXP_CYCLES));
    check(rd_done == EXP_DONE, $sformatf("done %0d != expected %0d", rd_done, EXP_DONE));
    check(rd_checksum == EXP_CHECKSUM,
          $sformatf("checksum 0x%08x != expected 0x%08x", rd_checksum, EXP_CHECKSUM));

    if (errors == 0) $display("PASS");
    else             $display("FAIL: %0d error(s)", errors);
    $finish;
  end

  initial begin #2_000_000; $display("FAIL: timeout"); $finish; end
endmodule
