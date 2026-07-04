// replay_top — record-replay datapath: framer + ping-pong buffer + label FIFO (issue #16, PLAN §6).
//
// HyperRAM -> Avalon-MM read master (over the #13 controller) -> split each record into a tensor word
// stream (to the engine, via the ping-pong buffer so the engine never starves) and a golden-label
// stream (to the scoreboard). Config mirrors the scoreboard's REC_BASE/REC_STRIDE/N_RECORDS plus a
// framer-specific tensor length. Direct Avalon master (not mSGDMA): the #13 slave is a plain
// Avalon-MM burst target, so a direct master is the least logic and easiest to close (docs note).
`ifndef REPLAY_TOP_SV
`define REPLAY_TOP_SV
module replay_top #(
    parameter int MAX_BURST   = 64,
    parameter int BUF_WORDS   = 14336,   // >= largest config-(a) record in words
    parameter int CUT_THROUGH = 0,
    parameter int CT_DEPTH    = 1024,    // cut-through FIFO depth (words)
    parameter int LBL_AW      = 5,       // label FIFO depth = 2**LBL_AW (>= engine in-flight)
    parameter int HR_BYTES    = 16 * 1024 * 1024,
    parameter int LOG_RESERVE = 64 * 1024
) (
    input  logic        clk,
    input  logic        rst,

    // configuration (mirrors of scoreboard CSRs + tensor length)
    input  logic        start,
    input  logic [22:0] rec_base,        // word address
    input  logic [19:0] rec_stride_w,    // stride/2
    input  logic [19:0] rec_nwords,      // N/2
    input  logic [31:0] n_records,
    input  logic        loop_en,

    output logic        busy,
    output logic        done,
    output logic        err_overrun,
    output logic        lbl_overflow,    // label FIFO overran (sticky; would bound in-flight)

    // Avalon-MM read master (to the HyperBus controller data slave)
    output logic [22:0] av_address,
    output logic [7:0]  av_burstcount,
    output logic        av_read,
    input  logic [15:0] av_readdata,
    input  logic        av_readdatavalid,
    input  logic        av_waitrequest,

    // engine tensor stream
    output logic        eng_valid,
    output logic [15:0] eng_data,
    output logic        eng_last,
    input  logic        eng_ready,

    // label stream (to scoreboard res_label; pop on retire)
    output logic        lbl_valid,
    output logic [7:0]  lbl_data,
    input  logic        lbl_ready,

    output logic        issue_valid
);
  localparam logic [22:0] GUARD_WORD_LIM = 23'((HR_BYTES - LOG_RESERVE) / 2);

  // framer <-> ping-pong
  logic        fill_ready, wr_burst_ok, fw_start, fw_we, fw_commit;
  logic [19:0] fw_len;
  logic [15:0] fw_data;
  // framer -> label FIFO
  logic        lbl_push;
  logic [7:0]  lbl_wdata;
  logic        lbl_full, lbl_empty;

  record_framer #(.MAX_BURST(MAX_BURST)) u_framer (
      .clk(clk), .rst(rst),
      .start(start), .rec_base(rec_base), .rec_stride_w(rec_stride_w), .rec_nwords(rec_nwords),
      .n_records(n_records), .loop_en(loop_en), .guard_word_lim(GUARD_WORD_LIM),
      .busy(busy), .done(done), .err_overrun(err_overrun),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read),
      .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid), .av_waitrequest(av_waitrequest),
      .fill_ready(fill_ready), .wr_burst_ok(wr_burst_ok), .fw_start(fw_start), .fw_len(fw_len),
      .fw_we(fw_we), .fw_data(fw_data), .fw_commit(fw_commit),
      .lbl_push(lbl_push), .lbl_data(lbl_wdata), .issue_valid(issue_valid));

  pingpong_buf #(.BUF_WORDS(BUF_WORDS), .CUT_THROUGH(CUT_THROUGH), .CT_DEPTH(CT_DEPTH),
                 .MAX_BURST(MAX_BURST)) u_buf (
      .clk(clk), .rst(rst),
      .fill_ready(fill_ready), .wr_burst_ok(wr_burst_ok), .fw_start(fw_start), .fw_len(fw_len), .fw_we(fw_we),
      .fw_data(fw_data), .fw_commit(fw_commit),
      .eng_valid(eng_valid), .eng_data(eng_data), .eng_last(eng_last), .eng_ready(eng_ready));

  sync_fifo #(.WIDTH(8), .ADDR_W(LBL_AW)) u_lbl (
      .clk(clk), .rst(rst),
      .wr_en(lbl_push), .wr_data(lbl_wdata), .full(lbl_full),
      .rd_en(lbl_ready && lbl_valid), .rd_data(lbl_data), .empty(lbl_empty));
  assign lbl_valid = !lbl_empty;

  // sticky overflow: a label pushed while the FIFO is full would bound in-flight below the engine
  always_ff @(posedge clk) begin
    if (rst) lbl_overflow <= 1'b0;
    else if (lbl_push && lbl_full) lbl_overflow <= 1'b1;
  end
endmodule
`endif
