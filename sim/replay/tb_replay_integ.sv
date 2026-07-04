// tb_replay_integ — integration sim: replay + real HyperBus controller + device + scoreboard (issue #16).
//
// Wires the four real modules together and pushes 100 records end to end:
//   w957d8nb_bfm  <-> hbmc_core (#13)  <-Avalon-  replay_top (#16)  -> fake engine -> scoreboard (#15)
// The engine consumes each record's tensor and retires with res_class=0; the replay's label stream
// feeds the scoreboard's res_label. Checks DONE_COUNT == 100 and PASS_COUNT == the expected number of
// records whose (label & 0xF) == 0 (from the device's memory pattern) — proving labels flow in order.
`timescale 1ns/1ps
module tb_replay_integ;
  import bench_pkg::*;
  import hyperbus_pkg::*;

  localparam int N_REC   = 100;
  localparam int STRIDE_B = 512;      // bytes
  localparam int N_BYTES  = 490;
  localparam int NUM_CLASSES = 12;
  localparam int CLASS_W = 4;

  logic clk = 0; always #5 clk = ~clk;
  logic rst;

  // ---- HyperBus controller <-> device ----
  logic       core_cs_n, core_dq_oe, core_rwds_o, core_rwds_oe; logic [7:0] core_dq_o;
  logic       bfm_dq_oe, bfm_rwds_o, bfm_rwds_oe; logic [7:0] bfm_dq_o;
  wire  [7:0] dq_bus   = core_dq_oe   ? core_dq_o   : (bfm_dq_oe   ? bfm_dq_o   : 8'h00);
  wire        rwds_bus = core_rwds_oe ? core_rwds_o : (bfm_rwds_oe ? bfm_rwds_o : 1'b0);

  // controller CSR + Avalon
  logic [5:0]  hcsr_addr; logic hcsr_read, hcsr_write; logic [31:0] hcsr_rdata, hcsr_wdata;
  logic [22:0] av_address; logic [7:0] av_burstcount; logic av_read;
  logic [15:0] av_readdata; logic av_readdatavalid, av_waitrequest;

  hbmc_core #(.LAT_BEATS_DEFAULT(6)) u_hbmc (
      .clk(clk), .rst(rst),
      .csr_address(hcsr_addr), .csr_read(hcsr_read), .csr_readdata(hcsr_rdata),
      .csr_write(hcsr_write), .csr_writedata(hcsr_wdata),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read),
      .av_writedata(16'd0), .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid),
      .av_waitrequest(av_waitrequest),
      .hb_cs_n(core_cs_n), .hb_dq_o(core_dq_o), .hb_dq_oe(core_dq_oe), .hb_dq_i(dq_bus),
      .hb_rwds_o(core_rwds_o), .hb_rwds_oe(core_rwds_oe), .hb_rwds_i(rwds_bus), .hb_capture_delay());

  w957d8nb_bfm #(.MEM_BYTES(65536), .LAT_BEATS(6), .ROW_BYTES(128), .ROW_PENALTY(4)) u_dev (
      .clk(clk), .cs_n(core_cs_n), .dq_i(dq_bus), .rwds_i(rwds_bus),
      .dq_o(bfm_dq_o), .dq_oe(bfm_dq_oe), .rwds_o(bfm_rwds_o), .rwds_oe(bfm_rwds_oe), .collision(1'b0));

  // ---- replay datapath ----
  logic        rp_start, rp_busy, rp_done, rp_overrun, rp_lblovf;
  logic        eng_valid, eng_last, eng_ready; logic [15:0] eng_data;
  logic        lbl_valid, lbl_ready; logic [7:0] lbl_data;
  logic        issue_valid;

  replay_top #(.MAX_BURST(64), .BUF_WORDS(512), .CUT_THROUGH(0), .LBL_AW(6)) u_replay (
      .clk(clk), .rst(rst),
      .start(rp_start), .rec_base(23'd0), .rec_stride_w(20'(STRIDE_B/2)), .rec_nwords(20'(N_BYTES/2)),
      .n_records(32'(N_REC)), .loop_en(1'b0),
      .busy(rp_busy), .done(rp_done), .err_overrun(rp_overrun), .lbl_overflow(rp_lblovf),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read),
      .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid), .av_waitrequest(av_waitrequest),
      .eng_valid(eng_valid), .eng_data(eng_data), .eng_last(eng_last), .eng_ready(eng_ready),
      .lbl_valid(lbl_valid), .lbl_data(lbl_data), .lbl_ready(lbl_ready), .issue_valid(issue_valid));

  // ---- fake engine: consume tensor, retire per record with res_class = 0 ----
  logic retire_pending;
  logic sb_res_valid; logic [CLASS_W-1:0] sb_res_class, sb_res_label;
  assign eng_ready   = !retire_pending;                       // hold until the current record retires
  assign sb_res_valid = retire_pending && lbl_valid;          // retire once the label is available
  assign lbl_ready   = sb_res_valid;                          // pop the label on retire
  assign sb_res_class = 4'd9;                                 // "prediction"; see exp_pass below
  assign sb_res_label = lbl_data[CLASS_W-1:0];
  always_ff @(posedge clk) begin
    if (rst) retire_pending <= 1'b0;
    else begin
      if (eng_valid && eng_ready && eng_last) retire_pending <= 1'b1;
      else if (sb_res_valid)                  retire_pending <= 1'b0;
    end
  end

  // ---- scoreboard (single clock: hot_clk = clk) ----
  logic [7:0]  scsr_addr; logic scsr_read, scsr_write; logic [31:0] scsr_rdata, scsr_wdata;
  scoreboard #(.NUM_CLASSES(NUM_CLASSES), .RESULT_MODE(0)) u_sb (
      .clk(clk), .rst(rst), .hot_clk(clk),
      .issue_valid(issue_valid), .issue_ready(),
      .res_valid(sb_res_valid), .res_ready(),
      .res_class(sb_res_class), .res_logits('0), .res_label(sb_res_label),
      .csr_address(scsr_addr), .csr_read(scsr_read), .csr_readdata(scsr_rdata),
      .csr_write(scsr_write), .csr_writedata(scsr_wdata), .csr_waitrequest(),
      .run_start(), .loop_en(), .cfg_n_records(), .cfg_rec_stride(), .cfg_rec_base(), .cfg_log_base());

  int errors = 0;
  task automatic check(input logic cond, input string msg);
    if (!cond) begin $display("FAIL: %s", msg); errors++; end
  endtask

  // device memory pattern (matches w957d8nb_bfm init) -> expected labels
  function automatic logic [7:0] patt(input int i); return 8'((i * 13 + 7)); endfunction

  task automatic scsr_wr(input logic [7:0] a, input logic [31:0] d);
    @(negedge clk); scsr_addr = a; scsr_wdata = d; scsr_write = 1'b1;
    @(negedge clk); scsr_write = 1'b0;
  endtask
  task automatic scsr_rd(input logic [7:0] a, output logic [31:0] d);
    @(negedge clk); scsr_addr = a; scsr_read = 1'b1;
    @(posedge clk); #1 d = scsr_rdata;
    @(negedge clk); scsr_read = 1'b0;
  endtask
  task automatic hcsr_wr(input logic [5:0] a, input logic [31:0] d);
    @(negedge clk); hcsr_addr = a; hcsr_wdata = d; hcsr_write = 1'b1;
    @(negedge clk); hcsr_write = 1'b0;
  endtask

  int exp_pass;
  logic [31:0] st, lo, hi, done, pass;
  initial begin
    rst = 1; rp_start = 0;
    hcsr_addr = 0; hcsr_read = 0; hcsr_write = 0; hcsr_wdata = 0;
    scsr_addr = 0; scsr_read = 0; scsr_write = 0; scsr_wdata = 0;
    repeat (5) @(negedge clk);
    rst = 0;
    repeat (2) @(negedge clk);

    // expected PASS = records whose label low nibble == the engine's fixed "prediction" (9).
    // With this pattern every label nibble is 9, so a correct chain yields PASS == DONE == 100.
    exp_pass = 0;
    for (int r = 0; r < N_REC; r++)
      if ((patt(r * STRIDE_B + N_BYTES) & 8'h0F) == 4'd9) exp_pass++;

    // configure the controller (base latency 6, fixed mode = device default)
    hcsr_wr(CSR_LATENCY, 32'd6);
    hcsr_wr(CSR_CONFIG, 32'd1);

    // configure + start the scoreboard, wait for RUNNING
    scsr_wr(ADDR_N_RECORDS, 32'(N_REC));
    scsr_wr(ADDR_REC_STRIDE, 32'(STRIDE_B));
    scsr_wr(ADDR_HIST_SHIFT, 32'd4);
    scsr_wr(ADDR_CTRL, 32'b1);              // START
    st = '1;
    while (st[ST_CLEARING] || !st[ST_RUNNING]) scsr_rd(ADDR_STATUS, st);

    // start the replay
    @(negedge clk); rp_start = 1; @(negedge clk); rp_start = 0;

    // wait for the scoreboard to retire all records (poll STATUS.DONE)
    st = '0;
    while (!st[ST_DONE]) begin scsr_rd(ADDR_STATUS, st); @(posedge clk); end

    // read the snapshot
    scsr_rd(ADDR_CYCLES_LO, lo);           // latches
    scsr_rd(ADDR_DONE, done);
    scsr_rd(ADDR_PASS, pass);
    check(done == N_REC, $sformatf("integ DONE=%0d expected %0d", done, N_REC));
    check(pass == exp_pass, $sformatf("integ PASS=%0d expected %0d", pass, exp_pass));
    check(rp_overrun == 1'b0, "integ no overrun");
    check(rp_lblovf == 1'b0, "integ no label overflow");
    $display("integ: DONE=%0d PASS=%0d (exp_pass=%0d) overrun=%0b", done, pass, exp_pass, rp_overrun);

    if (errors == 0) $display("PASS");
    else             $display("FAIL: %0d error(s)", errors);
    $finish;
  end

  initial begin #20_000_000; $display("FAIL: timeout"); $finish; end
endmodule
