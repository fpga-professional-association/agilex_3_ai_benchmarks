// cdc_bit_sync — N-flop synchronizer for a single-bit level crossing clock domains (issue #15).
// The only sanctioned primitive for level CDC (AGENTS.md: no hand-rolled synchronizers in
// functional modules). Reset-less by design; the synchronized value simply flushes after a few
// destination-clock edges. Do NOT use this for multi-bit buses — use async_fifo for those.
`ifndef CDC_BIT_SYNC_SV
`define CDC_BIT_SYNC_SV
module cdc_bit_sync #(
    parameter int STAGES = 2
) (
    input  logic dst_clk,
    input  logic d,       // level in the source domain (must be stable-ish / sticky)
    output logic q        // level synchronized into dst_clk
);
  // verilator lint_off UNOPTFLAT
  logic [STAGES-1:0] sync_ff;
  // verilator lint_on UNOPTFLAT
  always_ff @(posedge dst_clk) sync_ff <= {sync_ff[STAGES-2:0], d};
  assign q = sync_ff[STAGES-1];
endmodule
`endif
