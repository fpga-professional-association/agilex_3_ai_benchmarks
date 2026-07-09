// l1_pe_top — L1 PE-array microbench top (issue #11, PLAN §7 L1 + §3 LV1/LV4).
//
// Wraps `l1_pe_core` (the hot-domain PE array + run FSM + checksum) with an Avalon-MM CSR slave and
// selects the LV4 clock-domain variable:
//   - MERGED   (ISOLATE=0): CSR slave and core on ONE clock. `clk_hot` is tied to `clk` by the
//                compile harness / SDC; the CSR<->core interface is direct wires. This is the
//                "CSR decode and array on one clock" point of PLAN §3 LV4.
//   - ISOLATED (ISOLATE=1): core on the hot `clk_hot`, CSR on the cool `clk`, with the sanctioned
//                rtl/common CDC wrappers at the seam (pulse_sync for START, async_fifo for the
//                result snapshot, cdc_bit_sync for status) — "async FIFOs at seams" (PLAN §3 LV4).
//                The fitter is then free to place/route the hot domain without the CSR logic pulling
//                on its clock; the measured hot-clock fmax is the LV4 payoff.
// Only the seam moves between the two builds — the core, its RTL and its checksum are identical, so
// the testbench's MERGED==ISOLATED checksum equality is what makes the fmax delta attributable to
// the domain split alone (issue "do not": only the domain variable moves).
//
// Register map (Avalon-MM, byte offsets, 32-bit; bench_pkg::L1_ADDR_*):
//   0x00 CTRL      RW  bit0 START (self-clearing)
//   0x04 N_VECTORS RW  vectors to retire before stopping
//   0x08 CYCLES_LO RO  low 32 bits of the completed run's cycle span
//   0x0C CYCLES_HI RO  high 32 bits
//   0x10 DONE      RO  vectors retired
//   0x14 CHECKSUM  RO  XOR checksum of retired results
//   0x18 STATUS    RO  bit0 RUNNING, bit1 DONE
//   0x1C DIMS      RO  {16'NUM_COLS, 16'NUM_ROWS} (host cross-check of the loaded .sof)
`ifndef L1_PE_TOP_SV
`define L1_PE_TOP_SV
module l1_pe_top
  import bench_pkg::*;
#(
    parameter int NUM_ROWS    = 4,
    parameter int NUM_COLS    = 4,
    parameter int N_TAPS      = 10,
    parameter bit RESET_HEAVY = 1'b0,
    parameter bit ISOLATE     = 1'b0
) (
    input  logic         clk,       // cool/CSR clock (== clk_hot when MERGED)
    input  logic         clk_hot,   // hot/array clock
    input  logic         rst_n,

    input  logic [7:0]   csr_address,
    input  logic         csr_read,
    output logic [31:0]  csr_readdata,
    input  logic         csr_write,
    input  logic [31:0]  csr_writedata,
    output logic         csr_waitrequest
);
  assign csr_waitrequest = 1'b0;

  wire rst_csr = ~rst_n;   // synchronous reset in the CSR (clk) domain
  wire rst_hot = ~rst_n;   // synchronous reset in the hot (clk_hot) domain

  // ---------------- CSR domain (clk) ----------------
  logic [31:0] n_vectors_q;
  logic        start_pulse_csr;
  wire ctrl_write = csr_write && (csr_address == L1_ADDR_CTRL);
  always_ff @(posedge clk) begin
    if (rst_csr) begin
      n_vectors_q     <= '0;
      start_pulse_csr <= 1'b0;
    end else begin
      if (csr_write && csr_address == L1_ADDR_N_VECTORS) n_vectors_q <= csr_writedata;
      start_pulse_csr <= ctrl_write && csr_writedata[CTRL_START];
    end
  end

  // ---------------- core (clk_hot) ----------------
  logic        core_start;
  logic        core_running, core_done, core_done_stb;
  logic [63:0] core_res_cycle;
  logic [31:0] core_res_vector, core_res_checksum;

  l1_pe_core #(
      .NUM_ROWS(NUM_ROWS), .NUM_COLS(NUM_COLS), .N_TAPS(N_TAPS), .RESET_HEAVY(RESET_HEAVY)
  ) u_core (
      .clk(clk_hot), .rst(rst_hot),
      .start(core_start), .n_vectors(n_vectors_q),
      .running(core_running), .done(core_done), .done_stb(core_done_stb),
      .res_cycle(core_res_cycle), .res_vector(core_res_vector), .res_checksum(core_res_checksum)
  );

  // ---------------- seam: CSR-domain views of the core (cyc/vec/chk/run/dn) ----------------
  logic [63:0] cyc_csr;
  logic [31:0] vec_csr, chk_csr;
  logic        run_csr, dn_csr;

  generate
    if (ISOLATE) begin : g_isolated
      // START: cool -> hot pulse
      pulse_sync u_start_sync (
          .src_clk(clk), .src_rst(rst_csr), .src_pulse(start_pulse_csr),
          .dst_clk(clk_hot), .dst_pulse(core_start)
      );
      // n_vectors is quiesced before START (written first, then CTRL.START), so the core samples the
      // stable value at the synchronized start pulse — MCP-style crossing, no per-bit sync needed.

      // RESULT snapshot: hot -> cool via the sanctioned async FIFO, pushed once at done_stb
      localparam int RW = 128;
      logic [RW-1:0] fifo_rd_data;
      logic          fifo_empty, fifo_full;
      async_fifo #(.WIDTH(RW), .ADDR_W(2)) u_res_fifo (
          .wr_clk(clk_hot), .wr_rst(rst_hot), .wr_en(core_done_stb),
          .wr_data({core_res_cycle, core_res_vector, core_res_checksum}), .full(fifo_full),
          .rd_clk(clk), .rd_rst(rst_csr), .rd_en(!fifo_empty),
          .rd_data(fifo_rd_data), .empty(fifo_empty)
      );
      // fifo_full is never expected to assert (one write per run, depth 4); tie off intentionally.
      wire _unused_full = &{1'b0, fifo_full};
      always_ff @(posedge clk) begin
        if (rst_csr) begin
          cyc_csr <= '0; vec_csr <= '0; chk_csr <= '0;
        end else if (!fifo_empty) begin
          cyc_csr <= fifo_rd_data[127:64];
          vec_csr <= fifo_rd_data[63:32];
          chk_csr <= fifo_rd_data[31:0];
        end
      end
      // STATUS: hot levels -> cool
      cdc_bit_sync u_run_sync (.dst_clk(clk), .d(core_running), .q(run_csr));
      cdc_bit_sync u_dn_sync  (.dst_clk(clk), .d(core_done),    .q(dn_csr));
    end else begin : g_merged
      // Single clock (clk_hot tied to clk by the harness/SDC): direct connection.
      assign core_start = start_pulse_csr;
      assign cyc_csr    = core_res_cycle;
      assign vec_csr    = core_res_vector;
      assign chk_csr    = core_res_checksum;
      assign run_csr    = core_running;
      assign dn_csr     = core_done;
      // done_stb only drives the ISOLATED result FIFO; unused on a single clock.
      wire _unused_stb = &{1'b0, core_done_stb};
    end
  endgenerate

  // csr_read is unused: this 0-wait slave drives csr_readdata combinationally from csr_address.
  wire _unused_rd = &{1'b0, csr_read};

  // ---------------- CSR reads (clk domain) ----------------
  always_comb begin
    unique case (csr_address)
      L1_ADDR_CTRL:      csr_readdata = '0;
      L1_ADDR_N_VECTORS: csr_readdata = n_vectors_q;
      L1_ADDR_CYCLES_LO: csr_readdata = cyc_csr[31:0];
      L1_ADDR_CYCLES_HI: csr_readdata = cyc_csr[63:32];
      L1_ADDR_DONE:      csr_readdata = vec_csr;
      L1_ADDR_CHECKSUM:  csr_readdata = chk_csr;
      L1_ADDR_STATUS:    csr_readdata = {30'd0, dn_csr, run_csr};
      L1_ADDR_DIMS:      csr_readdata = {16'(NUM_COLS), 16'(NUM_ROWS)};
      default:           csr_readdata = 32'hDEAD_C0DE;
    endcase
  end

endmodule
`endif
