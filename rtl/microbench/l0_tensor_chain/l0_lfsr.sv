// l0_lfsr — parameterized Galois LFSR stimulus generator (issue #9).
//
// Not a whitening/crypto PRNG and TAPS is not claimed to be a maximal-length polynomial: this is
// pure deterministic stimulus generation for the L0 tensor-chain microbench, where the only
// requirement is that the RTL and the companion golden model (sw/host/l0_golden.py) compute the
// exact same sequence from the same SEED/TAPS. `clear` reseeds synchronously; this is architectural
// state (PLAN §3 LV1) — the seed must be a known value for the checksum self-check to be
// reproducible, so it is reset like scoreboard.sv's own accumulators, not treated as a reset-less
// datapath pipeline register.
`ifndef L0_LFSR_SV
`define L0_LFSR_SV
module l0_lfsr #(
    parameter int WIDTH = 80,
    parameter logic [WIDTH-1:0] SEED = '0,
    parameter logic [WIDTH-1:0] TAPS = '0
) (
    input  logic             clk,
    input  logic              clear,
    output logic [WIDTH-1:0]  state
);

  logic [WIDTH-1:0] state_q;
  wire              feedback = state_q[0];

  always_ff @(posedge clk) begin
    if (clear) state_q <= SEED;
    else       state_q <= (state_q >> 1) ^ (feedback ? TAPS : '0);
  end

  assign state = state_q;

endmodule
`endif
