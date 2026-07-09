// l1_sweep_top — compile-only harness for the L1 fmax sweep (issue #11). NOT a board bring-up top
// (that would need real AXC3000 pinout, #7/#8). Its only jobs are (a) to keep the whole l1_pe_top
// datapath live so the Fitter/Timing-Analyzer report a real fmax for it (a power-on CSR sequencer
// starts a run; the CSR readback is registered out to a pin so nothing is pruned), and (b) to
// expose the NUM_ROWS/NUM_COLS/RESET_HEAVY/ISOLATE knobs as parameters the per-revision .qsf sets.
//
// Clocking: `clk` is the cool/CSR clock; `clk_hot` is the hot/array clock. ISOLATE is a compile
// parameter, so `ISOLATE ? clk_hot : clk` collapses at elaboration to a constant clock choice — no
// runtime clock mux. MERGED builds tie the array to `clk` (and leave clk_hot unused / unconstrained,
// fine for a fmax-only compile that emits no .sof); ISOLATED builds put the array on `clk_hot` with
// the two clocks constrained asynchronous in l1_sweep_isolated.sdc.
`ifndef L1_SWEEP_TOP_SV
`define L1_SWEEP_TOP_SV
module l1_sweep_top
  import bench_pkg::*;
#(
    parameter int NUM_ROWS    = 4,
    parameter int NUM_COLS    = 4,
    parameter bit RESET_HEAVY = 1'b0,
    parameter bit ISOLATE     = 1'b0,
    parameter int RUN_VECTORS = 64
) (
    input  logic        clk,
    input  logic        clk_hot,
    input  logic        rst_n,
    output logic [7:0]  obs        // registered observable — keeps the design from being optimised away
);
  wire dut_clk_hot = ISOLATE ? clk_hot : clk;

  // ---- power-on CSR sequencer (cool/clk domain): write N_VECTORS, pulse START, then poll ----
  logic [7:0]   seq_cnt;
  logic [7:0]   csr_addr;
  logic         csr_rd, csr_wr;
  logic [31:0]  csr_wdata, csr_rdata;
  logic         csr_wait;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      seq_cnt   <= '0;
      csr_addr  <= '0;
      csr_rd    <= 1'b0;
      csr_wr    <= 1'b0;
      csr_wdata <= '0;
    end else begin
      csr_wr <= 1'b0;
      csr_rd <= 1'b0;
      if (seq_cnt != 8'hFF) seq_cnt <= seq_cnt + 8'd1;
      case (seq_cnt)
        8'd2: begin csr_addr <= L1_ADDR_N_VECTORS; csr_wdata <= 32'(RUN_VECTORS); csr_wr <= 1'b1; end
        8'd4: begin csr_addr <= L1_ADDR_CTRL;      csr_wdata <= 32'h1;            csr_wr <= 1'b1; end
        default: begin csr_addr <= L1_ADDR_CHECKSUM; csr_rd <= 1'b1; end   // keep reading -> stays live
      endcase
    end
  end

  l1_pe_top #(
      .NUM_ROWS(NUM_ROWS), .NUM_COLS(NUM_COLS), .RESET_HEAVY(RESET_HEAVY), .ISOLATE(ISOLATE)
  ) dut (
      .clk(clk), .clk_hot(dut_clk_hot), .rst_n(rst_n),
      .csr_address(csr_addr), .csr_read(csr_rd), .csr_readdata(csr_rdata),
      .csr_write(csr_wr), .csr_writedata(csr_wdata), .csr_waitrequest(csr_wait)
  );

  // register a reduction of the readback to the observable pin (prevents pruning)
  always_ff @(posedge clk) begin
    if (!rst_n) obs <= '0;
    else        obs <= csr_rdata[7:0] ^ csr_rdata[15:8] ^ csr_rdata[23:16] ^ csr_rdata[31:24]
                       ^ {7'd0, csr_wait};
  end

endmodule
`endif
