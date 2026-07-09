// tb_xor_reduce_tree — standalone check of the registered reduction tree used by soft_mac_array
// (issue #10). Drives a batch of random values into each DUT, holds them steady long enough for the
// pipeline to fully settle, then checks the registered output equals a plain XOR-reduce of the held
// inputs. Covers N=1 (degenerate leaf), a power-of-two N=8, and a non-power-of-two N=5 (exercises the
// ceil/floor recursive split).
`timescale 1ns/1ps
module tb_xor_reduce_tree;

  localparam int WIDTH  = 8;
  localparam int SETTLE = 8;   // >= max($clog2(N)+1) among the instances below, plus margin

  logic clk = 0;
  always #5 clk = ~clk;
  logic rst_n;
  int errors = 0;

  logic [0:0][WIDTH-1:0] in_n1;
  logic [4:0][WIDTH-1:0] in_n5;
  logic [7:0][WIDTH-1:0] in_n8;
  logic [WIDTH-1:0] out_n1, out_n5, out_n8;

  xor_reduce_tree #(.WIDTH(WIDTH), .N(1)) dut_n1 (.clk(clk), .rst_n(rst_n), .data_in(in_n1), .data_out(out_n1));
  xor_reduce_tree #(.WIDTH(WIDTH), .N(5)) dut_n5 (.clk(clk), .rst_n(rst_n), .data_in(in_n5), .data_out(out_n5));
  xor_reduce_tree #(.WIDTH(WIDTH), .N(8)) dut_n8 (.clk(clk), .rst_n(rst_n), .data_in(in_n8), .data_out(out_n8));

  task automatic check(input logic cond, input string msg);
    if (!cond) begin
      $display("FAIL: %s", msg);
      errors++;
    end
  endtask

  logic [WIDTH-1:0] exp_n1, exp_n5, exp_n8;

  initial begin
    rst_n = 0;
    in_n1[0] = '0;
    for (int i = 0; i < 5; i++) in_n5[i] = '0;
    for (int i = 0; i < 8; i++) in_n8[i] = '0;
    repeat (3) @(posedge clk);
    rst_n = 1;

    // Right after reset (before any real data has had time to flow through), outputs must be 0 —
    // proves the reset path actually reaches every leaf, not just the root register.
    check(out_n1 === '0, "N=1 not zero immediately after reset");
    check(out_n5 === '0, "N=5 not zero immediately after reset");
    check(out_n8 === '0, "N=8 not zero immediately after reset");

    for (int trial = 0; trial < 20; trial++) begin
      exp_n1 = '0;
      for (int i = 0; i < 1; i++) begin
        in_n1[i] = $urandom;
        exp_n1 ^= in_n1[i];
      end
      exp_n5 = '0;
      for (int i = 0; i < 5; i++) begin
        in_n5[i] = $urandom;
        exp_n5 ^= in_n5[i];
      end
      exp_n8 = '0;
      for (int i = 0; i < 8; i++) begin
        in_n8[i] = $urandom;
        exp_n8 ^= in_n8[i];
      end

      repeat (SETTLE) @(posedge clk);

      check(out_n1 === exp_n1, $sformatf("N=1 trial=%0d got=%0h exp=%0h", trial, out_n1, exp_n1));
      check(out_n5 === exp_n5, $sformatf("N=5 trial=%0d got=%0h exp=%0h", trial, out_n5, exp_n5));
      check(out_n8 === exp_n8, $sformatf("N=8 trial=%0d got=%0h exp=%0h", trial, out_n8, exp_n8));
    end

    if (errors == 0) $display("PASS");
    else             $display("FAIL: %0d error(s)", errors);
    $finish;
  end

  initial begin #2_000_000; $display("FAIL: timeout"); $finish; end

endmodule
