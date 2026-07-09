// l1_pe_core — the L1 hot-domain core (issue #11): the PE array plus its run-control FSM, cycle
// counter and checksum sink, all on ONE clock. `l1_pe_top` instantiates this on the hot clock and
// wraps it with a CSR slave that is either on the same clock (MERGED) or a separate cool clock with
// CDC wrappers at the seam (ISOLATED) — the LV4 variable. Keeping the core single-clock means the
// LV4 study moves only the seam, never the datapath.
//
// Architectural state (running/done/cycle/vector/checksum/n_vectors) always carries a synchronous
// reset (PLAN §3 LV1: "sync reset only where architecture needs state"); the array's datapath
// pipeline registers carry a reset only in the RESET_HEAVY build (that is the LV1 variable).
`ifndef L1_PE_CORE_SV
`define L1_PE_CORE_SV
module l1_pe_core #(
    parameter int NUM_ROWS    = 4,
    parameter int NUM_COLS    = 4,
    parameter int N_TAPS      = 10,
    parameter bit RESET_HEAVY = 1'b0,
    parameter int FILL_MARGIN = 6
) (
    input  logic         clk,
    input  logic         rst,             // synchronous, active-high
    input  logic         start,           // 1-cycle pulse (already in this clock domain)
    input  logic [31:0]  n_vectors,       // stable when `start` asserts
    output logic         running,
    output logic         done,
    output logic         done_stb,        // 1-cycle pulse when the run completes
    output logic [63:0]  res_cycle,
    output logic [31:0]  res_vector,
    output logic [31:0]  res_checksum
);
  localparam int FILL_CYCLES = NUM_ROWS + NUM_COLS + FILL_MARGIN;

  logic         running_q, done_q;
  logic         clear_q, load_q;
  logic [63:0]  cycle_q;
  logic [31:0]  vector_q, checksum_q, n_vectors_q;
  logic [63:0]  res_cycle_q;
  logic [31:0]  res_vector_q, res_checksum_q;
  logic         done_stb_q;

  logic signed [31:0] array_result;

  wire retire = running_q && (cycle_q >= 64'(FILL_CYCLES));

  always_ff @(posedge clk) begin
    if (rst) begin
      running_q      <= 1'b0;
      done_q         <= 1'b0;
      clear_q        <= 1'b0;
      load_q         <= 1'b0;
      cycle_q        <= '0;
      vector_q       <= '0;
      checksum_q     <= '0;
      n_vectors_q    <= '0;
      res_cycle_q    <= '0;
      res_vector_q   <= '0;
      res_checksum_q <= '0;
      done_stb_q     <= 1'b0;
    end else begin
      // clear pulses on start; load pulses one cycle later (latches stationary weights)
      clear_q    <= start;
      load_q     <= clear_q;
      done_stb_q <= 1'b0;

      if (start) begin
        running_q   <= 1'b1;
        done_q      <= 1'b0;
        cycle_q     <= '0;
        vector_q    <= '0;
        checksum_q  <= '0;
        n_vectors_q <= n_vectors;
      end else if (running_q) begin
        cycle_q <= cycle_q + 64'd1;
        if (retire) begin
          checksum_q <= checksum_q ^ 32'(array_result);
          vector_q   <= vector_q + 1'b1;
          if ((vector_q + 1'b1) >= n_vectors_q) begin
            running_q      <= 1'b0;
            done_q         <= 1'b1;
            done_stb_q     <= 1'b1;
            res_cycle_q    <= cycle_q + 64'd1;
            res_vector_q   <= vector_q + 1'b1;
            res_checksum_q <= checksum_q ^ 32'(array_result);
          end
        end
      end
    end
  end

  l1_pe_array #(
      .NUM_ROWS(NUM_ROWS), .NUM_COLS(NUM_COLS), .N_TAPS(N_TAPS), .RESET_HEAVY(RESET_HEAVY)
  ) u_array (
      .clk(clk), .rst(rst), .clear(clear_q), .load(load_q), .result(array_result)
  );

  assign running      = running_q;
  assign done         = done_q;
  assign done_stb     = done_stb_q;
  assign res_cycle    = res_cycle_q;
  assign res_vector   = res_vector_q;
  assign res_checksum = res_checksum_q;

endmodule
`endif
