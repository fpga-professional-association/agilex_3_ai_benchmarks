// tb_replay — self-checking Verilator testbench for the record-replay datapath (issue #16).
//
// avalon_mem_bfm (fixture-loaded) -> replay_top -> a fake engine that checks every tensor word and
// label against the memory image (a real packer-produced fixture). Covers: bit-exact replay + ordered
// labels + exact record count, starvation (fast feed / slow engine -> zero gaps at record boundaries),
// backpressure, loop-mode wraparound, and the log-reserve overrun guard.
`timescale 1ns/1ps
module tb_replay #(parameter int CT = 0);   // CT=1 exercises the cut-through path (record > FIFO)
  `include "fixture_params.svh"

  logic clk = 0; always #5 clk = ~clk;
  logic rst;

  // config
  logic        start, loop_en;
  logic [22:0] rec_base;
  logic [19:0] rec_stride_w, rec_nwords;
  logic [31:0] n_records;
  // status
  logic        busy, done, err_overrun, lbl_overflow;
  // Avalon
  logic [22:0] av_address; logic [7:0] av_burstcount; logic av_read;
  logic [15:0] av_readdata; logic av_readdatavalid, av_waitrequest;
  // engine + labels
  logic        eng_valid, eng_last, eng_ready;
  logic [15:0] eng_data;
  logic        lbl_valid, lbl_ready; logic [7:0] lbl_data;
  logic        issue_valid;

  avalon_mem_bfm #(.MEM_WORDS(FX_MEM_WORDS), .READ_LATENCY(4), .GAP(0)) u_bfm (
      .clk(clk), .rst(rst), .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read),
      .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid), .av_waitrequest(av_waitrequest));

  // cut-through FIFO (64 words) is intentionally SMALLER than a record (245 words) to prove streaming
  replay_top #(.MAX_BURST(64), .BUF_WORDS(512), .CUT_THROUGH(CT), .CT_DEPTH(128), .LBL_AW(5)) dut (
      .clk(clk), .rst(rst),
      .start(start), .rec_base(rec_base), .rec_stride_w(rec_stride_w), .rec_nwords(rec_nwords),
      .n_records(n_records), .loop_en(loop_en),
      .busy(busy), .done(done), .err_overrun(err_overrun), .lbl_overflow(lbl_overflow),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read),
      .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid), .av_waitrequest(av_waitrequest),
      .eng_valid(eng_valid), .eng_data(eng_data), .eng_last(eng_last), .eng_ready(eng_ready),
      .lbl_valid(lbl_valid), .lbl_data(lbl_data), .lbl_ready(lbl_ready), .issue_valid(issue_valid));

  int errors = 0;
  task automatic check(input logic cond, input string msg);
    if (!cond) begin $display("FAIL: %s", msg); errors++; end
  endtask

  // ---- fake engine / checker ----
  logic       eng_en, eng_slow, eng_reset;
  int         eng_rec, eng_word, gaps, started_tick, phase_target, lbl_rec;
  int         issue_count;
  logic       tick;
  always_ff @(posedge clk) tick <= ~tick;
  assign eng_ready = eng_en && (eng_slow ? tick : 1'b1);
  // labels are checked/popped independently of tensor draining, in record order — the label word
  // trails the tensor words in memory, so a zero-latency engine would otherwise race it (cut-through).
  assign lbl_ready = eng_en && lbl_valid;

  function automatic logic [15:0] mexp(input int rec, input int word);
    return u_bfm.mem[(rec % FX_COUNT) * FX_STRIDE_W + word];
  endfunction
  function automatic logic [7:0] lexp(input int rec);
    return u_bfm.mem[(rec % FX_COUNT) * FX_STRIDE_W + FX_N_WORDS][7:0];
  endfunction

  always_ff @(posedge clk) begin
    if (eng_reset) begin
      eng_rec <= 0; eng_word <= 0; gaps <= 0; started_tick <= 0; issue_count <= 0; lbl_rec <= 0;
    end else if (eng_en) begin
      if (issue_valid) issue_count <= issue_count + 1;
      if (eng_valid) started_tick <= 1;
      // a gap = ready but no data mid-run, before all target records retire (starvation)
      if (started_tick == 1 && eng_ready && !eng_valid && eng_rec < phase_target) gaps <= gaps + 1;
      // tensor word stream
      if (eng_valid && eng_ready) begin
        if (eng_data !== mexp(eng_rec, eng_word))
          begin $display("FAIL: rec %0d word %0d got %h exp %h", eng_rec, eng_word, eng_data,
                         mexp(eng_rec, eng_word)); errors <= errors + 1; end
        if (eng_last) begin eng_word <= 0; eng_rec <= eng_rec + 1; end
        else eng_word <= eng_word + 1;
      end
      // label stream (decoupled, in order)
      if (lbl_valid) begin
        if (lbl_data !== lexp(lbl_rec))
          begin $display("FAIL: rec %0d label got %h exp %h", lbl_rec, lbl_data, lexp(lbl_rec));
                 errors <= errors + 1; end
        lbl_rec <= lbl_rec + 1;
      end
    end
  end

  task automatic run_phase(input logic loop, input int nrec, input logic slow, input int target);
    // reset everything, then configure + start, then wait for `target` retirements
    rst = 1; eng_en = 0; eng_reset = 1; start = 0; loop_en = 0;
    repeat (3) @(negedge clk);
    rst = 0; eng_reset = 0; eng_slow = slow; eng_en = 1; phase_target = target;
    rec_base = 0; rec_stride_w = FX_STRIDE_W; rec_nwords = FX_N_WORDS; n_records = nrec; loop_en = loop;
    @(negedge clk); start = 1; @(negedge clk); start = 0;
    while (eng_rec < target) @(posedge clk);
    while (lbl_rec < target) @(posedge clk);   // let labels catch up (they trail the tensor)
    repeat (2) @(posedge clk);
    eng_en = 0;
  endtask

  initial begin
    tick = 0;
    // ---- 1. main run: fast feed + fast engine ----
    run_phase(.loop(0), .nrec(FX_COUNT), .slow(0), .target(FX_COUNT));
    check(eng_rec == FX_COUNT, $sformatf("main: retired %0d of %0d", eng_rec, FX_COUNT));
    check(issue_count == FX_COUNT, $sformatf("main: %0d issue pulses", issue_count));
    check(err_overrun == 1'b0, "main: no overrun");
    check(lbl_overflow == 1'b0, "main: no label overflow");
    $display("phase1 main: retired=%0d issues=%0d errors=%0d", eng_rec, issue_count, errors);

    // ---- 2. starvation: fast feed, slow engine -> zero gaps at boundaries (ping-pong only) ----
    run_phase(.loop(0), .nrec(FX_COUNT), .slow(1), .target(FX_COUNT));
    if (CT == 0) check(gaps == 0, $sformatf("starvation: %0d gap cycles (engine starved)", gaps));
    $display("phase2 starvation: gaps=%0d (CT=%0d)", gaps, CT);

    // ---- 3. loop mode: wrap the set twice ----
    run_phase(.loop(1), .nrec(FX_COUNT), .slow(0), .target(2 * FX_COUNT));
    check(eng_rec == 2 * FX_COUNT, $sformatf("loop: retired %0d of %0d", eng_rec, 2 * FX_COUNT));
    $display("phase3 loop: retired=%0d", eng_rec);

    // ---- 4. overrun guard: base near the top of HyperRAM must refuse to run ----
    rst = 1; eng_en = 0; eng_reset = 1; repeat (3) @(negedge clk); rst = 0; eng_reset = 0;
    rec_base = 23'(8355840 - 10); rec_stride_w = FX_STRIDE_W; rec_nwords = FX_N_WORDS;
    n_records = FX_COUNT; loop_en = 0;
    @(negedge clk); start = 1; @(negedge clk); start = 0;
    repeat (10) @(posedge clk);
    check(err_overrun == 1'b1, "overrun: err_overrun asserted for out-of-range base");
    check(busy == 1'b0, "overrun: framer did not run");
    $display("phase4 overrun: err_overrun=%0b", err_overrun);

    if (errors == 0) $display("PASS");
    else             $display("FAIL: %0d error(s)", errors);
    $finish;
  end

  initial begin #5_000_000; $display("FAIL: timeout"); $finish; end
endmodule
