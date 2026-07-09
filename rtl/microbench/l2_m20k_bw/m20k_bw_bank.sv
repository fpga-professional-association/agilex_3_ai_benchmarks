// m20k_bw_bank — one M20K read port + free-running read address counter + XOR-fold checksum sink
// (issue #12, PLAN §7 L2 + §3 LV3).
//
// The memory is initialised at elaboration with a deterministic per-bank xorshift32 sequence
// (m20k_bw_pkg::bank_seed/xorshift32_next) so scripts/l2_golden.py can precompute the exact
// checksum any (BANK_ID, ADDR_WIDTH, K, OUTPUT_REG) combination will produce — a read-only ROM
// content is exactly what a real M20K's init-file (.mif-equivalent initial block) mechanism
// supports, and Verilator elaborates the same `initial` block identically (issue #12 do-not:
// "do not report bandwidth from a run whose checksum failed" needs the two to agree bit-for-bit).
//
// `addr` is a registered read address (the minimum latency any M20K read incurs) so `mem[addr]`
// is a standard registered-address / combinational-read inference. OUTPUT_REG adds ONE further
// register stage directly on the read data with no other logic between it and the memory read —
// exactly the M20K's own optional dedicated output-register hardware feature (PLAN §3 LV3
// "output registers on" vs the config-(c) "output registers off" anti-pattern; this is the fmax
// lever the issue wants visible in the Quartus Fitter report, not a simulation-only difference).
`ifndef M20K_BW_BANK_SV
`define M20K_BW_BANK_SV
module m20k_bw_bank
  import m20k_bw_pkg::*;
#(
    parameter int DATA_WIDTH = 32,
    parameter int ADDR_WIDTH = 9,     // per-bank depth = 2**ADDR_WIDTH
    parameter int BANK_ID    = 0,
    parameter bit OUTPUT_REG = 1'b1
) (
    input  logic                  clk,
    input  logic                  rst,
    input  logic                  start,    // pulse: reseed address + clear checksum for a new run
    input  logic                  rd_en,    // this cycle's read pulse for this bank
    output logic [DATA_WIDTH-1:0] checksum_q
);
  localparam int DEPTH = (1 << ADDR_WIDTH);

  (* ramstyle = "M20K" *) logic [DATA_WIDTH-1:0] mem[0:DEPTH-1];

  initial begin
    logic [31:0] s;
    s = bank_seed(BANK_ID);
    for (int i = 0; i < DEPTH; i++) begin
      s = xorshift32_next(s);
      mem[i] = s[DATA_WIDTH-1:0];
    end
  end

  logic [ADDR_WIDTH-1:0] addr;
  logic [DATA_WIDTH-1:0] rd_data;

  always_ff @(posedge clk) begin
    if (rst || start) addr <= '0;
    else if (rd_en) addr <= addr + 1'b1;  // wraps naturally at DEPTH (any K works, incl. K>DEPTH)
  end

  assign rd_data = mem[addr];

  generate
    if (OUTPUT_REG) begin : g_outreg
      // dedicated M20K output-register stage: registers rd_data with nothing else in between, and
      // rd_en is delayed by the SAME one cycle so the pair (valid_q, rd_data_q) presented to the
      // checksum accumulator always refers to the same read (total latency: 2 edges from rd_en to
      // the checksum update landing, vs. 1 edge for g_noreg below — this +1-edge delta, applied
      // uniformly per bank regardless of geometry, is exactly the top level's DRAIN_CYCLES).
      logic [DATA_WIDTH-1:0] rd_data_q;
      logic                  valid_q;

      always_ff @(posedge clk) begin
        rd_data_q <= rd_data;
        valid_q   <= rd_en;
      end

      always_ff @(posedge clk) begin
        if (rst || start) checksum_q <= '0;
        else if (valid_q) checksum_q <= checksum_q ^ rd_data_q;
      end
    end else begin : g_noreg
      // minimum M20K latency: registered address + combinational data out, so the checksum can
      // fold rd_data in on the very same edge that samples rd_en (1 edge total, matching the
      // registered-address read that already exists above — no additional pipeline stage).
      always_ff @(posedge clk) begin
        if (rst || start) checksum_q <= '0;
        else if (rd_en) checksum_q <= checksum_q ^ rd_data;
      end
    end
  endgenerate

endmodule
`endif
