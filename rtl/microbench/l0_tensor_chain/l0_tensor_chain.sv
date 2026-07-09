// l0_tensor_chain — L0 tensor-mode-attempt DSP dot-product chain microbench (issue #9, PLAN §7 L0
// + §3 LV2). Parameterized N ∈ {8,16,32} cascaded l0_mac_block stages, each an N_TAPS=10-wide INT8
// dot product; stimulus from on-chip Galois LFSRs (l0_lfsr); an Avalon-MM CSR slave (register map
// below, mirrored in bench_pkg::L0_ADDR_*) starts a run, counts cycles, and accumulates a checksum
// of every retired combined result so the host + a companion Python model (sw/host/l0_golden.py)
// can cross-check bit-for-bit without a JTAG data-plane read of every sample (PLAN §8 method E).
//
// *** Read rtl/microbench/l0_tensor_chain/README.md before touching this file. *** It documents,
// with exact tool output, why this module is CLASSIC-MODE RTL (plain `+`/`*` inference), not the
// tensor-mode WYSIWYG primitive the issue set out to capture: Quartus Prime Pro 26.1 refuses to
// elaborate `inteleighteena_synth_tensor_mac` (or the plain classic MAC atom of the same family)
// for FAMILY "Agilex 3" (error 16666), and its own message catalog says why (error 24863: "The
// native AI-optimized DSP (DSP Prime block) only supports Stratix 10 NX and Agilex 5 devices").
// This is a toolchain gap, not a hardware or coding-pattern problem — see the README's
// "Investigation" section for the full trail (WYSIWYG direct instantiation, the IP-Catalog
// `tensor_agilex_edge` component, and why both are currently blocked for this device/tool version).
//
// Register map (Avalon-MM, byte offsets, all 32-bit; see bench_pkg::L0_ADDR_*):
//   0x00 CTRL       RW  bit0 START (self-clearing) — reseeds LFSRs + accumulators, arms a new run
//   0x04 N_VECTORS  RW  number of retired vectors to run before stopping
//   0x08 CYCLES_LO  RO  low 32 bits of the cycle span; reading this latches the atomic snapshot
//   0x0C CYCLES_HI  RO  high bits of the cycle span (from snapshot)
//   0x10 DONE_COUNT RO  vectors retired (from snapshot)
//   0x14 CHECKSUM   RO  XOR-accumulated checksum of every retired combined result (from snapshot)
//   0x18 STATUS     RO  bit0 RUNNING, bit1 DONE
//   0x1C N_BLOCKS   RO  compile-time N (cross-check against the host's expectation of which .sof is
//                       loaded)
`ifndef L0_TENSOR_CHAIN_SV
`define L0_TENSOR_CHAIN_SV
module l0_tensor_chain
  import bench_pkg::*;
#(
    parameter int N_BLOCKS  = 8,
    parameter int N_TAPS    = 10,          // Agilex DSP UG §3.3.4 tensor-mode dot-product width
    parameter int LFSR_W    = 8 * N_TAPS,  // one byte per tap
    parameter int FILL_MARGIN = 4          // extra safety cycles beyond the N_BLOCKS pipeline depth
) (
    input  logic         clk,
    input  logic         rst_n,

    // Avalon-MM CSR slave
    input  logic [7:0]   csr_address,
    input  logic         csr_read,
    output logic [31:0]  csr_readdata,
    input  logic         csr_write,
    input  logic [31:0]  csr_writedata,
    output logic         csr_waitrequest
);
  assign csr_waitrequest = 1'b0;   // 0-wait slave

  localparam int FILL_CYCLES = N_BLOCKS + FILL_MARGIN;

  // A fixed, arbitrary (not claimed maximal-length) nonzero Galois feedback tap mask shared by
  // every LFSR instance in this module — see l0_lfsr.sv. Determinism vs. the Python golden model
  // (sw/host/l0_golden.py:TAPS_UNIT) is what matters here, not period length. Built by repeating a
  // fixed 16-bit unit across the full LFSR width so it stays well-defined for any N_TAPS.
  localparam logic [15:0] TAPS_UNIT = 16'hB465;
  localparam logic [LFSR_W-1:0] TAPS = LFSR_W'({(LFSR_W / 16 + 1) {TAPS_UNIT}});

  // Deterministic, distinct, non-zero per-role seed (role 0 = shared data stream; role
  // 1..N_BLOCKS = each block's own weight stream). Kept as a function (not a $urandom call) so the
  // Python golden model can reproduce it exactly — see sw/host/l0_golden.py:seed_for_role().
  function automatic logic [LFSR_W-1:0] seed_for_role(input int role);
    logic [LFSR_W-1:0] s;
    for (int k = 0; k < N_TAPS; k++) begin
      s[8*k +: 8] = 8'(role * 7 + 1 + k * 3);
    end
    return s;
  endfunction

  // ---------------- run-control FSM (architectural state: sync reset per LV1) ----------------
  logic        running_q, done_q;
  logic [63:0] cycle_q;
  logic [31:0] vector_q;
  logic [31:0] checksum_q;
  logic [31:0] n_vectors_q;

  wire ctrl_write = csr_write && (csr_address == L0_ADDR_CTRL);
  wire start_req  = ctrl_write && csr_writedata[CTRL_START];
  wire clear      = start_req;   // reseeds the LFSRs + MAC accumulators (README: same role as the
                                 // real tensor primitive's own clr0/clr1)

  logic signed [31:0] chain_out;   // last block's cascade_out, this cycle

  wire retire = running_q && (cycle_q >= 64'(FILL_CYCLES));

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      running_q   <= 1'b0;
      done_q      <= 1'b0;
      cycle_q     <= '0;
      vector_q    <= '0;
      checksum_q  <= '0;
      n_vectors_q <= '0;
    end else begin
      if (csr_write && csr_address == L0_ADDR_N_VECTORS) n_vectors_q <= csr_writedata;

      if (start_req) begin
        running_q  <= 1'b1;
        done_q     <= 1'b0;
        cycle_q    <= '0;
        vector_q   <= '0;
        checksum_q <= '0;
      end else if (running_q) begin
        cycle_q <= cycle_q + 64'd1;
        if (retire) begin
          checksum_q <= checksum_q ^ 32'(chain_out);
          vector_q   <= vector_q + 1'b1;
          if ((vector_q + 1'b1) >= n_vectors_q) begin
            running_q <= 1'b0;
            done_q    <= 1'b1;
          end
        end
      end
    end
  end

  // ---------------- snapshot (atomic multi-word read, scoreboard.sv convention) ----------------
  logic [63:0] snap_cycle;
  logic [31:0] snap_done, snap_checksum;
  wire snap_take = csr_read && (csr_address == L0_ADDR_CYCLES_LO);
  always_ff @(posedge clk) begin
    if (snap_take) begin
      snap_cycle    <= cycle_q;
      snap_done     <= vector_q;
      snap_checksum <= checksum_q;
    end
  end

  // ---------------- CSR reads ----------------
  always_comb begin
    unique case (csr_address)
      L0_ADDR_CTRL:      csr_readdata = '0;                       // START is self-clearing
      L0_ADDR_N_VECTORS: csr_readdata = n_vectors_q;
      L0_ADDR_CYCLES_LO: csr_readdata = cycle_q[31:0];             // read here latches the snapshot
      L0_ADDR_CYCLES_HI: csr_readdata = 32'(snap_cycle >> 32);
      L0_ADDR_DONE:      csr_readdata = snap_done;
      L0_ADDR_CHECKSUM:  csr_readdata = snap_checksum;
      L0_ADDR_STATUS:    csr_readdata = {30'd0, done_q, running_q};
      L0_ADDR_N_BLOCKS:  csr_readdata = 32'(N_BLOCKS);
      default:           csr_readdata = 32'hDEAD_C0DE;
    endcase
  end

  // ---------------- stimulus: 1 shared data LFSR + N_BLOCKS independent weight LFSRs ----------------
  logic [LFSR_W-1:0] data_state;
  l0_lfsr #(.WIDTH(LFSR_W), .SEED(seed_for_role(999)), .TAPS(TAPS)) u_data_lfsr (
      .clk(clk), .clear(clear), .state(data_state)
  );

  logic signed [7:0] data_taps [N_TAPS];
  for (genvar gt = 0; gt < N_TAPS; gt++) begin : g_data_taps
    assign data_taps[gt] = data_state[8*gt +: 8];
  end

  logic signed [31:0] cascade [N_BLOCKS];   // cascade[i] = block i's registered cascade_out

  for (genvar b = 0; b < N_BLOCKS; b++) begin : g_block
    logic [LFSR_W-1:0] weight_state;
    l0_lfsr #(.WIDTH(LFSR_W), .SEED(seed_for_role(b)), .TAPS(TAPS)) u_weight_lfsr (
        .clk(clk), .clear(clear), .state(weight_state)
    );

    logic signed [7:0] weight_taps [N_TAPS];
    for (genvar gt2 = 0; gt2 < N_TAPS; gt2++) begin : g_weight_taps
      assign weight_taps[gt2] = weight_state[8*gt2 +: 8];
    end

    l0_mac_block #(.N_TAPS(N_TAPS), .HAS_CASCADE_IN(b != 0)) u_mac (
        .clk(clk), .clear(clear),
        .weight_taps(weight_taps), .data_taps(data_taps),
        .cascade_in(b == 0 ? 32'sd0 : cascade[b-1]),
        .cascade_out(cascade[b])
    );
  end

  assign chain_out = cascade[N_BLOCKS-1];

endmodule
`endif
