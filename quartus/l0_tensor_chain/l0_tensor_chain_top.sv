// l0_tensor_chain_top — self-contained Quartus compile harness for l0_tensor_chain (issue #9).
//
// This is a compile/fitter-report harness, NOT a hardware bring-up top level (that needs a
// JTAG-Avalon-MM master / System Console flow, PLAN §9 PH0-PH1, issue #7 — not yet closed at the
// time of writing). It exists so `l0_tensor_chain` can be synthesized and fit standalone (real DSP
// usage, real fmax) without pulling in a Platform Designer system: a tiny power-on sequencer drives
// the same two CSR writes sw/host/run_l0.py would (N_VECTORS, then CTRL.START), then continuously
// alternates reading CYCLES_LO (which latches the atomic snapshot, docs-register-map style) and
// CHECKSUM, registering both low bytes as pins. Exposing the checksum specifically (not just
// STATUS, which never depends on the arithmetic at all) is deliberate: STATUS alone would let the
// Fitter prove the whole LFSR/MAC datapath is dead logic (nothing observable would depend on its
// computed value) and legally optimize it away.
`default_nettype none
module l0_tensor_chain_top #(
    parameter int N_BLOCKS  = 8,
    parameter int N_VECTORS = 1_000_000
) (
    input  wire clk,
    input  wire rst_n,
    output wire [7:0] checksum_byte,
    output wire [7:0] cycles_byte
);
  import bench_pkg::*;

  logic [7:0]  csr_address_q;
  logic        csr_read_q;
  /* verilator lint_off UNUSEDSIGNAL */
  logic [31:0] csr_readdata;   // only the low byte is captured below; upper bits intentionally unused
  /* verilator lint_on UNUSEDSIGNAL */
  logic        csr_write_q;
  logic [31:0] csr_writedata_q;
  /* verilator lint_off UNUSEDSIGNAL */
  logic        csr_waitrequest;   // 0-wait slave (l0_tensor_chain.sv) — intentionally ignored here
  /* verilator lint_on UNUSEDSIGNAL */

  l0_tensor_chain #(.N_BLOCKS(N_BLOCKS)) u_dut (
      .clk(clk), .rst_n(rst_n),
      .csr_address(csr_address_q), .csr_read(csr_read_q), .csr_readdata(csr_readdata),
      .csr_write(csr_write_q), .csr_writedata(csr_writedata_q), .csr_waitrequest(csr_waitrequest)
  );

  // ---------------- power-on sequencer + continuous snapshot poll (architectural: sync reset) ----
  typedef enum logic [1:0] {SEQ_CFG, SEQ_START, SEQ_POLL_LATCH, SEQ_POLL_READ} seq_state_e;
  seq_state_e seq_q;

  logic [7:0] checksum_byte_q, cycles_byte_q;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      seq_q           <= SEQ_CFG;
      csr_address_q   <= '0;
      csr_writedata_q <= '0;
      csr_write_q     <= 1'b0;
      csr_read_q      <= 1'b0;
      checksum_byte_q <= '0;
      cycles_byte_q   <= '0;
    end else begin
      csr_write_q <= 1'b0;
      csr_read_q  <= 1'b0;
      unique case (seq_q)
        SEQ_CFG: begin
          csr_write_q     <= 1'b1;
          csr_address_q   <= L0_ADDR_N_VECTORS;
          csr_writedata_q <= 32'(N_VECTORS);
          seq_q           <= SEQ_START;
        end
        SEQ_START: begin
          csr_write_q     <= 1'b1;
          csr_address_q   <= L0_ADDR_CTRL;
          csr_writedata_q <= 32'(1 << CTRL_START);
          seq_q           <= SEQ_POLL_LATCH;
        end
        SEQ_POLL_LATCH: begin
          // csr_address_q currently holds CHECKSUM (set by the previous SEQ_POLL_READ state, or by
          // SEQ_START on the very first pass through — harmless, just one throwaway capture cycle
          // before this settles into steady state): this cycle's csr_readdata reflects THAT read.
          checksum_byte_q <= csr_readdata[7:0];
          // reading CYCLES_LO latches the atomic snapshot (register_map.md convention); becomes
          // visible on csr_readdata next cycle (SEQ_POLL_READ).
          csr_read_q    <= 1'b1;
          csr_address_q <= L0_ADDR_CYCLES_LO;
          seq_q         <= SEQ_POLL_READ;
        end
        SEQ_POLL_READ: begin
          // csr_address_q currently holds CYCLES_LO (set above, last edge): this cycle's
          // csr_readdata reflects that read.
          cycles_byte_q <= csr_readdata[7:0];
          csr_read_q      <= 1'b1;
          csr_address_q   <= L0_ADDR_CHECKSUM;
          seq_q           <= SEQ_POLL_LATCH;
        end
        default: seq_q <= SEQ_CFG;
      endcase
    end
  end

  assign checksum_byte = checksum_byte_q;
  assign cycles_byte   = cycles_byte_q;

endmodule
`default_nettype wire
