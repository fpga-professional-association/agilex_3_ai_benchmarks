// xor_reduce_tree — registered, log-depth XOR reduction of N same-width words (issue #10).
//
// Recursively splits the N inputs in half (ceil/floor, so any N >= 1 works, not just powers of
// two) and registers one XOR per level. Reduction depth is ceil(log2(N)) pipeline stages, each with
// a single-XOR combinational path, so this tree never becomes the timing-critical structure for a
// wide array — soft_mac_array's own multiplier/accumulator chain stays the fmax-limiting logic that
// PLAN §7 L0b wants characterized, not an artifact of how the checksum sink is built.
//
// Architectural state (sync reset only, no clock gating, no async reset — AGENTS.md RTL conventions;
// this is accumulator/counter-like state, not a datapath pipeline register).
`default_nettype none
module xor_reduce_tree #(
    parameter int WIDTH = 24,
    parameter int N     = 256    // number of input words; N >= 1
) (
    input  wire                     clk,
    input  wire                     rst_n,
    input  wire  [N-1:0][WIDTH-1:0] data_in,
    output logic        [WIDTH-1:0] data_out
);

  generate
    if (N == 1) begin : g_leaf
      always_ff @(posedge clk) begin
        if (!rst_n) data_out <= '0;
        else        data_out <= data_in[0];
      end
    end else begin : g_split
      localparam int N_LO = N / 2;
      localparam int N_HI = N - N_LO;

      logic [WIDTH-1:0] lo_q, hi_q;

      xor_reduce_tree #(.WIDTH(WIDTH), .N(N_LO)) u_lo (
          .clk    (clk),
          .rst_n  (rst_n),
          .data_in(data_in[N_LO-1:0]),
          .data_out(lo_q)
      );
      xor_reduce_tree #(.WIDTH(WIDTH), .N(N_HI)) u_hi (
          .clk    (clk),
          .rst_n  (rst_n),
          .data_in(data_in[N-1:N_LO]),
          .data_out(hi_q)
      );

      always_ff @(posedge clk) begin
        if (!rst_n) data_out <= '0;
        else        data_out <= lo_q ^ hi_q;
      end
    end
  endgenerate

endmodule
`default_nettype wire
