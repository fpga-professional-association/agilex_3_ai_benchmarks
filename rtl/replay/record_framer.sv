// record_framer — reads records from HyperRAM and splits them for the engine + scoreboard (issue #16).
//
// Avalon-MM read master over the HyperBus controller (#13). Per record (docs/record_format.md): reads
// `stride` bytes as 16-bit words in linear bursts and routes each word by position —
//   word_idx <  nwords : tensor word -> ping-pong buffer
//   word_idx == nwords : label word  -> low byte to the label FIFO (byte N)
//   word_idx >  nwords : pad         -> discarded
// Loops when loop_en; asserts an overrun error if the record store would reach into the log reserve
// (PLAN §6 / issue #16 "do not read into the top 64 KB"). Layout is fixed at pack time — no reformat.
`ifndef RECORD_FRAMER_SV
`define RECORD_FRAMER_SV
module record_framer #(
    parameter int MAX_BURST = 64        // max words per Avalon burst (<= 255)
) (
    input  logic        clk,
    input  logic        rst,

    // configuration (words); driven by CSR mirrors in replay_top
    input  logic        start,          // pulse
    input  logic [22:0] rec_base,       // word address of record 0
    input  logic [19:0] rec_stride_w,   // stride in words (= stride_bytes/2)
    input  logic [19:0] rec_nwords,     // tensor words per record (= N/2)
    input  logic [31:0] n_records,
    input  logic        loop_en,
    input  logic [22:0] guard_word_lim, // first word address the store may NOT reach (log reserve)

    output logic        busy,
    output logic        done,
    output logic        err_overrun,

    // Avalon-MM read master
    output logic [22:0] av_address,
    output logic [7:0]  av_burstcount,
    output logic        av_read,
    input  logic [15:0] av_readdata,
    input  logic        av_readdatavalid,
    input  logic        av_waitrequest,

    // to ping-pong buffer
    input  logic        fill_ready,
    input  logic        wr_burst_ok,    // buffer has room for one more burst (cut-through flow control)
    output logic        fw_start,
    output logic [19:0] fw_len,
    output logic        fw_we,
    output logic [15:0] fw_data,
    output logic        fw_commit,

    // to label FIFO + scoreboard
    output logic        lbl_push,
    output logic [7:0]  lbl_data,
    output logic        issue_valid     // one pulse per record handed to the engine
);
  typedef enum logic [2:0] {IDLE, REC, REQ, RECV, NEXTR, FINISH} state_t;
  state_t st;

  logic [31:0] rec_idx;
  logic [22:0] cur_addr;
  logic [19:0] word_idx;       // position within the current record
  logic [19:0] words_left;     // words remaining in the current record
  logic [7:0]  burst_rem;      // words remaining in the current burst

  assign busy = (st != IDLE) && (st != FINISH);
  assign done = (st == FINISH);

  wire [7:0] this_burst = (words_left > 20'(MAX_BURST)) ? 8'(MAX_BURST) : words_left[7:0];
  wire [31:0] store_end = {9'd0, rec_base} + (n_records * {12'd0, rec_stride_w});

  always_ff @(posedge clk) begin
    if (rst) begin
      st <= IDLE; rec_idx <= '0; cur_addr <= '0; word_idx <= '0; words_left <= '0;
      burst_rem <= '0; err_overrun <= 1'b0;
      av_address <= '0; av_burstcount <= '0; av_read <= 1'b0;
      fw_start <= 1'b0; fw_len <= '0; fw_we <= 1'b0; fw_data <= '0; fw_commit <= 1'b0;
      lbl_push <= 1'b0; lbl_data <= '0; issue_valid <= 1'b0;
    end else begin
      // default pulse deassertions
      fw_start <= 1'b0; fw_we <= 1'b0; fw_commit <= 1'b0; lbl_push <= 1'b0; issue_valid <= 1'b0;

      unique case (st)
        IDLE: begin
          if (start) begin
            // overrun guard: end of the record store must stay below the log reserve
            if (store_end > {9'd0, guard_word_lim}) begin
              err_overrun <= 1'b1;   // refuse to run; stays set until a valid start
            end else begin
              err_overrun <= 1'b0;
              rec_idx <= '0; cur_addr <= rec_base; st <= REC;
            end
          end
        end

        REC: begin
          if (fill_ready) begin
            fw_start   <= 1'b1;
            fw_len     <= rec_nwords;
            issue_valid<= 1'b1;
            word_idx   <= '0;
            words_left <= rec_stride_w;
            st <= REQ;
          end
        end

        REQ: begin
          if (wr_burst_ok) begin           // wait for buffer room (cut-through); always ok normal
            av_address   <= cur_addr;
            av_burstcount<= this_burst;
            av_read      <= 1'b1;
          end
          if (av_read && !av_waitrequest) begin
            av_read   <= 1'b0;
            burst_rem <= this_burst;
            st <= RECV;
          end
        end

        RECV: begin
          if (av_readdatavalid) begin
            // route the word
            if (word_idx < rec_nwords) begin
              fw_we <= 1'b1; fw_data <= av_readdata;
            end else if (word_idx == rec_nwords) begin
              lbl_push <= 1'b1; lbl_data <= av_readdata[7:0];
            end
            word_idx   <= word_idx + 1'b1;
            words_left <= words_left - 1'b1;
            cur_addr   <= cur_addr + 1'b1;
            burst_rem  <= burst_rem - 1'b1;
            if (burst_rem == 8'd1) begin
              if (words_left == 20'd1) begin
                fw_commit <= 1'b1;
                st <= NEXTR;
              end else st <= REQ;
            end
          end
        end

        NEXTR: begin
          if (rec_idx + 1 == n_records) begin
            if (loop_en) begin rec_idx <= '0; cur_addr <= rec_base; st <= REC; end
            else st <= FINISH;
          end else begin
            rec_idx <= rec_idx + 1'b1;   // cur_addr already at the next record
            st <= REC;
          end
        end

        FINISH: begin
          if (start) st <= IDLE;         // allow re-arm
        end
        default: st <= IDLE;
      endcase
    end
  end
endmodule
`endif
