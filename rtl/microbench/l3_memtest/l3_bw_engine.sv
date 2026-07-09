// l3_bw_engine — linear-burst HyperRAM bandwidth engine (issue #14, PLAN §7 L3 step 4/5).
//
// Avalon-MM master onto hbmc_core (rtl/hyperbus/hbmc_core.sv). On START it streams BURST_COUNT
// back-to-back logical bursts of BURST_WORDS words each (one direction per run: write or read,
// DIR bit) from/to one continuously-incrementing linear address starting at BASE_ADDR, and counts
// elapsed cycles from the first command issued to the last word retired. The host derives
// sustained MB/s = (BURST_WORDS*BURST_COUNT*2 bytes) / (CYCLES / f_clk), and efficiency =
// sustained / (2 x f_HB) per PLAN §4 — never quote the 2xf_HB peak as a sustained number
// (AGENTS.md "do not" list).
//
// hbmc_core's av_burstcount field is 8 bits (issue #13): a single HyperBus command can move at most
// MAX_SUBBURST_WORDS=255 words (510 B). For BURST_WORDS <= 255 (64 B/256 B in the issue's sweep)
// each logical burst maps 1:1 onto one hbmc_core command, so the sweep genuinely characterizes CA
// overhead vs payload at that burst size. For BURST_WORDS > 255 (1 KB/4 KB) a logical burst is
// necessarily split into multiple back-to-back hbmc_core sub-bursts (each paying its own 6-beat CA +
// latency), which is a real controller limitation, not a modeling shortcut — flag it when comparing
// the 1 KB/4 KB efficiency numbers against 64 B/256 B (PLAN §7 L3 step 5 "investigate controller
// dead cycles" applies extra to these two points). Sub-bursts within and across logical-burst
// boundaries are issued with zero added idle cycles (the very next command is asserted the cycle
// after the previous completes), i.e. genuinely "back-to-back".
`ifndef L3_BW_ENGINE_SV
`define L3_BW_ENGINE_SV
module l3_bw_engine
  import l3_memtest_pkg::*;
#(
    parameter int MAX_SUBBURST = MAX_SUBBURST_WORDS
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
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic [15:0] av_readdata,   // bandwidth engine only times arrival; content is not checked
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic        av_readdatavalid,
    input  logic        av_waitrequest
);
  typedef enum logic [1:0] {IDLE, CMD, BODY, DONE} state_t;
  state_t st;

  logic        dir_read;
  logic [22:0] base_addr, cur_addr;
  logic [31:0] burst_words, burst_count;
  logic [31:0] words_left;      // words remaining in the WHOLE run
  logic [31:0] logical_left;    // words remaining in the current logical burst (BURSTS_DONE bookkeeping)
  logic [7:0]  subrem;
  logic [31:0] bursts_done;
  logic [63:0] elapsed;
  logic [15:0] wr_ctr;          // free-running fill pattern for write-direction traffic (content ignored)

  wire busy = (st != IDLE) && (st != DONE);
  wire [31:0] cap_by_logical = (logical_left < words_left) ? logical_left : words_left;
  wire [7:0]  this_sub = (cap_by_logical > 32'(MAX_SUBBURST)) ? 8'(MAX_SUBBURST) : cap_by_logical[7:0];

  always_comb begin
    csr_readdata = 32'd0;
    if (csr_read) begin
      unique case (csr_address)
        BW_STATUS:      csr_readdata = {30'd0, (st == DONE), busy};
        BW_CYCLES_LO:   csr_readdata = elapsed[31:0];
        BW_CYCLES_HI:   csr_readdata = elapsed[63:32];
        BW_BURSTS_DONE: csr_readdata = bursts_done;
        BW_BASE_ADDR:   csr_readdata = {9'd0, base_addr};
        BW_BURST_WORDS: csr_readdata = burst_words;
        BW_BURST_COUNT: csr_readdata = burst_count;
        default:        csr_readdata = 32'hDEAD_C0DE;
      endcase
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      st <= IDLE; dir_read <= 1'b0;
      base_addr <= '0; burst_words <= 32'd1; burst_count <= 32'd1;
      words_left <= '0; logical_left <= '0; subrem <= '0;
      bursts_done <= '0; elapsed <= '0; wr_ctr <= '0; cur_addr <= '0;
      av_address <= '0; av_burstcount <= '0; av_read <= 1'b0; av_write <= 1'b0; av_writedata <= '0;
    end else begin
      elapsed <= busy ? (elapsed + 64'd1) : elapsed;

      if (csr_write && (st == IDLE || st == DONE)) begin
        unique case (csr_address)
          BW_BASE_ADDR:   base_addr   <= csr_writedata[22:0];
          BW_BURST_WORDS: burst_words <= csr_writedata;
          BW_BURST_COUNT: burst_count <= csr_writedata;
          default: ;
        endcase
      end
      if (csr_write && csr_address == BW_CTRL && csr_writedata[0] &&
          (st == IDLE || st == DONE)) begin
        dir_read     <= csr_writedata[1];
        cur_addr     <= base_addr;
        words_left   <= burst_words * burst_count;
        logical_left <= burst_words;
        bursts_done  <= '0; elapsed <= '0; wr_ctr <= '0;
        st <= (burst_words == 32'd0 || burst_count == 32'd0) ? DONE : CMD;
      end

      unique case (st)
        IDLE, DONE: ;

        CMD: begin
          subrem <= this_sub;
          av_address <= cur_addr; av_burstcount <= this_sub;
          if (dir_read) begin
            av_read <= 1'b1;
          end else begin
            av_write <= 1'b1; av_writedata <= wr_ctr;
          end
          st <= BODY;
        end

        BODY: begin
          if (dir_read) begin
            if (av_read && !av_waitrequest) av_read <= 1'b0;
            if (av_readdatavalid) begin
              cur_addr     <= cur_addr + 23'd1;
              words_left   <= words_left - 32'd1;
              subrem       <= subrem - 8'd1;
              logical_left <= (logical_left == 32'd1) ? burst_words : (logical_left - 32'd1);
              if (logical_left == 32'd1) bursts_done <= bursts_done + 32'd1;
              if (words_left == 32'd1) st <= DONE;
              else if (subrem == 8'd1) st <= CMD;
            end
          end else begin
            if (!av_waitrequest) begin
              wr_ctr       <= wr_ctr + 16'd1;
              cur_addr     <= cur_addr + 23'd1;
              words_left   <= words_left - 32'd1;
              subrem       <= subrem - 8'd1;
              logical_left <= (logical_left == 32'd1) ? burst_words : (logical_left - 32'd1);
              if (logical_left == 32'd1) bursts_done <= bursts_done + 32'd1;
              if (subrem == 8'd1) begin
                av_write <= 1'b0;
                st <= (words_left == 32'd1) ? DONE : CMD;
              end else begin
                av_writedata <= wr_ctr + 16'd1;
              end
            end
          end
        end

        default: st <= IDLE;
      endcase
    end
  end
endmodule
`endif
