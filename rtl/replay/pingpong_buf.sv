// pingpong_buf — 2× record buffer between the record framer and the engine (issue #16, PLAN §6).
//
// Word-based (16-bit) throughout: HyperRAM reads are 16-bit and tensor byte counts are even, so a
// record is a whole number of words with no byte straddling. `len` and pointers are in WORDS.
//
// Normal mode (default): two BUF_WORDS buffers. The framer fills one record while the engine drains
// the previous, so the engine never starves at a record boundary. Cut-through mode (CUT_THROUGH=1,
// PLAN §6 config-b: 224²/416² records larger than M20K): a single streaming FIFO, no full-record
// buffering — framer and engine overlap word-by-word.
//
// Fill (framer): assert `fw_start` with the record word-length when `fill_ready`; stream words with
// `fw_we`/`fw_data`; pulse `fw_commit` when done. Drain (engine): `eng_valid`/`eng_data`/`eng_last`
// with `eng_ready` back-pressure.
`ifndef PINGPONG_BUF_SV
`define PINGPONG_BUF_SV
module pingpong_buf #(
    parameter int BUF_WORDS   = 14336,   // >= largest config-(a) record (VWW 27,712 B = 13,856 words)
    parameter int CUT_THROUGH = 0,
    parameter int CT_DEPTH    = 1024,    // cut-through FIFO depth (words)
    parameter int MAX_BURST   = 64,      // framer burst size (for cut-through space check)
    parameter int LEN_W       = 20
) (
    input  logic              clk,
    input  logic              rst,

    // fill (framer)
    output logic              fill_ready,   // ready to START a new record
    output logic              wr_burst_ok,  // room to accept one more burst (cut-through flow control)
    input  logic              fw_start,
    input  logic [LEN_W-1:0]  fw_len,     // record length in words
    input  logic              fw_we,
    input  logic [15:0]       fw_data,
    input  logic              fw_commit,

    // drain (engine)
    output logic              eng_valid,
    output logic [15:0]       eng_data,
    output logic              eng_last,
    input  logic              eng_ready
);
  generate
  if (CUT_THROUGH == 0) begin : g_pingpong
    localparam int AW = $clog2(BUF_WORDS);

    logic [15:0] buf0 [BUF_WORDS];
    logic [15:0] buf1 [BUF_WORDS];
    logic [1:0]  st0, st1;              // 0=EMPTY 1=FILLING 2=FULL 3=DRAINING
    logic [LEN_W-1:0] len0, len1;

    logic          fill_sel;
    logic [AW-1:0] fill_ptr;
    logic          drain_sel;
    logic [AW-1:0] drain_ptr;

    wire b0_empty = (st0 == 2'd0);
    wire b1_empty = (st1 == 2'd0);
    wire b0_full  = (st0 == 2'd2);
    wire b1_full  = (st1 == 2'd2);
    wire draining = (st0 == 2'd3) || (st1 == 2'd3);
    assign fill_ready  = b0_empty || b1_empty;
    assign wr_burst_ok = 1'b1;                  // dedicated per-record buffer always has room

    wire [LEN_W-1:0] drain_len = (drain_sel == 1'b0) ? len0 : len1;
    assign eng_valid = draining;
    assign eng_data  = (drain_sel == 1'b0) ? buf0[drain_ptr] : buf1[drain_ptr];
    assign eng_last  = draining && (LEN_W'(drain_ptr) == (drain_len - 1'b1));

    always_ff @(posedge clk) begin
      if (rst) begin
        st0 <= 2'd0; st1 <= 2'd0; fill_sel <= 1'b0; fill_ptr <= '0;
        drain_sel <= 1'b0; drain_ptr <= '0; len0 <= '0; len1 <= '0;
      end else begin
        if (fw_start && fill_ready) begin
          fill_sel <= b0_empty ? 1'b0 : 1'b1;
          fill_ptr <= '0;
          if (b0_empty) begin st0 <= 2'd1; len0 <= fw_len; end
          else          begin st1 <= 2'd1; len1 <= fw_len; end
        end
        if (fw_we) begin
          if (fill_sel == 1'b0) buf0[fill_ptr] <= fw_data;
          else                  buf1[fill_ptr] <= fw_data;
          fill_ptr <= fill_ptr + 1'b1;
        end
        if (fw_commit) begin
          if (fill_sel == 1'b0) st0 <= 2'd2; else st1 <= 2'd2;
        end
        // ---- drain side (seamless switch: no bubble at a record boundary) ----
        if (!draining) begin
          if      (b0_full) begin drain_sel <= 1'b0; drain_ptr <= '0; st0 <= 2'd3; end
          else if (b1_full) begin drain_sel <= 1'b1; drain_ptr <= '0; st1 <= 2'd3; end
        end else if (eng_valid && eng_ready) begin
          if (eng_last) begin
            // retire current buffer and, if the other is already FULL, start it THIS cycle
            if (drain_sel == 1'b0) begin
              st0 <= 2'd0;
              if (b1_full) begin drain_sel <= 1'b1; drain_ptr <= '0; st1 <= 2'd3; end
            end else begin
              st1 <= 2'd0;
              if (b0_full) begin drain_sel <= 1'b0; drain_ptr <= '0; st0 <= 2'd3; end
            end
          end else drain_ptr <= drain_ptr + 1'b1;
        end
      end
    end
  end else begin : g_cutthrough
    localparam int AW = $clog2(CT_DEPTH);
    logic [15:0]      fifo [CT_DEPTH];
    logic [AW:0]      wptr, rptr;
    logic [LEN_W-1:0] remain;
    logic             active;

    wire full  = (wptr[AW-1:0] == rptr[AW-1:0]) && (wptr[AW] != rptr[AW]);
    wire empty = (wptr == rptr);
    wire [AW:0] occ = wptr - rptr;
    assign fill_ready  = !active;                               // one record in flight at a time
    assign wr_burst_ok = (CT_DEPTH - occ) >= MAX_BURST;         // room for one more burst
    assign eng_valid  = active && !empty;
    assign eng_data   = fifo[rptr[AW-1:0]];
    assign eng_last   = eng_valid && (remain == 1);

    always_ff @(posedge clk) begin
      if (rst) begin
        wptr <= '0; rptr <= '0; remain <= '0; active <= 1'b0;
      end else begin
        if (fw_start) begin active <= 1'b1; remain <= fw_len; end
        if (fw_we && !full) begin fifo[wptr[AW-1:0]] <= fw_data; wptr <= wptr + 1'b1; end
        if (eng_valid && eng_ready) begin
          rptr   <= rptr + 1'b1;
          remain <= remain - 1'b1;
          if (eng_last) active <= 1'b0;
        end
      end
    end
    wire _unused_commit = fw_commit;   // length-tracked in cut-through
  end
  endgenerate
endmodule
`endif
