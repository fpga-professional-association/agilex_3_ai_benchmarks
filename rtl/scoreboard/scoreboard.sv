// scoreboard — benchmark scoreboard, cool (CSR) domain top (issue #15).
//
// Turns a stream of retired inferences into the two-register-read result the plan promises (§6):
//   FPS = DONE_COUNT * f_clk / CYCLES_64,  accuracy = PASS_COUNT / DONE_COUNT.
// The hot-domain measurement front-end (sb_frontend) computes latency + the cycle-span window and
// pushes one event per inference through an async FIFO into this domain, where accumulators, a
// latency histogram, and an atomic snapshot feed the Avalon-MM CSR slave (docs/register_map.md).
//
// Hardware counts; the host divides. Nothing here computes FPS or accuracy.
`ifndef SCOREBOARD_SV
`define SCOREBOARD_SV
module scoreboard
  import bench_pkg::*;
#(
    parameter int NUM_CLASSES  = 12,
    parameter int LOGIT_W      = 16,
    parameter int RESULT_MODE  = 0,     // 0 = INDEX, 1 = LOGITS
    parameter int LAT_W        = 32,
    parameter int CYC_W        = 48,
    parameter int MAX_INFLIGHT = 16,
    parameter int FIFO_AW      = 5,     // event async-FIFO depth = 2**FIFO_AW
    parameter int CLASS_W      = (NUM_CLASSES <= 1) ? 1 : $clog2(NUM_CLASSES),
    parameter int EVW          = 1 + CLASS_W + CLASS_W + LAT_W + CYC_W
) (
    // cool / CSR clock domain
    input  logic                 clk,
    input  logic                 rst,          // synchronous, cool domain

    // hot / engine clock domain (ready/valid; engine holds res_* until res_ready)
    input  logic                 hot_clk,
    input  logic                 issue_valid,
    output logic                 issue_ready,
    input  logic                 res_valid,
    output logic                 res_ready,
    input  logic [CLASS_W-1:0]   res_class,
    input  logic [NUM_CLASSES*LOGIT_W-1:0] res_logits,   // flattened signed logits (LOGITS mode)
    input  logic [CLASS_W-1:0]   res_label,

    // Avalon-MM CSR slave (cool domain)
    input  logic [7:0]           csr_address,
    input  logic                 csr_read,
    output logic [31:0]          csr_readdata,
    input  logic                 csr_write,
    input  logic [31:0]          csr_writedata,
    output logic                 csr_waitrequest,

    // to the replay framer (issue #16)
    output logic                 run_start,     // 1-cycle pulse when a START-armed run begins
    output logic                 loop_en,
    output logic [31:0]          cfg_n_records,
    output logic [31:0]          cfg_rec_stride,
    output logic [31:0]          cfg_rec_base,
    output logic [31:0]          cfg_log_base
);
  assign csr_waitrequest = 1'b0;   // 0-wait slave

  // ---------------- configuration registers ----------------
  logic [31:0] n_records, rec_stride, rec_base, log_base;
  logic [5:0]  hist_shift;
  logic [5:0]  hist_addr;
  logic        loop_en_q;
  assign loop_en        = loop_en_q;
  assign cfg_n_records  = n_records;
  assign cfg_rec_stride = rec_stride;
  assign cfg_rec_base   = rec_base;
  assign cfg_log_base   = log_base;

  // ---------------- clear / run FSM ----------------
  logic        clearing;
  logic [6:0]  clr_addr;
  logic        pending_start;
  logic        running;
  wire         ctrl_write   = csr_write && (csr_address == ADDR_CTRL);
  wire         start_req    = ctrl_write && csr_writedata[CTRL_START];
  wire         softrst_req  = ctrl_write && csr_writedata[CTRL_SOFT_RESET];
  wire         clear_begin  = (start_req || softrst_req) && !clearing;

  // ---------------- event async FIFO (hot -> cool) ----------------
  logic             clearing_hot;          // `clearing` synchronized into hot domain
  cdc_bit_sync #(.STAGES(2)) u_clr_hot (.dst_clk(hot_clk), .d(clearing), .q(clearing_hot));

  logic [EVW-1:0]   ev_data_h, ev_rd;
  logic             ev_valid_h, ev_full_h, cool_empty;
  logic             f_issue_ovf, f_ts_under, f_ev_drop;

  sb_frontend #(
      .NUM_CLASSES(NUM_CLASSES), .LOGIT_W(LOGIT_W), .RESULT_MODE(RESULT_MODE),
      .LAT_W(LAT_W), .CYC_W(CYC_W), .MAX_INFLIGHT(MAX_INFLIGHT), .CLASS_W(CLASS_W), .EVW(EVW)
  ) u_frontend (
      .clk(hot_clk), .clear(clearing_hot),
      .issue_valid(issue_valid), .issue_ready(issue_ready),
      .res_valid(res_valid), .res_ready(res_ready),
      .res_class(res_class), .res_logits(res_logits), .res_label(res_label),
      .ev_valid(ev_valid_h), .ev_data(ev_data_h), .ev_full(ev_full_h),
      .issue_ovf(f_issue_ovf), .ts_underflow(f_ts_under), .ev_drop(f_ev_drop)
  );

  wire ev_take = !cool_empty;   // drain one event per cool cycle (show-ahead FIFO)
  async_fifo #(.WIDTH(EVW), .ADDR_W(FIFO_AW)) u_ev_fifo (
      .wr_clk(hot_clk), .wr_rst(clearing_hot), .wr_en(ev_valid_h), .wr_data(ev_data_h), .full(ev_full_h),
      .rd_clk(clk),     .rd_rst(clearing),     .rd_en(ev_take),    .rd_data(ev_rd),     .empty(cool_empty)
  );

  // unpack the event
  localparam int EV_MATCH_LSB = 0;
  localparam int EV_PRED_LSB  = 1;
  localparam int EV_LABEL_LSB = EV_PRED_LSB + CLASS_W;
  localparam int EV_LAT_LSB   = EV_LABEL_LSB + CLASS_W;
  localparam int EV_CYC_LSB   = EV_LAT_LSB + LAT_W;

  wire                  ev_match = ev_rd[EV_MATCH_LSB];
  wire [LAT_W-1:0]      ev_lat   = ev_rd[EV_LAT_LSB +: LAT_W];
  wire [CYC_W-1:0]      ev_cyc   = ev_rd[EV_CYC_LSB +: CYC_W];

  // ---------------- accumulators + histogram ----------------
  logic [31:0]     done_count, pass_count;
  logic [LAT_W-1:0] lat_min, lat_max;
  logic [CYC_W-1:0] cyc_span;
  logic [31:0]     hist_mem [HIST_ENTRIES];

  // histogram bucket for this event's latency (saturating at the top bucket)
  logic [LAT_W-1:0] bucket_ext;
  assign bucket_ext = ev_lat >> hist_shift;
  wire [5:0] ev_bucket = (bucket_ext >= HIST_ENTRIES) ? 6'(HIST_ENTRIES-1) : bucket_ext[5:0];

  always_ff @(posedge clk) begin
    if (rst || clearing) begin
      done_count <= '0;
      pass_count <= '0;
      lat_min    <= '1;      // all ones so first sample wins
      lat_max    <= '0;
      cyc_span   <= '0;
    end else if (ev_take) begin
      done_count <= done_count + 1'b1;
      if (ev_match)              pass_count <= pass_count + 1'b1;
      if (ev_lat < lat_min)      lat_min    <= ev_lat;
      if (ev_lat > lat_max)      lat_max    <= ev_lat;
      cyc_span   <= ev_cyc;
    end
  end

  // histogram memory: one write port, muxed between clear-FSM zeroing and event increment
  // (they never coincide — clearing happens at run boundaries with no in-flight events).
  always_ff @(posedge clk) begin
    if (clearing)      hist_mem[clr_addr[5:0]] <= '0;
    else if (ev_take)  hist_mem[ev_bucket]     <= hist_mem[ev_bucket] + 1'b1;
  end

  // ---------------- clear/run FSM state ----------------
  wire done_flag = !loop_en_q && (n_records != 0) && (done_count >= n_records);

  always_ff @(posedge clk) begin
    if (rst) begin
      clearing      <= 1'b0;
      clr_addr      <= '0;
      pending_start <= 1'b0;
      running       <= 1'b0;
      run_start     <= 1'b0;
    end else begin
      run_start <= 1'b0;
      if (clear_begin) begin
        clearing      <= 1'b1;
        clr_addr      <= '0;
        pending_start <= start_req;   // START arms a run; plain SOFT_RESET does not
      end else if (clearing) begin
        if (clr_addr == 7'(HIST_ENTRIES-1)) begin
          clearing <= 1'b0;
          if (pending_start) begin
            running   <= 1'b1;
            run_start <= 1'b1;
          end
        end else begin
          clr_addr <= clr_addr + 1'b1;
        end
      end else if (done_flag) begin
        running <= 1'b0;
      end
    end
  end

  // ---------------- sticky flags hot -> cool ----------------
  logic issue_ovf_c, ts_under_c, ev_drop_c;
  cdc_bit_sync #(.STAGES(2)) u_s0 (.dst_clk(clk), .d(f_issue_ovf), .q(issue_ovf_c));
  cdc_bit_sync #(.STAGES(2)) u_s1 (.dst_clk(clk), .d(f_ts_under),  .q(ts_under_c));
  cdc_bit_sync #(.STAGES(2)) u_s2 (.dst_clk(clk), .d(f_ev_drop),   .q(ev_drop_c));

  logic [31:0] status;
  always_comb begin
    status                 = '0;
    status[ST_RUNNING]     = running;
    status[ST_DONE]        = done_flag;
    status[ST_ISSUE_OVF]   = issue_ovf_c | ev_drop_c;
    status[ST_TS_UNDER]    = ts_under_c;
    status[ST_CLEARING]    = clearing;
  end

  // ---------------- snapshot (atomic multi-word read) ----------------
  logic [CYC_W-1:0] snap_cyc;
  logic [31:0]      snap_done, snap_pass;
  logic [LAT_W-1:0] snap_min, snap_max;
  wire snap_take = csr_read && (csr_address == ADDR_CYCLES_LO);
  always_ff @(posedge clk) begin
    if (snap_take) begin
      snap_cyc  <= cyc_span;
      snap_done <= done_count;
      snap_pass <= pass_count;
      snap_min  <= lat_min;
      snap_max  <= lat_max;
    end
  end

  // ---------------- CSR writes ----------------
  always_ff @(posedge clk) begin
    if (rst) begin
      n_records  <= '0; rec_stride <= '0; rec_base <= '0; log_base <= '0;
      hist_shift <= '0; hist_addr  <= '0; loop_en_q <= 1'b0;
    end else if (csr_write) begin
      case (csr_address)
        ADDR_CTRL:       loop_en_q  <= csr_writedata[CTRL_LOOP_EN];
        ADDR_N_RECORDS:  n_records  <= csr_writedata;
        ADDR_REC_STRIDE: rec_stride <= csr_writedata;
        ADDR_REC_BASE:   rec_base   <= csr_writedata;
        ADDR_HIST_SHIFT: hist_shift <= csr_writedata[5:0];
        ADDR_HIST_ADDR:  hist_addr  <= csr_writedata[5:0];
        ADDR_LOG_BASE:   log_base   <= csr_writedata;
        default: ;
      endcase
    end
  end

  // ---------------- CSR reads (combinational, 0-wait) ----------------
  always_comb begin
    unique case (csr_address)
      // CTRL: bit0 START and bit2 SOFT_RESET are self-clearing (always read 0); only LOOP_EN persists
      ADDR_CTRL:       csr_readdata = {29'd0, 1'b0, loop_en_q, 1'b0};
      ADDR_N_RECORDS:  csr_readdata = n_records;
      ADDR_REC_STRIDE: csr_readdata = rec_stride;
      ADDR_REC_BASE:   csr_readdata = rec_base;
      ADDR_CYCLES_LO:  csr_readdata = 32'(cyc_span);          // low 32; read here latches the snapshot
      ADDR_CYCLES_HI:  csr_readdata = 32'(snap_cyc >> 32);    // high bits of the 64-bit span (snapshot)
      ADDR_DONE:       csr_readdata = snap_done;
      ADDR_PASS:       csr_readdata = snap_pass;
      ADDR_LAT_MIN:    csr_readdata = 32'(snap_min);
      ADDR_LAT_MAX:    csr_readdata = 32'(snap_max);
      ADDR_STATUS:     csr_readdata = status;
      ADDR_HIST_SHIFT: csr_readdata = {26'd0, hist_shift};
      ADDR_HIST_ADDR:  csr_readdata = {26'd0, hist_addr};
      ADDR_HIST_DATA:  csr_readdata = hist_mem[hist_addr];
      ADDR_LOG_BASE:   csr_readdata = log_base;
      default:         csr_readdata = 32'hDEAD_C0DE;
    endcase
  end

endmodule
`endif
