// l1_pe_cell — one processing element of the L1 systolic tile (issue #11, PLAN §7 L1 + §3 LV1).
//
// Wraps #9's `l0_mac_block` **verbatim** as the multiply-accumulate core (the "known-good PE cell"
// the issue says to reuse). Because #9 proved Agilex 3 tensor mode is unreachable from hand-written
// RTL on Quartus 26.1 (PLAN §3 LV2 caveat, msg 24863), that core is classic-mode: a 10-lane INT8
// dot product per cycle, ~5 classic DSP blocks, ≤10 MACs/DSP-block — half the tensor-mode target.
// L1 therefore characterises the fmax / retiming behaviour of the *classic-mode* datapath, which is
// the honest "what can custom RTL do on this silicon" number (PLAN §7 L0 caveat).
//
// This cell adds, around that verbatim core, the two things L1 studies:
//   1. a **stationary** weight register (weight-stationary dataflow): weights are latched once from
//      the per-cell weight source at `load`, then held for the whole measured run. They are loaded
//      from a live (LFSR) source through an enable-gated register, so synthesis cannot prove them
//      constant and fold the multiplies into LUTs (the 0-DSP trap #9's README documents) — they
//      stay real DSP multiplies.
//   2. the LV1 reset-style knob (`RESET_HEAVY`) applied to the datapath registers this cell owns.
//
// LV1 discipline (PLAN §3 LV1, AGENTS.md): in the RETIME_CLEAN build the datapath pipeline register
// (`data_q`) is **reset-less** so 2nd-gen Hyperflex is free to retime it; only architectural state
// carries a synchronous reset. In the RESET_HEAVY (anti-pattern) build every register — including
// this datapath one — carries a synchronous reset from a single shared net, which pins the register
// and blocks retiming. `l0_mac_block`'s own accumulator keeps its `clear` semantics unchanged in
// both builds (it mirrors the real DSP's clr port and is not a retiming target); the measurable
// LV1 delta lives in the systolic/pipeline registers this cell and `l1_pe_array` own.
`ifndef L1_PE_CELL_SV
`define L1_PE_CELL_SV
module l1_pe_cell #(
    parameter int N_TAPS         = 10,
    parameter bit HAS_CASCADE_IN = 1'b1,
    parameter bit RESET_HEAVY    = 1'b0
) (
    input  logic                clk,
    input  logic                rst,          // synchronous, used only when RESET_HEAVY
    input  logic                clear,        // reseed/zero accumulator at run start (architectural)
    input  logic                load,         // latch stationary weights (1 cycle at run start)
    input  logic signed [7:0]   weight_src [N_TAPS],  // live weight source (LFSR) to latch
    input  logic signed [7:0]   data_in    [N_TAPS],  // systolic data entering this cell this cycle
    input  logic signed [31:0]  cascade_in,
    output logic signed [31:0]  cascade_out,
    output logic signed [7:0]   data_out   [N_TAPS]   // registered data forwarded to the next column
);

  // ---- stationary weight register (loaded once at `load`, then held) ----
  logic signed [7:0] weight_q [N_TAPS];
  always_ff @(posedge clk) begin
    if (RESET_HEAVY && rst) begin
      for (int i = 0; i < N_TAPS; i++) weight_q[i] <= '0;
    end else if (load) begin
      for (int i = 0; i < N_TAPS; i++) weight_q[i] <= weight_src[i];
    end
  end

  // ---- systolic data pipeline register (datapath: reset-less in CLEAN, reset in HEAVY) ----
  logic signed [7:0] data_q [N_TAPS];
  always_ff @(posedge clk) begin
    if (RESET_HEAVY && rst) begin
      for (int i = 0; i < N_TAPS; i++) data_q[i] <= '0;
    end else begin
      for (int i = 0; i < N_TAPS; i++) data_q[i] <= data_in[i];
    end
  end
  assign data_out = data_q;

  // ---- verbatim #9 MAC core: 10-lane dot product + column cascade ----
  l0_mac_block #(.N_TAPS(N_TAPS), .HAS_CASCADE_IN(HAS_CASCADE_IN)) u_mac (
      .clk         (clk),
      .clear       (clear),
      .weight_taps (weight_q),
      .data_taps   (data_in),   // combinational data into the MAC; data_q forwards to next column
      .cascade_in  (cascade_in),
      .cascade_out (cascade_out)
  );

endmodule
`endif
