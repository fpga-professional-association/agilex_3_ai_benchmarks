// l0_mac_block — one N_TAPS-wide INT8 dot-product-and-cascade-accumulate stage (issue #9).
//
// This is the "one DSP block" unit of the L0 tensor-chain microbench. N_TAPS=10 matches the AI
// Tensor Block's per-column dot-product width (Agilex DSP UG §3.3.4, "Tensor Fixed-point Mode":
// "A signed 20-bit fixed-point DOT product ... performs 10 signed 8x8 multiplications"). This
// module is CLASSIC-MODE RTL (plain inferred `+`/`*`, no WYSIWYG primitive) — see
// rtl/microbench/l0_tensor_chain/README.md for why the real tensor-mode primitive
// (`inteleighteena_synth_tensor_mac` / the "Native AI Optimized DSP Agilex FPGA IP") could not be
// elaborated for FAMILY "Agilex 3" in this toolchain, and exactly what was tried. Because this
// module only exercises one of the tensor block's two columns, it delivers at most N_TAPS=10
// MACs/DSP/cycle here, half the PLAN §1 tensor-mode target of 20 — that gap IS the "silent 10x
// loss" PLAN §3 LV2 warns about, not a bug in this module.
//
// Cascade accumulation runs through cascade_in/cascade_out (PLAN LV2: "not ALM adder trees") —
// each block's own accumulate register chains from the previous block's registered output, exactly
// mirroring the real tensor block's cascade_data_in_col/cascade_data_out_col ports.
//
// `clear` zeros the accumulator on a fresh run (architectural state, tied to the CTRL.START event —
// not the system reset net); the real DSP UG primitive exposes the identical need via its own
// clr0/clr1 ports, so this is not a LV1 violation of "reset-less pipeline register", it is the
// same requirement the real hardware itself has.
`ifndef L0_MAC_BLOCK_SV
`define L0_MAC_BLOCK_SV
module l0_mac_block #(
    parameter int N_TAPS        = 10,
    parameter bit HAS_CASCADE_IN = 1'b1
) (
    input  logic                      clk,
    input  logic                      clear,
    input  logic signed [7:0]         weight_taps [N_TAPS],
    input  logic signed [7:0]         data_taps   [N_TAPS],
    input  logic signed [31:0]        cascade_in,
    output logic signed [31:0]        cascade_out
);

  logic signed [31:0] dot_comb;
  always_comb begin
    dot_comb = '0;
    for (int i = 0; i < N_TAPS; i++) begin
      dot_comb += signed'(weight_taps[i]) * signed'(data_taps[i]);
    end
  end

  logic signed [31:0] acc_q;
  always_ff @(posedge clk) begin
    if (clear)              acc_q <= '0;
    else if (HAS_CASCADE_IN) acc_q <= dot_comb + cascade_in;
    else                     acc_q <= dot_comb;
  end
  assign cascade_out = acc_q;

endmodule
`endif
