// soft_mac_array — parameterized array of W-bit x W-bit soft-logic MACs (issue #10, PLAN §7 L0b).
//
// Past the device's 138 DSPs, sub-8-bit MACs have to land in ALM soft logic; Quartus fractal
// synthesis is supposed to pack those small multiplier trees efficiently (docs/toolchain.md records
// whether/how that's confirmed for Agilex 3). This module is the characterization vehicle: M
// independent W x W unsigned multiply-accumulate lanes, W in {4,2,1}, M sized per grid point by
// scripts/sweep_l0b.py. Two things this design must guarantee so the compiled resource count means
// anything:
//
//   1. No constant-folded operands. Every lane's two operands come from their own free-running LFSR
//      (distinct non-zero per-lane seeds), never from a literal/parameter tied directly into the
//      multiplier — Quartus cannot constant-propagate a register with feedback.
//   2. No dangling logic. Every lane's accumulator feeds a registered, log-depth XOR-reduce tree
//      (xor_reduce_tree) down to the single checksum_q output, so nothing is trimmed as unused.
//      That tree is pipelined specifically so it never becomes the timing-critical path itself (see
//      xor_reduce_tree) — the fmax this design closes at should reflect the MAC lanes, not the sink.
//
// All state here (LFSRs, accumulators, checksum) is architectural (free-running counters, not a
// datapath pipeline), so per AGENTS.md it uses synchronous reset only; no clock gating, no async
// reset anywhere.
//
// Fractal synthesis: the `(* altera_attribute = "-name FRACTAL_SYNTHESIS ON" *)` attribute below is
// applied to the per-lane product register per docs/toolchain.md's "Fractal synthesis" section
// (cites Quartus Prime Pro Edition User Guide: Design Compilation, doc 683236, v25.3). Whether it
// actually takes effect on A3CY100BM16AE7S (vs. being silently ignored / falling back to default
// soft-multiplier mapping) is confirmed empirically per grid point by scripts/sweep_l0b.py grepping
// the Quartus synthesis report — see docs/toolchain.md for the result.
`default_nettype none
module soft_mac_array #(
    parameter int W  = 4,     // operand width in bits: the INT4/INT2/INT1 sweep axis. Must be in {1,2,4}.
    parameter int M  = 256,   // number of independent MAC lanes (resource usage must scale ~linearly with this)
    parameter int ACC_W = 24  // per-lane accumulator width, held fixed across W so density differences
                              // in the swept curve come from the multiplier tree, not accumulator size
) (
    input  wire               clk,
    input  wire               rst_n,      // architectural reset: LFSR + accumulator + checksum-tree state
    output logic [ACC_W-1:0]  checksum_q  // registered sink — every lane must reach here or it gets trimmed
);

  // Elaboration-time parameter sanity (simulation-only; synthesis tools ignore `initial`/`$fatal`).
  initial begin
    if (!(W == 1 || W == 2 || W == 4))
      $fatal(1, "soft_mac_array: W=%0d unsupported — PLAN Sec7 L0b only sweeps W in {1,2,4}", W);
    if (ACC_W <= 2 * W)
      $fatal(1, "soft_mac_array: ACC_W=%0d too small for W=%0d (need > 2*W headroom)", ACC_W, W);
    if (M < 1)
      $fatal(1, "soft_mac_array: M=%0d must be >= 1", M);
  end

  // LFSR stimulus width: widened to 2 bits minimum so W=1 still gets genuine LFSR-derived (not a
  // bare toggle) pseudo-random bits — see module header.
  localparam int LFSR_W      = (W < 2) ? 2 : W;
  localparam int LFSR_PERIOD = (1 << LFSR_W) - 1;   // maximal-length nonzero-state period

  // Standard two-tap maximal-length Fibonacci LFSR polynomials (published table, e.g. Xilinx
  // XAPP052 "Efficient Shift Registers, LFSR Counters"): x^2+x+1, x^3+x^2+1, x^4+x^3+1. Explicit
  // sized casts avoid implicit-width warnings under Quartus/Verilator's strict lint (only the arm
  // matching this instance's LFSR_W is ever actually selected; the cast keeps every arm's width
  // consistent regardless).
  localparam logic [LFSR_W-1:0] LFSR_TAPS =
      (LFSR_W == 2) ? LFSR_W'(2'b11)   :
      (LFSR_W == 3) ? LFSR_W'(3'b110)  :
                      LFSR_W'(4'b1100);

  logic [M-1:0][ACC_W-1:0] acc_q;

  generate
    for (genvar i = 0; i < M; i++) begin : g_lane
      // Distinct, guaranteed-nonzero seeds per lane per operand so no two lanes (and no two
      // operands within a lane) run in lockstep, and no LFSR ever locks at the all-zero state.
      localparam logic [LFSR_W-1:0] SEED_A = LFSR_W'(((32'hC0FF_EE01 + i * 2 + 1) % LFSR_PERIOD) + 1);
      localparam logic [LFSR_W-1:0] SEED_B = LFSR_W'(((32'h1234_5679 + i * 2 + 2) % LFSR_PERIOD) + 1);

      logic [LFSR_W-1:0] lfsr_a_q, lfsr_b_q;

      (* altera_attribute = "-name FRACTAL_SYNTHESIS ON" *) logic [2*W-1:0] product_q;

      always_ff @(posedge clk) begin
        if (!rst_n) begin
          lfsr_a_q <= SEED_A;
          lfsr_b_q <= SEED_B;
        end else begin
          lfsr_a_q <= {lfsr_a_q[LFSR_W-2:0], ^(lfsr_a_q & LFSR_TAPS)};
          lfsr_b_q <= {lfsr_b_q[LFSR_W-2:0], ^(lfsr_b_q & LFSR_TAPS)};
        end
      end

      // Unsigned W x W multiply, registered. Operands are LFSR register bits — never a constant.
      always_ff @(posedge clk) begin
        if (!rst_n) product_q <= '0;
        else        product_q <= lfsr_a_q[W-1:0] * lfsr_b_q[W-1:0];
      end

      always_ff @(posedge clk) begin
        if (!rst_n) acc_q[i] <= '0;
        else        acc_q[i] <= acc_q[i] + {{(ACC_W - 2 * W){1'b0}}, product_q};
      end
    end
  endgenerate

  xor_reduce_tree #(
      .WIDTH(ACC_W),
      .N    (M)
  ) u_checksum (
      .clk     (clk),
      .rst_n   (rst_n),
      .data_in (acc_q),
      .data_out(checksum_q)
  );

endmodule
`default_nettype wire
