// sb_frontend — hot-domain measurement front-end of the scoreboard (issue #15).
//
// Runs in the engine (hot / PE-array) clock. Per PLAN §3 LV4 the latency and cycle-span window are
// measured HERE, against a hot free-running counter, so no clock-domain-crossing jitter contaminates
// the numbers. Each completed inference is packed into one event word and handed to the async FIFO
// that carries it to the cool CSR domain.
//
// Event contents (see EV_* localparams): match bit, predicted class, golden label, per-inference
// latency (cycles), and the running cycle-span window (first-issue -> this-retire). Because the whole
// event is assembled atomically in one hot cycle, the cool side always sees a coherent set.
//
// Reset-less datapath where practical (AGENTS.md LV1); architectural state (counters, FIFO, sticky
// flags, the first-issue latch) uses synchronous reset via `clear`.
`ifndef SB_FRONTEND_SV
`define SB_FRONTEND_SV
module sb_frontend #(
    parameter int NUM_CLASSES  = 12,
    parameter int LOGIT_W      = 16,   // signed logit width (LOGITS mode)
    parameter int RESULT_MODE  = 0,    // 0 = INDEX (engine gives class idx), 1 = LOGITS (argmax here)
    parameter int LAT_W        = 32,   // per-inference latency counter width
    parameter int CYC_W        = 48,   // cycle-span window width
    parameter int MAX_INFLIGHT = 16,   // >= engine in-flight depth (issue #15 wants >= 4)
    parameter int CLASS_W      = (NUM_CLASSES <= 1) ? 1 : $clog2(NUM_CLASSES),
    // event word width: match(1) + pred + label + latency + window (derived; keep in sync w/ top)
    parameter int EVW          = 1 + CLASS_W + CLASS_W + LAT_W + CYC_W
) (
    input  logic clk,        // hot / engine clock
    input  logic clear,      // synchronous clear pulse (from cool domain via pulse_sync)

    // issue / retire interface from the replay datapath + engine (hot domain).
    // Both sides are ready/valid: the engine must hold res_* until res_ready, the framer must hold
    // issue_valid until issue_ready. Events are only accepted (counted, paired) on a ready cycle, so
    // a full event FIFO STALLS the producer rather than dropping an inference (issue #15).
    input  logic                          issue_valid,
    output logic                          issue_ready,
    input  logic                          res_valid,
    output logic                          res_ready,
    input  logic [CLASS_W-1:0]            res_class,               // used when RESULT_MODE=INDEX
    // LOGITS mode: NUM_CLASSES signed logits, flattened LSB-first (class 0 in the low bits).
    // A flattened bus (not an unpacked array) keeps Verilator's always_comb sensitivity correct.
    input  logic [NUM_CLASSES*LOGIT_W-1:0] res_logits,
    input  logic [CLASS_W-1:0]            res_label,

    // packed event out to the async FIFO (write side)
    output logic                          ev_valid,
    output logic [EVW-1:0]                ev_data,
    input  logic                          ev_full,

    // sticky diagnostics (levels; synchronized to cool domain by the top)
    output logic                          issue_ovf,     // issue offered while timestamp FIFO full
    output logic                          ts_underflow,  // retire fired with no pending issue
    output logic                          ev_drop        // (defensive) retire fired while ev FIFO full
);
  // ---- event field layout (LSBs; total width == EVW parameter) ----
  localparam int EV_MATCH_LSB = 0;
  localparam int EV_PRED_LSB  = EV_MATCH_LSB + 1;
  localparam int EV_LABEL_LSB = EV_PRED_LSB + CLASS_W;
  localparam int EV_LAT_LSB   = EV_LABEL_LSB + CLASS_W;
  localparam int EV_CYC_LSB   = EV_LAT_LSB + LAT_W;

  localparam int TS_AW = (MAX_INFLIGHT <= 1) ? 1 : $clog2(MAX_INFLIGHT);

  // free-running hot cycle counter (reset each run so numbers stay small / TB-predictable)
  logic [CYC_W-1:0] hot_cycle;
  always_ff @(posedge clk) hot_cycle <= clear ? '0 : (hot_cycle + 1'b1);

  // handshake: an event is accepted only when it can actually be recorded
  logic ts_full, ts_empty;
  assign issue_ready = !ts_full;
  assign res_ready   = !ev_full;
  wire   issue_fire  = issue_valid && issue_ready;
  wire   res_fire    = res_valid   && res_ready;

  // first-issue latch -> window start
  logic             first_seen;
  logic [CYC_W-1:0] window_start;
  always_ff @(posedge clk) begin
    if (clear) begin
      first_seen   <= 1'b0;
      window_start <= '0;
    end else if (issue_fire && !first_seen) begin
      first_seen   <= 1'b1;
      window_start <= hot_cycle;
    end
  end

  // issue-timestamp FIFO: pair each accepted issue with a retire, in order
  logic [CYC_W-1:0] ts_head;
  logic             ts_pop;
  sync_fifo #(.WIDTH(CYC_W), .ADDR_W(TS_AW)) u_ts_fifo (
      .clk(clk), .rst(clear),
      .wr_en(issue_fire), .wr_data(hot_cycle), .full(ts_full),
      .rd_en(ts_pop), .rd_data(ts_head), .empty(ts_empty)
  );
  assign ts_pop = res_fire && !ts_empty;

  // sticky diagnostics. A stall (valid high while !ready) is NORMAL backpressure, not an error — the
  // producer is expected to hold. A DROP is a handshake violation: an item offered while not ready
  // and then withdrawn before it was accepted. We detect that by remembering last cycle's handshake.
  logic iv_d, ir_d, rv_d, rr_d;
  always_ff @(posedge clk) begin
    if (clear) begin iv_d <= 1'b0; ir_d <= 1'b0; rv_d <= 1'b0; rr_d <= 1'b0; end
    else       begin iv_d <= issue_valid; ir_d <= issue_ready; rv_d <= res_valid; rr_d <= res_ready; end
  end
  always_ff @(posedge clk) begin
    if (clear) begin
      issue_ovf    <= 1'b0;
      ts_underflow <= 1'b0;
      ev_drop      <= 1'b0;
    end else begin
      if (iv_d && !ir_d && !issue_valid) issue_ovf    <= 1'b1;  // issue withdrawn during a stall
      if (res_fire && ts_empty)          ts_underflow <= 1'b1;  // retire with nothing pending
      if (rv_d && !rr_d && !res_valid)   ev_drop      <= 1'b1;  // retire withdrawn during a stall
    end
  end

  // argmax (LOGITS mode) — lowest index wins ties (documented tie-break, matches parity gate #21).
  // Part-selects of a signed vector are unsigned, so each logit is re-cast with $signed.
  logic [CLASS_W-1:0]        pred;
  logic signed [LOGIT_W-1:0] best_val;
  always_comb begin
    if (RESULT_MODE == 1) begin
      pred     = '0;
      best_val = $signed(res_logits[0 +: LOGIT_W]);
      for (int i = 1; i < NUM_CLASSES; i++) begin
        logic signed [LOGIT_W-1:0] li;
        li = $signed(res_logits[i*LOGIT_W +: LOGIT_W]);
        if (li > best_val) begin
          best_val = li;
          pred     = CLASS_W'(i);
        end
      end
    end else begin
      pred     = res_class;
      best_val = '0;
    end
  end

  // per-inference latency and current window span
  logic [CYC_W-1:0] issued_ts;
  assign issued_ts = ts_empty ? hot_cycle : ts_head;          // underflow -> zero latency, flag set

  logic [CYC_W-1:0] latency_cyc;
  assign latency_cyc = hot_cycle - issued_ts;

  logic [CYC_W-1:0] window_span;
  assign window_span = hot_cycle - window_start;              // first-issue -> this retire

  // pack + emit event (drop if FIFO full — flagged, must not happen in normal operation)
  logic             match;
  assign match = (pred == res_label);

  // saturate latency into LAT_W bits if the span somehow exceeds it
  wire lat_overflow = (CYC_W > LAT_W) ? |latency_cyc[CYC_W-1:LAT_W] : 1'b0;
  logic [LAT_W-1:0] lat_trunc;
  assign lat_trunc = lat_overflow ? {LAT_W{1'b1}} : latency_cyc[LAT_W-1:0];

  assign ev_valid = res_fire;
  always_comb begin
    ev_data                                     = '0;
    ev_data[EV_MATCH_LSB]                       = match;
    ev_data[EV_PRED_LSB  +: CLASS_W]            = pred;
    ev_data[EV_LABEL_LSB +: CLASS_W]            = res_label;
    ev_data[EV_LAT_LSB   +: LAT_W]              = lat_trunc;
    ev_data[EV_CYC_LSB   +: CYC_W]              = window_span;
  end
endmodule
`endif
