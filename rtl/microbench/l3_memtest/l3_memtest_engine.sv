// l3_memtest_engine — LFSR + address-in-data HyperRAM memtest (issue #14, PLAN §7 L3 step 2).
//
// Avalon-MM master onto hbmc_core (rtl/hyperbus/hbmc_core.sv): on START it runs PASS_TARGET
// write-then-read-verify passes over [BASE_ADDR, BASE_ADDR+SPAN_WORDS), regenerating the identical
// LFSR sequence (seeded from SEED) for both the write pass and every read-verify pass so a mismatch
// can only mean a HyperRAM bit/address fault, never a sequence-generation bug. Accumulates a total
// ERR_COUNT across all passes and latches the word address of the first mismatch for debugging.
// Issue #14 acceptance: "Zero-error memtest at operating point (>= 100 passes)" — set PASS_TARGET
// >= 100 and read ERR_COUNT/PASS_DONE once DONE.
//
// hbmc_core's av_burstcount is 8 bits (issue #13), so any SPAN_WORDS above 255 is transparently
// chunked into back-to-back sub-bursts (l3_memtest_pkg::MAX_SUBBURST_WORDS) — invisible to the
// LFSR/address stream, which runs continuously across chunk boundaries.
`ifndef L3_MEMTEST_ENGINE_SV
`define L3_MEMTEST_ENGINE_SV
module l3_memtest_engine
  import l3_memtest_pkg::*;
#(
    parameter int MAX_SUBBURST = MAX_SUBBURST_WORDS  // override only for small-memory testbenches
) (
    input  logic clk,
    input  logic rst,

    input  logic [4:0]  csr_address,
    input  logic        csr_read,
    output logic [31:0] csr_readdata,
    input  logic        csr_write,
    input  logic [31:0] csr_writedata,

    output logic [22:0] av_address,
    output logic [7:0]  av_burstcount,
    output logic        av_read,
    output logic        av_write,
    output logic [15:0] av_writedata,
    input  logic [15:0] av_readdata,
    input  logic        av_readdatavalid,
    input  logic        av_waitrequest
);
  localparam logic [15:0] DEFAULT_SEED = 16'hACE1;

  typedef enum logic [2:0] {IDLE, WCMD, WBODY, RCMD, RBODY, PASS_DONE_ST, DONE} state_t;
  state_t st;

  logic [15:0] seed, lfsr;
  logic [22:0] base_addr, cur_addr;
  logic [31:0] span_words, remaining;
  logic [31:0] pass_target, pass_done;
  logic [31:0] err_count;
  logic [22:0] first_err_addr;
  logic        first_err_seen;
  logic [7:0]  subrem;
  logic [15:0] rd_expect;

  wire busy = (st != IDLE) && (st != DONE);
  wire [15:0] seed_eff = (seed == 16'd0) ? DEFAULT_SEED : seed;
  wire [7:0]  this_sub = (remaining > 32'(MAX_SUBBURST)) ? 8'(MAX_SUBBURST) : remaining[7:0];

  always_comb begin
    csr_readdata = 32'd0;
    if (csr_read) begin
      unique case (csr_address)
        MT_STATUS:       csr_readdata = {30'd0, (st == DONE), busy};
        MT_PASS_DONE:    csr_readdata = pass_done;
        MT_ERR_COUNT:    csr_readdata = err_count;
        MT_ERR_ADDR:     csr_readdata = {9'd0, first_err_addr};
        MT_SEED:         csr_readdata = {16'd0, seed};
        MT_BASE_ADDR:    csr_readdata = {9'd0, base_addr};
        MT_SPAN_WORDS:   csr_readdata = span_words;
        MT_PASS_TARGET:  csr_readdata = pass_target;
        default:         csr_readdata = 32'hDEAD_C0DE;
      endcase
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      st <= IDLE;
      seed <= '0; base_addr <= '0; span_words <= '0; pass_target <= 32'd1;
      pass_done <= '0; err_count <= '0; first_err_addr <= '0; first_err_seen <= 1'b0;
      av_address <= '0; av_burstcount <= '0; av_read <= 1'b0; av_write <= 1'b0; av_writedata <= '0;
      lfsr <= DEFAULT_SEED; cur_addr <= '0; remaining <= '0; subrem <= '0; rd_expect <= '0;
    end else begin
      if (csr_write && (st == IDLE || st == DONE)) begin
        unique case (csr_address)
          MT_SEED:        seed        <= csr_writedata[15:0];
          MT_BASE_ADDR:   base_addr   <= csr_writedata[22:0];
          MT_SPAN_WORDS:  span_words  <= csr_writedata;
          MT_PASS_TARGET: pass_target <= csr_writedata;
          default: ;
        endcase
      end
      if (csr_write && csr_address == MT_CTRL && csr_writedata[0] &&
          (st == IDLE || st == DONE)) begin
        pass_done <= '0; err_count <= '0; first_err_seen <= 1'b0; first_err_addr <= '0;
        lfsr <= seed_eff;
        cur_addr <= base_addr; remaining <= span_words;
        st <= (span_words == 32'd0) ? DONE : WCMD;
      end

      unique case (st)
        IDLE, DONE: ;

        WCMD: begin
          av_write <= 1'b1; av_address <= cur_addr; av_burstcount <= this_sub;
          av_writedata <= memtest_expected(lfsr, cur_addr);
          subrem <= this_sub;
          st <= WBODY;
        end

        WBODY: begin
          if (!av_waitrequest) begin
            lfsr      <= lfsr16_next(lfsr);
            cur_addr  <= cur_addr + 23'd1;
            remaining <= remaining - 32'd1;
            subrem    <= subrem - 8'd1;
            if (subrem == 8'd1) begin
              av_write <= 1'b0;
              if (remaining == 32'd1) begin
                lfsr <= seed_eff; cur_addr <= base_addr; remaining <= span_words;
                st <= RCMD;
              end else st <= WCMD;
            end else begin
              av_writedata <= memtest_expected(lfsr16_next(lfsr), cur_addr + 23'd1);
            end
          end
        end

        RCMD: begin
          av_read <= 1'b1; av_address <= cur_addr; av_burstcount <= this_sub;
          subrem <= this_sub; rd_expect <= memtest_expected(lfsr, cur_addr);
          st <= RBODY;
        end

        RBODY: begin
          if (av_read && !av_waitrequest) av_read <= 1'b0;  // command accepted, deassert
          if (av_readdatavalid) begin
            if (av_readdata != rd_expect) begin
              err_count <= err_count + 32'd1;
              if (!first_err_seen) begin first_err_seen <= 1'b1; first_err_addr <= cur_addr; end
            end
            lfsr      <= lfsr16_next(lfsr);
            cur_addr  <= cur_addr + 23'd1;
            remaining <= remaining - 32'd1;
            subrem    <= subrem - 8'd1;
            rd_expect <= memtest_expected(lfsr16_next(lfsr), cur_addr + 23'd1);
            if (remaining == 32'd1) st <= PASS_DONE_ST;
            else if (subrem == 8'd1) st <= RCMD;
          end
        end

        PASS_DONE_ST: begin
          pass_done <= pass_done + 32'd1;
          if (pass_done + 32'd1 == pass_target) begin
            st <= DONE;
          end else begin
            lfsr <= seed_eff; cur_addr <= base_addr; remaining <= span_words;
            st <= WCMD;
          end
        end

        default: st <= IDLE;
      endcase
    end
  end
endmodule
`endif
