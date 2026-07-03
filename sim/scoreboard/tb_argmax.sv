// tb_argmax — focused test of the LOGITS-mode argmax inside sb_frontend (issue #15).
// Drives logit vectors (including ties) and checks the predicted class decoded from the packed
// event. Tie-break rule: lowest index wins (must match the parity gate #21).
`timescale 1ns/1ps
module tb_argmax;
  import bench_pkg::*;

  localparam int NUM_CLASSES = 12;
  localparam int CLASS_W     = 4;
  localparam int LOGIT_W     = 16;
  localparam int LAT_W       = 32;
  localparam int CYC_W       = 48;
  localparam int EVW         = 1 + CLASS_W + CLASS_W + LAT_W + CYC_W;

  logic clk = 0; always #5 clk = ~clk;

  logic clear, issue_valid, issue_ready, res_valid, res_ready;
  logic [CLASS_W-1:0] res_class, res_label;
  logic [NUM_CLASSES*LOGIT_W-1:0] res_logits;
  logic ev_valid, ev_full;
  logic [EVW-1:0] ev_data;
  logic issue_ovf, ts_underflow, ev_drop;
  int errors = 0;

  sb_frontend #(
      .NUM_CLASSES(NUM_CLASSES), .LOGIT_W(LOGIT_W), .RESULT_MODE(1),
      .LAT_W(LAT_W), .CYC_W(CYC_W), .MAX_INFLIGHT(8), .CLASS_W(CLASS_W), .EVW(EVW)
  ) dut (
      .clk(clk), .clear(clear),
      .issue_valid(issue_valid), .issue_ready(issue_ready),
      .res_valid(res_valid), .res_ready(res_ready),
      .res_class(res_class), .res_logits(res_logits), .res_label(res_label),
      .ev_valid(ev_valid), .ev_data(ev_data), .ev_full(ev_full),
      .issue_ovf(issue_ovf), .ts_underflow(ts_underflow), .ev_drop(ev_drop)
  );

  function automatic int expected_argmax(input int v [NUM_CLASSES]);
    int best = 0;
    for (int i = 1; i < NUM_CLASSES; i++) if (v[i] > v[best]) best = i;
    return best;   // strict > keeps the lowest index on ties
  endfunction

  // drive one issue+retire with the given logits, return the predicted class from the event
  task automatic run_vec(input int v [NUM_CLASSES], output int pred);
    @(posedge clk);
    issue_valid <= 1'b1;
    @(posedge clk);
    issue_valid <= 1'b0;
    for (int i = 0; i < NUM_CLASSES; i++) res_logits[i*LOGIT_W +: LOGIT_W] <= v[i][LOGIT_W-1:0];
    res_label <= 0;
    @(posedge clk);
    res_valid <= 1'b1;
    @(posedge clk);           // event asserted this cycle
    pred = ev_data[1 +: CLASS_W];
    res_valid <= 1'b0;
  endtask

  int vec [NUM_CLASSES];
  int pred, exp;
  initial begin
    clear = 1; issue_valid = 0; res_valid = 0; ev_full = 0;
    res_class = 0; res_label = 0; res_logits = '0;
    repeat (3) @(posedge clk);
    clear = 0;
    @(posedge clk);

    // directed: unique max at index 5
    for (int i = 0; i < NUM_CLASSES; i++) vec[i] = -100 + i;
    vec[5] = 500;
    run_vec(vec, pred); exp = expected_argmax(vec);
    check(pred == exp, $sformatf("unique-max pred=%0d exp=%0d", pred, exp));

    // directed: tie between index 2 and index 9 -> lowest (2) wins
    for (int i = 0; i < NUM_CLASSES; i++) vec[i] = 0;
    vec[2] = 300; vec[9] = 300;
    run_vec(vec, pred);
    check(pred == 2, $sformatf("tie -> lowest index, got %0d", pred));

    // directed: all equal -> index 0
    for (int i = 0; i < NUM_CLASSES; i++) vec[i] = 7;
    run_vec(vec, pred);
    check(pred == 0, $sformatf("all-equal -> 0, got %0d", pred));

    // directed: negative logits, max at 11
    for (int i = 0; i < NUM_CLASSES; i++) vec[i] = -1000 + i;   // increasing, max at 11
    run_vec(vec, pred);
    check(pred == 11, $sformatf("neg logits max@11, got %0d", pred));

    // randomized
    for (int t = 0; t < 500; t++) begin
      for (int i = 0; i < NUM_CLASSES; i++) vec[i] = $urandom_range(0, 1000) - 500;
      run_vec(vec, pred); exp = expected_argmax(vec);
      check(pred == exp, $sformatf("rand t=%0d pred=%0d exp=%0d", t, pred, exp));
    end

    if (errors == 0) $display("PASS");
    else             $display("FAIL: %0d error(s)", errors);
    $finish;
  end

  task automatic check(input logic cond, input string msg);
    if (!cond) begin $display("FAIL: %s", msg); errors = errors + 1; end
  endtask

  initial begin #2_000_000; $display("FAIL: timeout"); $finish; end
endmodule
