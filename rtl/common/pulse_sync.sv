// pulse_sync — carry a single-cycle pulse from src_clk to dst_clk (issue #15).
// Toggle-flop on the source side, 2-flop synchronizer + edge-detect on the destination side.
// Correct only when source pulses are spaced further apart than the destination can resolve them
// (several dst cycles); that holds for our uses (START/SOFT_RESET clear pulses at run boundaries).
`ifndef PULSE_SYNC_SV
`define PULSE_SYNC_SV
module pulse_sync (
    input  logic src_clk,
    input  logic src_rst,     // synchronous reset in the source domain
    input  logic src_pulse,   // 1-cycle pulse in src_clk
    input  logic dst_clk,
    output logic dst_pulse    // 1-cycle pulse in dst_clk
);
  logic toggle_q;
  always_ff @(posedge src_clk) begin
    if (src_rst) toggle_q <= 1'b0;
    else if (src_pulse) toggle_q <= ~toggle_q;
  end

  logic [2:0] sync_ff;
  always_ff @(posedge dst_clk) sync_ff <= {sync_ff[1:0], toggle_q};
  assign dst_pulse = sync_ff[2] ^ sync_ff[1];
endmodule
`endif
