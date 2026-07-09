// l1_pe_array — weight-stationary systolic tile for the L1 fmax-vs-size sweep (issue #11).
//
// A NUM_ROWS x NUM_COLS grid of `l1_pe_cell` (each wrapping #9's verbatim classic-mode
// `l0_mac_block`). Dataflow (textbook weight-stationary systolic tile):
//   - activations enter each row's LEFT edge and shift RIGHT one column per cycle (the systolic
//     pipeline register lives in l1_pe_cell.data_q);
//   - partial sums accumulate DOWN each column through the DSP cascade (l0_mac_block.cascade_*),
//     never through ALM adder trees (PLAN §3 LV2);
//   - each column's bottom emits one result; the NUM_COLS results are summed and registered into
//     `result` for the top's checksum sink.
// "M20K-fed edges" (issue deliverable): the activation stream comes from an inferred, initialised,
// registered-read on-chip RAM (`ram_style = "M20K"`), read by a free-running address counter — real
// BRAM read timing in the edge path, not operands conjured from thin air. One shared buffer feeds
// all rows; each row decorrelates it with a cheap combinational rotate by row index, so the buffer
// stays small (a few M20Ks) instead of scaling with the array and swamping the DSP-vs-fmax study.
//
// Weight-stationary + non-constant: weights are latched once at `load` from per-cell free-running
// LFSRs, so synthesis cannot fold the multiplies to LUTs (0-DSP trap, #9 README) yet they are held
// for the measured run. The whole datapath is deterministic given the seeds, so RETIME_CLEAN vs
// RESET_HEAVY (and later MERGED vs ISOLATED in the top) produce a bit-identical checksum — the
// testbench asserts that, which is what lets the fmax delta be attributed to the timing discipline
// alone (issue "do not": only the RTL/domain variable moves).
`ifndef L1_PE_ARRAY_SV
`define L1_PE_ARRAY_SV
module l1_pe_array #(
    parameter int NUM_ROWS    = 4,
    parameter int NUM_COLS    = 4,
    parameter int N_TAPS      = 10,
    parameter bit RESET_HEAVY = 1'b0,
    parameter int ACT_DEPTH   = 1024   // activation M20K depth (>=512 to infer M20K, not MLAB)
) (
    input  logic                clk,
    input  logic                rst,     // synchronous; drives datapath regs only when RESET_HEAVY
    input  logic                clear,   // run-start: reseed LFSRs + M20K addr + accumulators
    input  logic                load,    // 1-cycle: latch stationary weights
    output logic signed [31:0]  result   // registered combined column output, this cycle
);

  localparam int VEC_W  = 8 * N_TAPS;                 // one activation/weight vector, bits
  localparam int ADDR_W = $clog2(ACT_DEPTH);

  // ---- activation buffer: inferred, initialised, registered-read M20K ----
  (* ramstyle = "M20K" *) logic [VEC_W-1:0] act_mem [ACT_DEPTH];
  // Deterministic init (a fixed byte recurrence). Content values don't matter for the timing study;
  // what matters is that the read is at runtime (uninferrable-as-constant) and reproducible.
  initial begin
    logic [7:0] s;
    s = 8'h6D;
    for (int a = 0; a < ACT_DEPTH; a++) begin
      logic [VEC_W-1:0] w;
      for (int b = 0; b < N_TAPS; b++) begin
        s = 8'((s * 8'd5) + 8'd23);   // fixed LCG-ish byte recurrence
        w[8*b +: 8] = s;
      end
      act_mem[a] = w;
    end
  end

  logic [ADDR_W-1:0]  rd_addr_q;
  logic [VEC_W-1:0]   act_rd_q;      // registered read data (M20K output register)
  always_ff @(posedge clk) begin
    if (clear) begin
      rd_addr_q <= '0;
    end else begin
      rd_addr_q <= rd_addr_q + 1'b1;
    end
    act_rd_q <= act_mem[rd_addr_q];  // synchronous read -> M20K
  end

  // Per-row edge activation: shared M20K word, decorrelated per row by a byte rotate of `row+1`.
  logic signed [7:0] row_edge [NUM_ROWS][N_TAPS];
  for (genvar r = 0; r < NUM_ROWS; r++) begin : g_rowfeed
    for (genvar t = 0; t < N_TAPS; t++) begin : g_tapfeed
      localparam int SRC = (t + r + 1) % N_TAPS;   // static per-(r,t) tap rotate
      assign row_edge[r][t] = act_rd_q[8*SRC +: 8];
    end
  end

  // ---- weight LFSRs (one per cell), free-running, latched stationary in each cell at `load` ----
  // Seed derived from (r,c) so every cell has distinct weights; distinct from the activation stream.
  function automatic logic [VEC_W-1:0] wseed(input int r, input int c);
    logic [VEC_W-1:0] s;
    for (int k = 0; k < N_TAPS; k++) s[8*k +: 8] = (8'(r*13 + c*7 + k*3) ^ 8'h5A) | 8'h01;
    return s;
  endfunction
  localparam logic [15:0] TAPS_UNIT = 16'hB465;   // same nonzero mask family as #9
  localparam logic [VEC_W-1:0] WTAPS = VEC_W'({(VEC_W/16 + 1){TAPS_UNIT}});

  // ---- the PE grid (all inter-cell wiring via array-scope 2D signals — no hierarchical refs) ----
  logic signed [7:0]  fwd  [NUM_ROWS][NUM_COLS][N_TAPS];  // each cell's registered forwarded data
  logic signed [31:0] casc [NUM_ROWS][NUM_COLS];          // each cell's cascade_out
  logic signed [31:0] col_out [NUM_COLS];

  for (genvar r = 0; r < NUM_ROWS; r++) begin : g_row
    for (genvar c = 0; c < NUM_COLS; c++) begin : g_col
      // per-cell weight source (free-running LFSR, latched stationary inside the cell at `load`)
      logic [VEC_W-1:0] wstate;
      l0_lfsr #(.WIDTH(VEC_W), .SEED(wseed(r, c)), .TAPS(WTAPS)) u_wlfsr (
          .clk(clk), .clear(clear), .state(wstate)
      );
      logic signed [7:0] wsrc [N_TAPS];
      for (genvar t = 0; t < N_TAPS; t++) begin : g_wsrc
        assign wsrc[t] = wstate[8*t +: 8];
      end

      // systolic data: col 0 from this row's M20K edge; col c>0 from the left neighbour's fwd reg
      logic signed [7:0] cell_din [N_TAPS];
      for (genvar t = 0; t < N_TAPS; t++) begin : g_din
        assign cell_din[t] = (c == 0) ? row_edge[r][t] : fwd[r][c-1][t];
      end

      l1_pe_cell #(
          .N_TAPS(N_TAPS), .HAS_CASCADE_IN(r != 0), .RESET_HEAVY(RESET_HEAVY)
      ) u_cell (
          .clk(clk), .rst(rst), .clear(clear), .load(load),
          .weight_src(wsrc),
          .data_in(cell_din),
          .cascade_in(r == 0 ? 32'sd0 : casc[r-1][c]),
          .cascade_out(casc[r][c]),
          .data_out(fwd[r][c])
      );
    end
  end
  for (genvar c = 0; c < NUM_COLS; c++) begin : g_colout
    assign col_out[c] = casc[NUM_ROWS-1][c];
  end

  // ---- combine the column results + register (LV1 target: reset-less in CLEAN, reset in HEAVY) ----
  logic signed [31:0] sum_comb;
  always_comb begin
    sum_comb = '0;
    for (int c = 0; c < NUM_COLS; c++) sum_comb += col_out[c];
  end
  logic signed [31:0] result_q;
  always_ff @(posedge clk) begin
    if (RESET_HEAVY && rst) result_q <= '0;
    else                    result_q <= sum_comb;
  end
  assign result = result_q;

endmodule
`endif
