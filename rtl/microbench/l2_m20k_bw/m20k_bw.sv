// m20k_bw — L2 aggregate M20K bandwidth microbench (issue #12, PLAN §7 L2 + §3 LV3).
//
// NUM_BANKS independent M20K banks (m20k_bw_bank), one reader per bank, each reader XOR-folding K
// back-to-back reads into its own checksum sink. GEOMETRY selects how the NUM_BANKS read ports are
// driven:
//
//   GEOM_BANKED (config a/c): every bank's read port fires every cycle in parallel — RUN lasts K
//     cycles, each bank retires K reads. This is PLAN §3 LV3's "good" geometry: one port per
//     reader, banked.
//   GEOM_SHARED (config b): a free-running round-robin counter drives exactly ONE bank's read port
//     per cycle — RUN lasts K*NUM_BANKS cycles so every bank still retires exactly K reads (same
//     total bytes moved as the banked case), but serialized through what is effectively one shared
//     port turn-taking across the banks. This is the "shared single port, round-robin" anti-pattern
//     the issue calls out, quantified directly by the resulting elapsed-cycle blowup.
//
// Same total bytes (NUM_BANKS * K * DATA_WIDTH/8) move in every config; only the elapsed cycle
// count differs, which is exactly what makes GB/s comparable apples-to-apples across configs and
// against the banks*bytes/port/cycle*fclk theoretical bound (PLAN §3 LV3, issue step 4/5).
//
// CSR map: m20k_bw_pkg::L2_ADDR_*. Same CTRL/STATUS bit + "atomic snapshot frozen on read" +
// indexed-readback conventions as bench_pkg/l3_memtest_pkg (AGENTS.md Avalon-MM CSR naming).
`ifndef M20K_BW_SV
`define M20K_BW_SV
module m20k_bw
  import m20k_bw_pkg::*;
#(
    parameter int NUM_BANKS  = 32,
    parameter int DATA_WIDTH = 32,     // must be <= 32 (csr_readdata is one 32-bit word)
    parameter int ADDR_WIDTH = 9,      // per-bank depth = 2**ADDR_WIDTH
    parameter bit GEOMETRY   = GEOM_BANKED,
    parameter bit OUTPUT_REG = 1'b1
) (
    input  logic        clk,
    input  logic        rst,

    input  logic [7:0]  csr_address,
    input  logic        csr_read,
    output logic [31:0] csr_readdata,
    input  logic        csr_write,
    input  logic [31:0] csr_writedata
);
  localparam int BANK_IDX_W = (NUM_BANKS <= 1) ? 1 : $clog2(NUM_BANKS);
  // DRAIN_CYCLES: cycles to wait, after the RUN cycle that issues the last rd_en pulse (cycle T),
  // before every bank's checksum_q register has visibly latched that read's contribution, so the
  // top can safely sample checksum[] into agg_checksum_q. m20k_bw_bank's g_noreg path updates
  // checksum_q at edge T->T+1 (visible from cycle T+1: latency 1); g_outreg delays both rd_data and
  // the enable by one more register, so its checksum_q update lands at edge (T+1)->(T+2) (visible
  // from cycle T+2: latency 2). The top's own capturing register must sample checksum[] "as of" a
  // cycle >= T+latency, i.e. must spend >= latency cycles in DRAIN before capturing — hence
  // DRAIN_CYCLES = 1 + OUTPUT_REG, not the OUTPUT_REG-only delta (a same-edge DONE transition with
  // no DRAIN cycle would race the bank's own register update and silently drop the last read from
  // the aggregate — caught by cross-checking scripts/l2_golden.py's cycle count, not by Verilator).
  localparam int DRAIN_CYCLES = 1 + int'(OUTPUT_REG);
  localparam int DRAIN_CNT_W  = (DRAIN_CYCLES <= 1) ? 1 : $clog2(DRAIN_CYCLES);

  typedef enum logic [1:0] {IDLE, RUN, DRAIN, DONE} state_t;
  state_t st;

  logic [31:0] k_reg;                  // reads per reader, configured pre-run
  logic [31:0] total_pulses;           // K (banked) or K*NUM_BANKS (shared)
  logic [31:0] pulses_issued;
  logic [DRAIN_CNT_W-1:0] drain_cnt;
  logic [BANK_IDX_W-1:0] rr;           // round-robin bank select (SHARED geometry only)
  logic [63:0] elapsed;
  logic [BANK_IDX_W-1:0] cs_addr;

  wire busy  = (st == RUN) || (st == DRAIN);
  wire start = csr_write && (csr_address == L2_ADDR_CTRL) && csr_writedata[0] &&
               (st == IDLE || st == DONE);

  assign total_pulses = GEOMETRY ? (k_reg * 32'(NUM_BANKS)) : k_reg;

  logic [NUM_BANKS-1:0] rd_en;
  logic [DATA_WIDTH-1:0] checksum [0:NUM_BANKS-1];
  logic [DATA_WIDTH-1:0] agg_checksum_q;

  genvar gb;
  generate
    for (gb = 0; gb < NUM_BANKS; gb++) begin : g_bank
      m20k_bw_bank #(
          .DATA_WIDTH(DATA_WIDTH), .ADDR_WIDTH(ADDR_WIDTH), .BANK_ID(gb), .OUTPUT_REG(OUTPUT_REG)
      ) u_bank (
          .clk(clk), .rst(rst), .start(start), .rd_en(rd_en[gb]), .checksum_q(checksum[gb])
      );
      assign rd_en[gb] = (st == RUN) && (GEOMETRY ? (rr == BANK_IDX_W'(gb)) : 1'b1);
    end
  endgenerate

  // ---- aggregate checksum: XOR of every bank's checksum, latched once DONE (matches CYCLES_LO's
  // "frozen once DONE" convention so a host read gets a coherent snapshot, not a value racing the
  // per-bank accumulators as they settle during DRAIN). ----
  logic [DATA_WIDTH-1:0] agg_comb;
  always_comb begin
    agg_comb = '0;
    for (int i = 0; i < NUM_BANKS; i++) agg_comb ^= checksum[i];
  end

  always_comb begin
    csr_readdata = 32'd0;
    if (csr_read) begin
      unique case (csr_address)
        L2_ADDR_K:         csr_readdata = k_reg;
        L2_ADDR_CYCLES_LO: csr_readdata = elapsed[31:0];
        L2_ADDR_CYCLES_HI: csr_readdata = elapsed[63:32];
        L2_ADDR_STATUS:    csr_readdata = {30'd0, (st == DONE), busy};
        L2_ADDR_CS_DATA:   csr_readdata = 32'(checksum[cs_addr]);
        L2_ADDR_AGG_CS:    csr_readdata = 32'(agg_checksum_q);
        L2_ADDR_DIMS:      csr_readdata = {6'(ADDR_WIDTH), OUTPUT_REG, GEOMETRY,
                                            8'(DATA_WIDTH / 8), 16'(NUM_BANKS)};
        default:           csr_readdata = 32'hDEAD_C0DE;
      endcase
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      st <= IDLE; k_reg <= 32'd1; pulses_issued <= '0; drain_cnt <= '0; rr <= '0;
      elapsed <= '0; cs_addr <= '0; agg_checksum_q <= '0;
    end else begin
      elapsed <= busy ? (elapsed + 64'd1) : elapsed;

      if (csr_write && (st == IDLE || st == DONE)) begin
        unique case (csr_address)
          L2_ADDR_K: k_reg <= csr_writedata;
          default: ;
        endcase
      end
      if (csr_write && csr_address == L2_ADDR_CS_ADDR) cs_addr <= csr_writedata[BANK_IDX_W-1:0];

      if (start) begin
        pulses_issued <= '0; drain_cnt <= '0; rr <= '0; elapsed <= '0;
        st <= (k_reg == 32'd0) ? DONE : RUN;
        if (k_reg == 32'd0) agg_checksum_q <= '0;  // K=0: every bank's checksum is trivially 0
      end

      unique case (st)
        IDLE, DONE: ;

        RUN: begin
          rr <= (rr == BANK_IDX_W'(NUM_BANKS - 1)) ? '0 : (rr + 1'b1);
          if (pulses_issued + 32'd1 == total_pulses) begin
            st <= DRAIN;
            drain_cnt <= '0;
          end
          pulses_issued <= pulses_issued + 32'd1;
        end

        DRAIN: begin
          // spend exactly DRAIN_CYCLES cycles here (see the DRAIN_CYCLES comment above) before
          // capturing the now-settled per-bank checksums into agg_checksum_q.
          if (int'(drain_cnt) + 1 >= DRAIN_CYCLES) begin
            st <= DONE;
            agg_checksum_q <= agg_comb;
          end else begin
            drain_cnt <= drain_cnt + 1'b1;
          end
        end

        default: st <= IDLE;
      endcase
    end
  end

endmodule
`endif
