// avalon_mem_bfm — 16-bit Avalon-MM read slave backed by a hex-loaded memory (issue #16 sim).
//
// Stands in for the HyperBus controller in the standalone replay TB: accepts a burst read command,
// waits READ_LATENCY cycles, then streams `burstcount` words (with optional GAP cycles between words
// to model a slow feed). waitrequest is low only while idle. Loaded from records.hex ($readmemh).
`timescale 1ns/1ps
module avalon_mem_bfm #(
    parameter int MEM_WORDS    = 2048,
    parameter int READ_LATENCY = 4,
    parameter int GAP          = 0,
    parameter     HEXFILE      = "sim/replay/fixtures/records.hex"
) (
    input  logic        clk,
    input  logic        rst,
    input  logic [22:0] av_address,
    input  logic [7:0]  av_burstcount,
    input  logic        av_read,
    output logic [15:0] av_readdata,
    output logic        av_readdatavalid,
    output logic        av_waitrequest
);
  logic [15:0] mem [MEM_WORDS];
  initial $readmemh(HEXFILE, mem);

  typedef enum logic [1:0] {IDLE, LAT, STREAM} state_t;
  state_t st;
  logic [22:0] addr;
  logic [7:0]  cnt;
  logic [15:0] lat_cnt, gap_cnt;

  assign av_waitrequest = (st != IDLE);

  always_ff @(posedge clk) begin
    if (rst) begin
      st <= IDLE; addr <= '0; cnt <= '0; lat_cnt <= '0; gap_cnt <= '0;
      av_readdatavalid <= 1'b0; av_readdata <= '0;
    end else begin
      av_readdatavalid <= 1'b0;
      unique case (st)
        IDLE: if (av_read) begin
          addr <= av_address; cnt <= av_burstcount; lat_cnt <= 16'(READ_LATENCY); st <= LAT;
        end
        LAT: if (lat_cnt == 0) st <= STREAM; else lat_cnt <= lat_cnt - 1'b1;
        STREAM: begin
          if (gap_cnt != 0) begin
            gap_cnt <= gap_cnt - 1'b1;
          end else begin
            av_readdatavalid <= 1'b1;
            av_readdata <= mem[addr[$clog2(MEM_WORDS)-1:0]];
            addr <= addr + 1'b1;
            cnt  <= cnt - 1'b1;
            gap_cnt <= 16'(GAP);
            if (cnt == 8'd1) st <= IDLE;
          end
        end
        default: st <= IDLE;
      endcase
    end
  end
endmodule
