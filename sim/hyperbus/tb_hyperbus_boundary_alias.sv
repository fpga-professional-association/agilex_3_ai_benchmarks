// tb_hyperbus_boundary_alias — page-alias regression against the ACTUAL on-silicon parameter set.
//
// Context (2026-07-11 retest, docs/coredla_hyperram_hang_diagnosis.md follow-up): the real board
// build elaborates rtl/hyperbus/axc3000_hyperram_pads.sv with its DEFAULT IO_VARIANT="DDIO_GPIO"
// (quartus/ip/axc3000_hyperram_axi4/axc3000_hyperram_axi4_hw.tcl's TOP_LEVEL, and neither ed_zero.tcl
// nor that PD component's HDL_PARAMETER list override IO_VARIANT) -- NOT the "SPLIT_PHY" branch that
// rtl/hyperbus/axc3000_hyperram_axi4.sv / sim/hyperbus/tb_axc3000_hyperram_axi4.sv exercise. Both
// branches share the SAME hyperbus_avalon + hyperbus_ctrl front-end/protocol-engine RTL (only the
// bottom PHY/IO layer differs: hyperbus_gpio_io real device primitives vs hyperbus_phy dispatch), so
// that shared logic IS Verilator-testable -- but only if instantiated with the DDIO_GPIO branch's
// ACTUAL parameters. axc3000_hyperram_axi4.sv's own parameter list does NOT expose/forward
// BURST_BOUNDARY_WORDS, WR_COALESCE, WR_COALESCE_WAIT or MAX_BURST_WORDS to hyperram_avalon, so it
// silently instantiates them at hyperram_avalon's OWN defaults (BURST_BOUNDARY_WORDS=0,
// WR_COALESCE=1'b0) -- i.e. the row/boundary-chop feature and CS#-coalescing that the ACTUAL DDIO_GPIO
// branch enables (axc3000_hyperram_pads.sv: CTRL_BURST_BOUNDARY_WORDS=16'h0400=1024,
// CTRL_WR_COALESCE=1'b1) are COMPLETELY INERT in tb_axc3000_hyperram_axi4.sv. Since the issue-13
// fix-set's ROUND 3/4 features (dbg_end_cwrite / dbg_spray_defuse) are themselves gated by
// `end_cwrite_aligned = (BURST_BOUNDARY_WORDS != 0) && ...` (third_party/hyperram/rtl/hyperbus_ctrl.sv),
// a BURST_BOUNDARY_WORDS=0 test exercises NONE of that machinery -- exactly the machinery a 4KB-period
// alias (2 x BURST_BOUNDARY_WORDS=1024-word rows) would most plausibly come from. This TB closes that
// gap: it instantiates hyperram_avalon directly (submodule top; PHY_VARIANT="GENERIC" so it stays
// clean under Verilator) with EVERY parameter mirrored 1:1 from axc3000_hyperram_pads.sv's g_ddio_gpio
// branch (see that file for the citation of each value), and drives its Avalon-MM slave port directly
// (no AXI4 bridge needed -- axi4_hbmc_bridge.sv's address decode was already proven clean by
// tb_axc3000_hyperram_axi4.sv's own page-alias phase; what's untested is hyperbus_ctrl's row-chop +
// coalesce + issue-13 interaction, which lives entirely below the Avalon-MM boundary).
//
// Silicon symptom under test (scratch/hyperram_retest/addrbit_probe.tcl, gate1b.tcl): HyperRAM aliases
// every address modulo 4096 B (2048 16-bit words) -- sentinel writes at 0x0/0x1000/0x2000/0x5000/
// 0x10000/0x100000 all land in the same cell per in-page offset; reads alias identically. This TB
// writes 6 such pages (4096 B / 2048 words each) as ONE continuous linear Avalon burst per page
// (spanning exactly 2 BURST_BOUNDARY_WORDS=1024-word device rows), ALL pages first, THEN reads all
// pages back -- a write-then-immediately-read pattern would be blind to a genuine alias (see
// tb_axc3000_hyperram_axi4.sv's page-alias phase header for why).
//
// Runs under: verilator --binary --timing (5.020).
`timescale 1ns/1ps
module tb_hyperbus_boundary_alias;
  import hyperbus_pkg::*;

  localparam int unsigned DQ_WIDTH   = HB_DQ_WIDTH_DEFAULT;   // 8
  localparam int unsigned DATA_WIDTH = 2 * DQ_WIDTH;          // 16
  localparam int unsigned ADDR_WIDTH = 32;                    // matches AVS_ADDR_W in both wrappers
  localparam int unsigned LEN_WIDTH  = 16;                    // matches AVS_LEN_W in both wrappers
  localparam int unsigned REG_MSB    = ADDR_WIDTH - 1;

  // ---- DDIO_GPIO branch's silicon-tuned constants, mirrored 1:1 from
  //      rtl/hyperbus/axc3000_hyperram_pads.sv (CTRL_* parameters + LATENCY_CLOCKS default) ----
  localparam int          LATENCY_CLOCKS            = 6;
  localparam int          CTRL_MAX_BURST_WORDS       = 1024;      // = one device ROW
  localparam logic [15:0] CTRL_BURST_BOUNDARY_WORDS  = 16'h0400;  // = the 1024-word device ROW
  localparam bit          CTRL_WR_COALESCE           = 1'b1;
  localparam int          CTRL_WR_COALESCE_WAIT      = 8;
  localparam int          CTRL_WR_LAT_TRIM           = 3;

  localparam logic [15:0] HB_INIT_CR0 =
      {1'b1, 3'b000, 4'b1111, hb_clocks_to_latency_code(LATENCY_CLOCKS), 1'b1, 3'b111};

  // --------------------------------------------------------------------
  // Clocking / reset
  // --------------------------------------------------------------------
  logic clk, clk90, clk_ref, rst;
  initial begin clk    = 1'b0; forever #5.0 clk    = ~clk;    end   // 100 MHz
  initial begin #2.5; clk90  = 1'b0; forever #5.0 clk90  = ~clk90;  end // +90 deg
  initial begin clk_ref = 1'b0; forever #2.5 clk_ref = ~clk_ref; end   // (tie-off for GENERIC)

  // --------------------------------------------------------------------
  // Avalon-MM slave signals
  // --------------------------------------------------------------------
  logic [ADDR_WIDTH-1:0]   avs_address;
  logic                    avs_read, avs_write;
  logic [DATA_WIDTH-1:0]   avs_writedata;
  logic [DATA_WIDTH/8-1:0] avs_byteenable;
  logic [LEN_WIDTH-1:0]    avs_burstcount;
  logic [DATA_WIDTH-1:0]   avs_readdata;
  logic                    avs_readdatavalid;
  logic                    avs_waitrequest;
  logic                    init_done;

  // --------------------------------------------------------------------
  // HyperBus device pins: master (PHY) side + device (model) side + resolution (tb_avalon pattern)
  // --------------------------------------------------------------------
  logic                 hb_ck, hb_ck_n, hb_cs_n, hb_rst_n;
  logic [DQ_WIDTH-1:0]  phy_dq_o;   logic phy_dq_oe;
  logic                 phy_rwds_o; logic phy_rwds_oe;
  logic [DQ_WIDTH-1:0]  mdl_dq_o;   logic mdl_dq_oe;
  logic                 mdl_rwds_o; logic mdl_rwds_oe;

  wire [DQ_WIDTH-1:0] dq_line   = mdl_dq_oe   ? mdl_dq_o   : (phy_dq_oe   ? phy_dq_o   : '0);
  wire                rwds_line = mdl_rwds_oe ? mdl_rwds_o : (phy_rwds_oe ? phy_rwds_o : 1'b0);

  localparam realtime RTT = 3.0;    // ns
  wire [DQ_WIDTH-1:0] dq_line_dly;   assign #RTT dq_line_dly   = dq_line;
  wire                rwds_line_dly; assign #RTT rwds_line_dly = rwds_line;

  // --------------------------------------------------------------------
  // DUT: hyperram_avalon (submodule top), parameterized to match axc3000_hyperram_pads.sv's
  // g_ddio_gpio branch exactly (see file header). PHY_VARIANT="GENERIC" is the only deliberate
  // substitution (Verilator-clean stand-in for the real device-primitive hyperbus_gpio_io) --
  // hyperbus_avalon/hyperbus_ctrl (everything this test is actually probing) are untouched.
  // --------------------------------------------------------------------
  hyperram_avalon #(
    .LATENCY_CLOCKS       (LATENCY_CLOCKS),
    .FIXED_LATENCY        (1'b1),
    .MAX_BURST_WORDS      (CTRL_MAX_BURST_WORDS),
    .BURST_BOUNDARY_WORDS (CTRL_BURST_BOUNDARY_WORDS),
    .WR_COMMIT_READ       (1'b0),
    .WR_COALESCE          (CTRL_WR_COALESCE),
    .WR_COALESCE_WAIT     (CTRL_WR_COALESCE_WAIT),
    .PROGRAM_CR           (1'b1),
    .POR_DELAY_CYCLES     (0),
    .INIT_CR0             (HB_INIT_CR0),
    .PHY_VARIANT          ("GENERIC"),
    .DIFF_CK              (1'b1),
    .RD_PREAMBLE_SKIP     (0)
  ) dut (
    .clk (clk), .clk90 (clk90), .clk_ref (clk_ref), .rst (rst),
    .cal_capture_phase (1'b0), .cal_preamble_skip (3'd0), .cal_rx_tap (5'd0), .cal_pair_skew (1'b0),
    .avs_address       (avs_address),
    .avs_read          (avs_read),
    .avs_write         (avs_write),
    .avs_writedata     (avs_writedata),
    .avs_byteenable    (avs_byteenable),
    .avs_burstcount    (avs_burstcount),
    .avs_readdata      (avs_readdata),
    .avs_readdatavalid (avs_readdatavalid),
    .avs_waitrequest   (avs_waitrequest),
    .hb_ck (hb_ck), .hb_ck_n (hb_ck_n), .hb_cs_n (hb_cs_n), .hb_rst_n (hb_rst_n),
    .hb_dq_o (phy_dq_o), .hb_dq_oe (phy_dq_oe), .hb_dq_i (dq_line_dly),
    .hb_rwds_o (phy_rwds_o), .hb_rwds_oe (phy_rwds_oe), .hb_rwds_i (rwds_line_dly),
    .init_done (init_done), .err_underrun (/* unused */), .dbg_bus (),
    // issue #13 fix-set: EXACT same tie-off values as axc3000_hyperram_pads.sv's g_ddio_gpio branch
    // (and rtl/hyperbus/axc3000_hyperram_axi4.sv's g_split_phy branch -- both wrappers use identical
    // values per commit f42ba37).
    .dbg_wr_lat_trim   (4'(CTRL_WR_LAT_TRIM)),
    .dbg_lat_clocks    (4'(LATENCY_CLOCKS)),
    .dbg_cr0_reprog    (1'b0),
    .dbg_prewin_drive  (1'b1),
    .dbg_prewin_n      (3'd4),
    .dbg_prewin_marker (1'b0),
    .dbg_postwin_hold  (1'b0),
    .dbg_prewin_contig (1'b1),
    .dbg_end_cwrite    (1'b1),
    .dbg_spray_defuse  (1'b1),
    .wrap_en           (1'b0)
  );

  // --------------------------------------------------------------------
  // Golden device model. MEM_WORDS bumped from the submodule TBs' usual 1<<16 (AW=16 bits) so the
  // model's OWN internal `addr` register (AW = $clog2(MEM_WORDS), sim/model/hyperram_model.sv) can
  // represent word address 0x8_0000 (byte 0x10_0000) -- the widest sentinel in the real silicon
  // symptom -- without truncating internally and manufacturing a false alias that would be a
  // testbench artifact, not a DUT bug.
  // --------------------------------------------------------------------
  hyperram_model #(
    .DQ_WIDTH       (DQ_WIDTH),
    .MEM_WORDS      (1 << 21),
    .LATENCY_CLOCKS (LATENCY_CLOCKS),
    .FIXED_LATENCY  (1'b1),
    .ROW_WORDS      (0),          // mid-burst row-crossing gap timing is orthogonal to this test
    .ROW_PENALTY    (4),
    .REFRESH_EVERY  (0)
  ) model (
    .hb_ck (hb_ck), .hb_ck_n (hb_ck_n), .hb_cs_n (hb_cs_n), .hb_rst_n (hb_rst_n),
    .hb_dq_i  (dq_line),  .hb_dq_ie  (phy_dq_oe),
    .hb_dq_o  (mdl_dq_o), .hb_dq_oe  (mdl_dq_oe),
    .hb_rwds_i (rwds_line), .hb_rwds_ie (phy_rwds_oe),
    .hb_rwds_o (mdl_rwds_o), .hb_rwds_oe (mdl_rwds_oe)
  );

  // --------------------------------------------------------------------
  // Scoreboard
  // --------------------------------------------------------------------
  int errors = 0;

  localparam int unsigned CAP_MAX = 4096;
  logic [DATA_WIDTH-1:0] cap [CAP_MAX];
  int unsigned           cap_n;
  logic                  capturing;

  always @(posedge clk) begin
    if (capturing && avs_readdatavalid) begin
      if (cap_n < CAP_MAX) cap[cap_n] <= avs_readdata;
      cap_n <= cap_n + 1;
    end
  end

  // Per-(page,word-index) tag, identical scheme to tb_axc3000_hyperram_axi4.sv's page-alias phase:
  // page number in bits[15:12], in-page word index (0..2047) in bits[11:0].
  function automatic logic [15:0] page_wordval(input int page, input int widx);
    return {4'(page), 12'(widx)};
  endfunction

  // ---- Avalon transaction tasks (tb_avalon.sv pattern: drive on negedge, sample on posedge) ----
  task automatic avs_idle();
    @(negedge clk);
    avs_address    = '0;
    avs_read       = 1'b0;
    avs_write      = 1'b0;
    avs_writedata  = '0;
    avs_byteenable = '1;
    avs_burstcount = '0;
  endtask

  task automatic do_write(input logic [ADDR_WIDTH-1:0] addr, input int unsigned n,
                          input logic [DATA_WIDTH-1:0] data [$]);
    int unsigned idx, g;
    idx = 0;
    @(negedge clk);
    avs_write      = 1'b1;
    avs_read       = 1'b0;
    avs_address    = addr;
    avs_burstcount = LEN_WIDTH'(n);
    avs_byteenable = '1;
    avs_writedata  = data[0];
    g = 0;
    forever begin
      @(posedge clk);
      g = g + 1;
      if (g > 50000) begin
        $display("[%0t] HANG do_write @0x%08x idx=%0d/%0d wait=%0b", $time, addr, idx, n, avs_waitrequest);
        errors = errors + 1; break;
      end
      if (!avs_waitrequest) begin
        idx = idx + 1;
        if (idx == n) break;
        @(negedge clk);
        avs_writedata = data[idx];
      end
    end
    avs_idle();
  endtask

  task automatic do_read(input logic [ADDR_WIDTH-1:0] addr, input int unsigned n);
    int unsigned guard;
    cap_n     = 0;
    capturing = 1'b1;
    @(negedge clk);
    avs_read       = 1'b1;
    avs_write      = 1'b0;
    avs_address    = addr;
    avs_burstcount = LEN_WIDTH'(n);
    guard = 0;
    forever begin
      @(posedge clk);
      guard = guard + 1;
      if (guard > 50000) begin
        $display("[%0t] HANG do_read accept @0x%08x wait=%0b", $time, addr, avs_waitrequest);
        errors = errors + 1; break;
      end
      if (!avs_waitrequest) break;
    end
    avs_idle();
    guard = 0;
    while (cap_n < n && guard < 50000) begin
      @(posedge clk);
      guard = guard + 1;
    end
    @(posedge clk);
    capturing = 1'b0;
    if (cap_n < n) begin
      $display("[%0t] ERROR: read of %0d words at 0x%08x returned only %0d", $time, n, addr, cap_n);
      errors = errors + 1;
    end
  endtask

  // ---- page write/read-check: ONE continuous 2048-word (4096 B) linear Avalon burst per page ----
  localparam int PAGE_WORDS = 2048;   // = 4096 B = the symptom's alias period

  task automatic write_page(input int page, input logic [ADDR_WIDTH-1:0] base_word_addr);
    logic [DATA_WIDTH-1:0] wdata [$];
    wdata = {};
    for (int widx = 0; widx < PAGE_WORDS; widx++) wdata.push_back(page_wordval(page, widx));
    do_write(base_word_addr, PAGE_WORDS, wdata);
  endtask

  task automatic read_check_page(input int page, input logic [ADDR_WIDTH-1:0] base_word_addr);
    logic [15:0] ex;
    do_read(base_word_addr, PAGE_WORDS);
    for (int widx = 0; widx < PAGE_WORDS; widx++) begin
      ex = page_wordval(page, widx);
      if (cap[widx] !== ex) begin
        $display("ALIAS-FAIL: page%0d widx%0d (word_addr %0d): got %h exp %h (got's page-tag nibble=%0d)",
                  page, widx, base_word_addr + widx, cap[widx], ex, cap[widx][15:12]);
        errors++;
      end
    end
  endtask

  // --------------------------------------------------------------------
  // Main sequence
  // --------------------------------------------------------------------
  int guard;
  localparam int NPAGES = 6;
  // Byte addresses from the real silicon symptom (addrbit_probe.tcl) converted to WORD addresses
  // (>>1): 0x0, 0x1000, 0x2000, 0x5000, 0x10000, 0x100000.
  logic [ADDR_WIDTH-1:0] page_word_addr [NPAGES];

  initial begin
    rst = 1'b1;
    avs_address = '0; avs_read = 1'b0; avs_write = 1'b0; avs_writedata = '0;
    avs_byteenable = '1; avs_burstcount = '0;
    capturing = 1'b0; cap_n = 0;
    repeat (5) @(posedge clk);
    @(negedge clk);
    rst = 1'b0;

    guard = 0;
    while (!init_done && guard < 200000) begin @(posedge clk); guard = guard + 1; end
    if (!init_done) begin
      $display("[%0t] FATAL: init_done never asserted", $time);
      errors = errors + 1;
    end else begin
      $display("[%0t] init_done asserted", $time);
    end
    repeat (4) @(posedge clk);

    page_word_addr[0] = 32'h0000_0000 >> 1;
    page_word_addr[1] = 32'h0000_1000 >> 1;
    page_word_addr[2] = 32'h0000_2000 >> 1;
    page_word_addr[3] = 32'h0000_5000 >> 1;
    page_word_addr[4] = 32'h0001_0000 >> 1;
    page_word_addr[5] = 32'h0010_0000 >> 1;

    $display("---- boundary-alias regression: writing %0d pages of %0d words each (BURST_BOUNDARY_WORDS=%0d, WR_COALESCE=%0d) ----",
              NPAGES, PAGE_WORDS, CTRL_BURST_BOUNDARY_WORDS, CTRL_WR_COALESCE);
    for (int p = 0; p < NPAGES; p++) write_page(p, page_word_addr[p]);
    $display("---- boundary-alias regression: reading back all %0d pages ----", NPAGES);
    for (int p = 0; p < NPAGES; p++) read_check_page(p, page_word_addr[p]);

    repeat (8) @(posedge clk);
    $display("==================================================================");
    $display("[%0t] tb_hyperbus_boundary_alias done: %0d error(s)", $time, errors);
    if (errors == 0) begin
      $display("ALL HYPERBUS-BOUNDARY-ALIAS TBS PASSED");
      $finish;
    end else begin
      $display("FAIL: %0d error(s)", errors);
      $fatal(1, "tb_hyperbus_boundary_alias: %0d errors", errors);
    end
  end

  initial begin
    #5_000_000;
    $display("[%0t] TIMEOUT", $time);
    $fatal(1, "tb_hyperbus_boundary_alias: global timeout");
  end

endmodule
