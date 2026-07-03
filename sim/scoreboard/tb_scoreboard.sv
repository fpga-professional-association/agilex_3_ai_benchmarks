// tb_scoreboard — self-checking Verilator testbench for the benchmark scoreboard (issue #15).
//
// Strategy: an independent REFERENCE MODEL shadows the DUT's boundary signals. It watches the same
// hot-domain issue/retire handshakes the DUT sees and recomputes, from the spec, the expected
// DONE/PASS/LAT_MIN/LAT_MAX, latency histogram, and cycle-span window. Because it observes real
// posedge-sampled events, its numbers track the DUT's frontend exactly (differences of a free hot
// counter cancel any absolute origin offset from the clear). The tests drive stimulus, drain, then
// read the CSRs and assert equality with the reference.
//
// Covers the five scenario groups from issue #15:
//   (1) 1000 randomized inferences -> exact DONE/PASS/LAT_MIN/LAT_MAX
//   (2) latency histogram + p50/p99 reconstruction
//   (3) mid-run snapshot coherency + exact end-of-run snapshot
//   (4) loop mode + SOFT_RESET clears counters but preserves config
//   (5) issue-FIFO overflow, timestamp underflow, backpressure (stall, never drop), and the
//       64-bit CYCLES span split.
`timescale 1ns/1ps
module tb_scoreboard;
  import bench_pkg::*;

  localparam int NUM_CLASSES  = 12;
  localparam int CLASS_W      = 4;
  localparam int LAT_W        = 32;
  localparam int CYC_W        = 48;
  localparam int MAX_INFLIGHT = 16;
  localparam int FIFO_AW      = 4;

  // ---- clocks (hot faster than cool, asymmetric to stress the CDC) ----
  logic clk = 0;        // cool / CSR
  logic hot_clk = 0;    // hot / engine
  always #10 clk = ~clk;      // 50 MHz-ish
  always #4  hot_clk = ~hot_clk;

  // ---- DUT I/O ----
  logic                 rst;
  logic                 issue_valid, issue_ready, res_valid, res_ready;
  logic [CLASS_W-1:0]   res_class, res_label;
  logic [NUM_CLASSES*16-1:0] res_logits;
  logic [7:0]           csr_address;
  logic                 csr_read, csr_write;
  logic [31:0]          csr_readdata, csr_writedata;
  logic                 csr_waitrequest;
  logic                 run_start, loop_en;
  logic [31:0]          cfg_n_records, cfg_rec_stride, cfg_rec_base, cfg_log_base;

  scoreboard #(
      .NUM_CLASSES(NUM_CLASSES), .LOGIT_W(16), .RESULT_MODE(0),
      .LAT_W(LAT_W), .CYC_W(CYC_W), .MAX_INFLIGHT(MAX_INFLIGHT), .FIFO_AW(FIFO_AW)
  ) dut (
      .clk(clk), .rst(rst),
      .hot_clk(hot_clk),
      .issue_valid(issue_valid), .issue_ready(issue_ready),
      .res_valid(res_valid), .res_ready(res_ready),
      .res_class(res_class), .res_logits(res_logits), .res_label(res_label),
      .csr_address(csr_address), .csr_read(csr_read), .csr_readdata(csr_readdata),
      .csr_write(csr_write), .csr_writedata(csr_writedata), .csr_waitrequest(csr_waitrequest),
      .run_start(run_start), .loop_en(loop_en),
      .cfg_n_records(cfg_n_records), .cfg_rec_stride(cfg_rec_stride),
      .cfg_rec_base(cfg_rec_base), .cfg_log_base(cfg_log_base)
  );

  // ---------------- reference model (shadow) ----------------
  int  ref_done, ref_pass;
  longint ref_min, ref_max, ref_window;
  int  ref_hist [HIST_ENTRIES];
  longint ref_lat [16384];       // per-inference latency list, for percentile cross-check
  int  ref_lat_n;
  int  ref_hist_shift;

  // shadow issue-timestamp ring
  longint ring [0:127];
  int  ring_wr, ring_rd;
  longint tb_hot;                // free-running hot counter (never reset; only differences used)
  logic ref_armed;
  logic ref_reset_req;
  longint ref_win_start;
  logic ref_first;
  logic saw_backpressure;
  int  errors;

  function automatic int ring_count();  return ring_wr - ring_rd; endfunction

  always @(posedge hot_clk) begin
    // reset the reference at a quiescent point requested by the driver
    if (ref_reset_req) begin
      ref_done = 0; ref_pass = 0; ref_min = 64'hFFFF_FFFF; ref_max = 0;
      ref_window = 0; ref_lat_n = 0; ref_first = 1'b0;
      ring_wr = 0; ring_rd = 0;
      for (int b = 0; b < HIST_ENTRIES; b++) ref_hist[b] = 0;
      ref_armed = 1'b1;
      ref_reset_req = 1'b0;
    end else if (ref_armed) begin
      // observe accepted issue
      if (issue_valid && issue_ready) begin
        if (!ref_first) begin ref_first = 1'b1; ref_win_start = tb_hot; end
        ring[ring_wr[6:0]] = tb_hot;
        ring_wr = ring_wr + 1;
      end
      // observe accepted retire
      if (res_valid && res_ready) begin
        longint issued, lat;
        int bkt;
        if (ring_count() == 0) begin
          issued = tb_hot;                 // underflow: DUT uses hot_cycle -> latency 0
        end else begin
          issued = ring[ring_rd[6:0]];
          ring_rd = ring_rd + 1;
        end
        lat = tb_hot - issued;
        ref_done = ref_done + 1;
        if (res_class == res_label) ref_pass = ref_pass + 1;
        if (lat < ref_min) ref_min = lat;
        if (lat > ref_max) ref_max = lat;
        ref_window = tb_hot - ref_win_start;
        bkt = int'(lat >> ref_hist_shift);
        if (bkt >= HIST_ENTRIES) bkt = HIST_ENTRIES-1;
        ref_hist[bkt] = ref_hist[bkt] + 1;
        if (ref_lat_n < 16384) begin ref_lat[ref_lat_n] = lat; ref_lat_n++; end
      end
      if (!res_ready) saw_backpressure = 1'b1;
    end
    tb_hot = tb_hot + 1;
  end

  // ---------------- helpers ----------------
  task automatic check(input logic cond, input string msg);
    if (!cond) begin $display("FAIL: %s", msg); errors = errors + 1; end
  endtask

  // All DUT inputs are driven on the negedge so they are stable before the posedge the DUT samples;
  // this avoids the TB-write / DUT-sample race (Verilator runs `<=` in initial context as blocking).
  task automatic csr_wr(input logic [7:0] a, input logic [31:0] d);
    @(negedge clk);
    csr_address = a; csr_writedata = d; csr_write = 1'b1;
    @(negedge clk);
    csr_write = 1'b0;
  endtask

  task automatic csr_rd(input logic [7:0] a, output logic [31:0] d);
    @(negedge clk);
    csr_address = a; csr_read = 1'b1;
    @(posedge clk);            // snapshot latches here if reading CYCLES_LO
    #1 d = csr_readdata;       // combinational readdata; sample after it settles
    @(negedge clk);
    csr_read = 1'b0;
  endtask

  // arm a fresh run: START, wait for clear to finish in both domains, then arm the reference
  task automatic start_run(input logic loop, input int n_records, input int hist_shift);
    logic [31:0] st;
    csr_wr(ADDR_HIST_SHIFT, hist_shift);
    csr_wr(ADDR_N_RECORDS, n_records);
    ref_hist_shift = hist_shift;
    // CTRL: START (bit0) + optional LOOP_EN (bit1)
    csr_wr(ADDR_CTRL, (32'b1 | (loop ? 32'b10 : 32'b0)));
    // wait until clearing done and running asserted
    st = '1;
    while (st[ST_CLEARING] || !st[ST_RUNNING]) csr_rd(ADDR_STATUS, st);
    repeat (8) @(posedge hot_clk);     // let clearing_hot deassert in the hot domain
    // request reference reset and wait for the shadow to apply it
    ref_reset_req = 1'b1;
    while (ref_reset_req) @(posedge hot_clk);
    repeat (2) @(posedge hot_clk);
  endtask

  task automatic soft_reset();
    logic [31:0] st;
    csr_wr(ADDR_CTRL, 32'b100);       // SOFT_RESET (bit2)
    st = '1;
    while (st[ST_CLEARING]) csr_rd(ADDR_STATUS, st);
    repeat (8) @(posedge hot_clk);
    ref_reset_req = 1'b1;
    while (ref_reset_req) @(posedge hot_clk);
    repeat (2) @(posedge hot_clk);
  endtask

  // hot-domain event drivers (honor ready -> stall, never drop). Inputs change on negedge.
  task automatic drive_issue();
    @(negedge hot_clk); issue_valid = 1'b1;
    @(posedge hot_clk);
    while (!issue_ready) @(posedge hot_clk);    // held through any stall
    @(negedge hot_clk); issue_valid = 1'b0;
  endtask

  // issue that intentionally withdraws regardless of readiness (for the overflow test)
  task automatic drive_issue_force();
    @(negedge hot_clk); issue_valid = 1'b1;
    @(negedge hot_clk); issue_valid = 1'b0;
  endtask

  task automatic drive_res(input int pred, input int label);
    @(negedge hot_clk);
    res_class = pred[CLASS_W-1:0]; res_label = label[CLASS_W-1:0]; res_valid = 1'b1;
    @(posedge hot_clk);
    while (!res_ready) @(posedge hot_clk);       // held through any stall
    @(negedge hot_clk); res_valid = 1'b0;
  endtask

  task automatic drain();
    repeat (200) @(posedge clk);
  endtask

  // read the full counter snapshot (LO read latches it)
  task automatic read_snapshot(output longint cyc, output int done, output int pass,
                               output longint lmin, output longint lmax);
    logic [31:0] lo, hi, d, p, mn, mx;
    csr_rd(ADDR_CYCLES_LO, lo);   // latches snapshot
    csr_rd(ADDR_CYCLES_HI, hi);
    csr_rd(ADDR_DONE, d);
    csr_rd(ADDR_PASS, p);
    csr_rd(ADDR_LAT_MIN, mn);
    csr_rd(ADDR_LAT_MAX, mx);
    cyc  = {hi, lo};
    done = d; pass = p; lmin = longint'(mn); lmax = longint'(mx);
  endtask

  // continuous ready/valid streams (used by the backpressure test to actually fill the event FIFO).
  // valid is held high (set on negedge) until n transfers complete, so a stall never withdraws.
  task automatic issue_stream(input int n);
    int cnt = 0;
    @(negedge hot_clk); issue_valid = 1'b1;
    while (cnt < n) begin
      @(posedge hot_clk);
      if (issue_ready) cnt++;
    end
    @(negedge hot_clk); issue_valid = 1'b0;
  endtask

  task automatic res_stream(input int n);
    int cnt = 0;
    @(negedge hot_clk); res_class = 0; res_label = 0; res_valid = 1'b1;
    while (cnt < n) begin
      @(posedge hot_clk);
      if (res_ready) cnt++;
    end
    @(negedge hot_clk); res_valid = 1'b0;
  endtask

  function automatic int pct_bucket(input int h [HIST_ENTRIES], input int total, input int p);
    int need, cum;
    need = (p * total + 99) / 100;
    cum = 0;
    for (int b = 0; b < HIST_ENTRIES; b++) begin
      cum += h[b];
      if (cum >= need) return b;
    end
    return HIST_ENTRIES-1;
  endfunction

  // ---------------- test sequence ----------------
  int rb_hist [HIST_ENTRIES];
  initial begin
    // init
    rst = 1'b1; issue_valid = 0; res_valid = 0; res_class = 0; res_label = 0;
    csr_address = 0; csr_read = 0; csr_write = 0; csr_writedata = 0;
    tb_hot = 0; ref_armed = 0; ref_reset_req = 0; ref_first = 0; ref_win_start = 0;
    saw_backpressure = 0; errors = 0;
    res_logits = '0;
    repeat (5) @(posedge clk);
    rst = 1'b0;
    repeat (5) @(posedge clk);

    // ---- config read/write ----
    csr_wr(ADDR_REC_STRIDE, 32'd704);
    csr_wr(ADDR_REC_BASE,   32'h0010_0000);
    csr_wr(ADDR_LOG_BASE,   32'h00FF_0000);
    begin
      logic [31:0] v;
      csr_rd(ADDR_REC_STRIDE, v); check(v == 32'd704, "REC_STRIDE readback");
      csr_rd(ADDR_REC_BASE,   v); check(v == 32'h0010_0000, "REC_BASE readback");
      csr_rd(ADDR_LOG_BASE,   v); check(v == 32'h00FF_0000, "LOG_BASE readback");
      check(cfg_rec_stride == 32'd704, "cfg_rec_stride output");
    end

    // =========================================================
    // Scenario 1: 1000 randomized in-order inferences
    // =========================================================
    start_run(.loop(0), .n_records(1000), .hist_shift(2));
    for (int i = 0; i < 1000; i++) begin
      int gap, pred, lab;
      gap  = $urandom_range(1, 30);
      pred = $urandom_range(0, NUM_CLASSES-1);
      lab  = $urandom_range(0, NUM_CLASSES-1);
      drive_issue();
      repeat (gap) @(posedge hot_clk);
      drive_res(pred, lab);
    end
    drain();
    begin
      longint cyc; int done, pass; longint lmin, lmax;
      read_snapshot(cyc, done, pass, lmin, lmax);
      check(done == ref_done,   $sformatf("S1 DONE %0d != ref %0d", done, ref_done));
      check(pass == ref_pass,   $sformatf("S1 PASS %0d != ref %0d", pass, ref_pass));
      check(lmin == ref_min,    $sformatf("S1 LAT_MIN %0d != ref %0d", lmin, ref_min));
      check(lmax == ref_max,    $sformatf("S1 LAT_MAX %0d != ref %0d", lmax, ref_max));
      check(cyc  == ref_window, $sformatf("S1 CYCLES %0d != ref %0d", cyc, ref_window));
      // histogram bucket-by-bucket
      for (int b = 0; b < HIST_ENTRIES; b++) begin
        logic [31:0] hv;
        csr_wr(ADDR_HIST_ADDR, b);
        csr_rd(ADDR_HIST_DATA, hv);
        rb_hist[b] = hv;
        check(hv == ref_hist[b], $sformatf("S1 HIST[%0d] %0d != ref %0d", b, hv, ref_hist[b]));
      end
      // percentiles from readback vs reference histogram (must be within one bucket)
      check(pct_bucket(rb_hist, ref_done, 50) <= pct_bucket(ref_hist, ref_done, 50) + 1 &&
            pct_bucket(rb_hist, ref_done, 50) + 1 >= pct_bucket(ref_hist, ref_done, 50),
            "S1 p50 bucket mismatch");
      check(pct_bucket(rb_hist, ref_done, 99) <= pct_bucket(ref_hist, ref_done, 99) + 1 &&
            pct_bucket(rb_hist, ref_done, 99) + 1 >= pct_bucket(ref_hist, ref_done, 99),
            "S1 p99 bucket mismatch");
      $display("S1 done=%0d pass=%0d min=%0d max=%0d cyc=%0d p50bkt=%0d p99bkt=%0d",
               done, pass, lmin, lmax, cyc,
               pct_bucket(rb_hist, ref_done, 50), pct_bucket(rb_hist, ref_done, 99));
    end

    // =========================================================
    // Scenario 2: overlap (8 in flight), then retire in order
    // =========================================================
    start_run(.loop(0), .n_records(8), .hist_shift(1));
    for (int i = 0; i < 8; i++) drive_issue();
    for (int i = 0; i < 8; i++) drive_res(i % NUM_CLASSES, i % NUM_CLASSES); // all correct
    drain();
    begin
      longint cyc; int done, pass; longint lmin, lmax;
      read_snapshot(cyc, done, pass, lmin, lmax);
      check(done == ref_done, "S2 DONE");
      check(pass == ref_pass, "S2 PASS");
      check(pass == 8, "S2 all correct -> PASS==8");
      check(cyc  == ref_window, "S2 CYCLES");
      $display("S2 overlap done=%0d pass=%0d min=%0d max=%0d", done, pass, lmin, lmax);
    end

    // =========================================================
    // Scenario 3: mid-run snapshot coherency
    // =========================================================
    start_run(.loop(0), .n_records(200), .hist_shift(2));
    fork
      begin : injector
        for (int i = 0; i < 200; i++) begin
          drive_issue();
          repeat ($urandom_range(1,5)) @(posedge hot_clk);
          drive_res($urandom_range(0,NUM_CLASSES-1), $urandom_range(0,NUM_CLASSES-1));
        end
      end
      begin : reader
        repeat (40) @(posedge clk);
        for (int k = 0; k < 5; k++) begin
          longint cyc; int done, pass; longint lmin, lmax;
          read_snapshot(cyc, done, pass, lmin, lmax);
          check(pass <= done, $sformatf("S3 coherency PASS(%0d) <= DONE(%0d)", pass, done));
          check(done <= 200, "S3 DONE bounded");
          repeat (30) @(posedge clk);
        end
      end
    join
    drain();
    begin
      longint cyc; int done, pass; longint lmin, lmax;
      read_snapshot(cyc, done, pass, lmin, lmax);
      check(done == ref_done, "S3 final DONE");
      check(pass == ref_pass, "S3 final PASS");
      check(cyc  == ref_window, "S3 final CYCLES");
      $display("S3 mid-run snapshot ok, final done=%0d", done);
    end

    // =========================================================
    // Scenario 4: loop mode + SOFT_RESET preserves config, clears counters
    // =========================================================
    start_run(.loop(1), .n_records(50), .hist_shift(2));
    for (int i = 0; i < 50; i++) begin
      drive_issue(); repeat(3) @(posedge hot_clk); drive_res(1, 1);
    end
    drain();
    begin
      logic [31:0] st, v; int done;
      csr_rd(ADDR_STATUS, st);
      check(st[ST_RUNNING] == 1'b1, "S4 loop mode stays RUNNING");
      csr_rd(ADDR_DONE, v); done = v;   // note: reading DONE without LO won't re-latch; read LO first
      // proper snapshot
      begin longint c; int dn, ps; longint mn, mx; read_snapshot(c, dn, ps, mn, mx);
        check(dn == 50, $sformatf("S4 DONE==50 got %0d", dn)); end
      // SOFT_RESET
      soft_reset();
      begin longint c; int dn, ps; longint mn, mx; read_snapshot(c, dn, ps, mn, mx);
        check(dn == 0, "S4 SOFT_RESET clears DONE");
        check(ps == 0, "S4 SOFT_RESET clears PASS"); end
      csr_rd(ADDR_N_RECORDS, v);  check(v == 50, "S4 SOFT_RESET preserves N_RECORDS");
      csr_rd(ADDR_REC_BASE, v);   check(v == 32'h0010_0000, "S4 SOFT_RESET preserves REC_BASE");
      csr_rd(ADDR_HIST_SHIFT, v); check(v == 2, "S4 SOFT_RESET preserves HIST_SHIFT");
      $display("S4 loop + soft-reset ok");
    end

    // =========================================================
    // Scenario 5a: issue-FIFO overflow flag
    // =========================================================
    start_run(.loop(0), .n_records(0), .hist_shift(0));
    ref_armed = 1'b0;     // don't score this deliberately-broken run
    for (int i = 0; i < MAX_INFLIGHT + 4; i++) drive_issue_force();  // issue past capacity, no retires
    repeat (20) @(posedge clk);
    begin logic [31:0] st; csr_rd(ADDR_STATUS, st);
      check(st[ST_ISSUE_OVF] == 1'b1, "S5a ISSUE_FIFO_OVF set"); end

    // =========================================================
    // Scenario 5b: timestamp underflow flag
    // =========================================================
    soft_reset();
    ref_armed = 1'b0;
    drive_res(3, 3);      // retire with nothing issued
    repeat (20) @(posedge clk);
    begin logic [31:0] st; csr_rd(ADDR_STATUS, st);
      check(st[ST_TS_UNDER] == 1'b1, "S5b TS_FIFO_UNDERFLOW set"); end

    // =========================================================
    // Scenario 5c: backpressure — stall, never drop
    // Concurrent issue/retire streams at max rate. Issues keep the timestamp FIFO full so retires
    // never underflow; retires produce events faster than the (slower) cool domain drains them, so
    // the event FIFO fills and res_ready goes low. The engine (res_stream) then STALLS — and every
    // one of the 300 inferences must still be counted (never dropped).
    // =========================================================
    saw_backpressure = 1'b0;
    start_run(.loop(0), .n_records(300), .hist_shift(2));
    fork
      issue_stream(300);
      begin
        repeat (6) @(posedge hot_clk);   // let a few issues get ahead first
        res_stream(300);
      end
    join
    drain();
    begin
      longint cyc; int done, pass; longint lmin, lmax; logic [31:0] st;
      read_snapshot(cyc, done, pass, lmin, lmax);
      check(done == 300, $sformatf("S5c no-drop: DONE==300 got %0d", done));
      check(done == ref_done, "S5c DONE==ref (nothing dropped)");
      csr_rd(ADDR_STATUS, st);
      check(st[ST_ISSUE_OVF] == 1'b0, "S5c no drop flag (ISSUE_OVF/ev_drop clear)");
      check(st[ST_TS_UNDER] == 1'b0, "S5c no underflow (issues stayed ahead of retires)");
      check(saw_backpressure == 1'b1, "S5c backpressure actually exercised (res_ready went low)");
      $display("S5c backpressure ok: done=%0d, stalled=%0b", done, saw_backpressure);
    end

    // =========================================================
    // Scenario 5d: 64-bit CYCLES span split (force the internal span high)
    // =========================================================
    begin
      logic [31:0] lo, hi;
      force dut.cyc_span = 48'h0001_2345_6789;   // > 2^32
      @(posedge clk);
      csr_rd(ADDR_CYCLES_LO, lo);                 // latches snapshot of the forced value
      csr_rd(ADDR_CYCLES_HI, hi);
      release dut.cyc_span;
      check(lo == 32'h2345_6789, $sformatf("S5d CYCLES_LO got %h", lo));
      check(hi == 32'h0000_0001, $sformatf("S5d CYCLES_HI got %h", hi));
      $display("S5d 64-bit span split ok: {%h,%h}", hi, lo);
    end

    // ---------------- verdict ----------------
    if (errors == 0) $display("PASS");
    else             $display("FAIL: %0d error(s)", errors);
    $finish;
  end

  // global watchdog
  initial begin
    #5_000_000;
    $display("FAIL: timeout");
    $finish;
  end
endmodule
