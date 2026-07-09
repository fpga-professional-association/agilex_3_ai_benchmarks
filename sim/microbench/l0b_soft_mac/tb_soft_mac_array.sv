// tb_soft_mac_array — self-checking testbench for soft_mac_array (issue #10, PLAN Sec7 L0b).
//
// Two checks:
//   1. Bit-exact: an M=1 instance (per W in {4,2,1}) is checked every cycle against a behavioral
//      reference model in this testbench that mirrors the DUT's exact per-lane equations (same LFSR
//      taps/seed formula, same registered multiply, same registered accumulate, same 1-cycle leaf
//      pass-through from xor_reduce_tree at N=1). This is the real correctness check on the LFSR
//      stimulus generator and the MAC datapath.
//   2. Liveness: a wider M=16 instance must produce a checksum that keeps changing over a long
//      window and is never stuck at a constant — a cheap simulation-time sanity check that every
//      lane's result is actually reaching the output (the same property scripts/sweep_l0b.py checks
//      at synthesis time via ALM-count-vs-M linearity).
`timescale 1ns/1ps
module tb_soft_mac_array;

  logic clk = 0;
  always #5 clk = ~clk;
  logic rst_n;
  int errors = 0;

  task automatic check(input logic cond, input string msg);
    if (!cond) begin
      $display("FAIL: %s", msg);
      errors++;
    end
  endtask

  // ---------------------------------------------------------------------------------------------
  // Part 1: bit-exact reference model, M=1, one instance per W.
  // ---------------------------------------------------------------------------------------------
  localparam int ACC_W = 24;

  logic [ACC_W-1:0] chk_w4, chk_w2, chk_w1;
  soft_mac_array #(.W(4), .M(1), .ACC_W(ACC_W)) dut_w4 (.clk(clk), .rst_n(rst_n), .checksum_q(chk_w4));
  soft_mac_array #(.W(2), .M(1), .ACC_W(ACC_W)) dut_w2 (.clk(clk), .rst_n(rst_n), .checksum_q(chk_w2));
  soft_mac_array #(.W(1), .M(1), .ACC_W(ACC_W)) dut_w1 (.clk(clk), .rst_n(rst_n), .checksum_q(chk_w1));

  // Behavioral reference: mirrors soft_mac_array's g_lane body exactly for lane i=0, M=1 (so the
  // xor_reduce_tree degenerates to a single N=1 leaf: a plain 1-cycle registered pass-through).
  // One reference pipeline per W, hand-unrolled (kept simple/explicit rather than parameterized,
  // since only W in {4,2,1} is exercised here).
  logic [3:0] ref_a4_q, ref_b4_q;
  logic [7:0] ref_prod4_q;
  logic [ACC_W-1:0] ref_acc4_q, ref_chk4_q;

  logic [1:0] ref_a2_q, ref_b2_q;
  logic [3:0] ref_prod2_q;
  logic [ACC_W-1:0] ref_acc2_q, ref_chk2_q;

  logic [1:0] ref_a1_q, ref_b1_q;   // W=1 borrows the 2-bit LFSR, low bit is the operand
  logic [1:0] ref_prod1_q;
  logic [ACC_W-1:0] ref_acc1_q, ref_chk1_q;

  // W=4 reference
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      ref_a4_q <= ((32'hC0FF_EE01 + 1) % 15) + 1;
      ref_b4_q <= ((32'h1234_5679 + 2) % 15) + 1;
    end else begin
      ref_a4_q <= {ref_a4_q[2:0], ^(ref_a4_q & 4'b1100)};
      ref_b4_q <= {ref_b4_q[2:0], ^(ref_b4_q & 4'b1100)};
    end
  end
  always_ff @(posedge clk) begin
    if (!rst_n) ref_prod4_q <= '0;
    else        ref_prod4_q <= ref_a4_q * ref_b4_q;
  end
  always_ff @(posedge clk) begin
    if (!rst_n) ref_acc4_q <= '0;
    else        ref_acc4_q <= ref_acc4_q + {{(ACC_W - 8){1'b0}}, ref_prod4_q};
  end
  always_ff @(posedge clk) begin
    if (!rst_n) ref_chk4_q <= '0;
    else        ref_chk4_q <= ref_acc4_q;
  end

  // W=2 reference
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      ref_a2_q <= ((32'hC0FF_EE01 + 1) % 3) + 1;
      ref_b2_q <= ((32'h1234_5679 + 2) % 3) + 1;
    end else begin
      ref_a2_q <= {ref_a2_q[0:0], ^(ref_a2_q & 2'b11)};
      ref_b2_q <= {ref_b2_q[0:0], ^(ref_b2_q & 2'b11)};
    end
  end
  always_ff @(posedge clk) begin
    if (!rst_n) ref_prod2_q <= '0;
    else        ref_prod2_q <= ref_a2_q * ref_b2_q;
  end
  always_ff @(posedge clk) begin
    if (!rst_n) ref_acc2_q <= '0;
    else        ref_acc2_q <= ref_acc2_q + {{(ACC_W - 4){1'b0}}, ref_prod2_q};
  end
  always_ff @(posedge clk) begin
    if (!rst_n) ref_chk2_q <= '0;
    else        ref_chk2_q <= ref_acc2_q;
  end

  // W=1 reference (LFSR_W=2, operand is the LSB)
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      ref_a1_q <= ((32'hC0FF_EE01 + 1) % 3) + 1;
      ref_b1_q <= ((32'h1234_5679 + 2) % 3) + 1;
    end else begin
      ref_a1_q <= {ref_a1_q[0:0], ^(ref_a1_q & 2'b11)};
      ref_b1_q <= {ref_b1_q[0:0], ^(ref_b1_q & 2'b11)};
    end
  end
  always_ff @(posedge clk) begin
    if (!rst_n) ref_prod1_q <= '0;
    else        ref_prod1_q <= ref_a1_q[0] * ref_b1_q[0];
  end
  always_ff @(posedge clk) begin
    if (!rst_n) ref_acc1_q <= '0;
    else        ref_acc1_q <= ref_acc1_q + {{(ACC_W - 2){1'b0}}, ref_prod1_q};
  end
  always_ff @(posedge clk) begin
    if (!rst_n) ref_chk1_q <= '0;
    else        ref_chk1_q <= ref_acc1_q;
  end

  // ---------------------------------------------------------------------------------------------
  // Part 2: liveness check, M=16, W=2.
  // ---------------------------------------------------------------------------------------------
  logic [ACC_W-1:0] chk_multi;
  soft_mac_array #(.W(2), .M(16), .ACC_W(ACC_W)) dut_multi (.clk(clk), .rst_n(rst_n), .checksum_q(chk_multi));

  int distinct_count;
  logic [ACC_W-1:0] seen[$];

  initial begin
    rst_n = 0;
    repeat (5) @(posedge clk);
    rst_n = 1;

    // Bit-exact comparison every cycle for a long window (well past every pipeline's fill latency).
    for (int c = 0; c < 500; c++) begin
      @(posedge clk);
      check(chk_w4 === ref_chk4_q, $sformatf("W=4 cyc=%0d dut=%0h ref=%0h", c, chk_w4, ref_chk4_q));
      check(chk_w2 === ref_chk2_q, $sformatf("W=2 cyc=%0d dut=%0h ref=%0h", c, chk_w2, ref_chk2_q));
      check(chk_w1 === ref_chk1_q, $sformatf("W=1 cyc=%0d dut=%0h ref=%0h", c, chk_w1, ref_chk1_q));

      if (c > 20) begin  // past the M=16 tree's fill latency
        automatic bit is_new = 1'b1;
        foreach (seen[j]) if (seen[j] == chk_multi) is_new = 1'b0;
        if (is_new) seen.push_back(chk_multi);
      end
    end

    distinct_count = seen.size();
    check(distinct_count >= 10,
          $sformatf("M=16 liveness: only %0d distinct checksum values over 480 cycles (expected >= 10 — suspect trimmed/dangling logic)",
                     distinct_count));

    if (errors == 0) $display("PASS");
    else             $display("FAIL: %0d error(s)", errors);
    $finish;
  end

  initial begin #2_000_000; $display("FAIL: timeout"); $finish; end

endmodule
