// tb_axc3000_hyperram_axi4 — self-checking Verilator TB for the PH3 wrapper against the
// third_party/hyperram submodule's REAL PHY + golden device model (PH3 blocker #1 closed).
//
// Instantiates axc3000_hyperram_axi4 (DUT; its SPLIT hb_dq/rwds pins -- NOT the inout pads module)
// with PHY_VARIANT="GENERIC" directly against third_party/hyperram/sim/model/hyperram_model.sv,
// resolving the shared HyperBus bus exactly like third_party/hyperram/sim/tb_avalon.sv: shared
// dq_line/rwds_line wires (single active driver at a time, enforced by protocol) with a #RTT
// delayed copy fed back to the DUT's hb_dq_i/hb_rwds_i (the model samples the undelayed line; the
// RTT delay on the DUT's read path proves it recovers data source-synchronously to RWDS, not by
// round-trip coincidence -- same rationale as tb_avalon).
//
// A compact AXI4 master BFM (tasks below, adapted from sim/hyperbus/tb_axi4_hbmc_bridge.sv) issues
// INCR write bursts across the full AWLEN range (0..15, i.e. 1..16 beats of 256b = 16..256 HyperBus
// words each) at distinct 4 KB-spaced addresses, then reads each burst back and checks every word
// byte-exact against an address-derived pattern (independent of the model's power-on fill). BRESP
// and RRESP are checked OKAY on every transaction. One extra case drives a partial WSTRB and checks
// wstrb_partial_seen (detection only, v1 scope, see axi4_hbmc_bridge.sv); hi_addr_seen is checked to
// stay clear (all test addresses are < 16 MB). The TB waits for init_done (POR + CR0 programming by
// the real hyperbus_ctrl, through the real GENERIC PHY) before issuing any traffic.
//
// Prints `ALL AXC3000-HYPERRAM-AXI4 TBS PASSED` on success, $fatal on any mismatch or timeout.
// Runs under: verilator --binary --timing (5.020).
`timescale 1ns/1ps
module tb_axc3000_hyperram_axi4;
  import hyperbus_pkg::*;

  // ---- DUT/AXI geometry (matches the CoreDLA DDR contract, docs/ph3_interfaces.md) ----
  localparam int DATA_W    = 256;
  localparam int ADDR_W    = 32;
  localparam int WID_W     = 5;
  localparam int RID_W     = 2;
  localparam int LEN_W     = 8;
  localparam int WORDS_PB  = DATA_W / 16;   // 16 HyperBus words per 256-bit AXI beat
  localparam int LAT       = 6;
  localparam int DQ_WIDTH  = HB_DQ_WIDTH_DEFAULT;   // 8

  // --------------------------------------------------------------------
  // Clocking / reset. clk = word/CK rate (100 MHz); clk90 (-> wrapper's clk2x port) is a genuine
  // +90deg phase at the SAME rate for PHY_VARIANT="GENERIC" (the SDR variant repurposes this same
  // port as a true 2x-rate byte clock -- see hyperram_avalon.sv header; not exercised by this TB).
  // --------------------------------------------------------------------
  logic clk, clk90, reset_n;
  initial begin clk   = 1'b0; forever #5.0 clk   = ~clk;   end   // 100 MHz
  initial begin #2.5; clk90 = 1'b0; forever #5.0 clk90 = ~clk90; end // +90 deg

  // --------------------------------------------------------------------
  // AXI4 slave signals (drive on DUT)
  // --------------------------------------------------------------------
  logic [WID_W-1:0]    awid;  logic [ADDR_W-1:0] awaddr; logic [LEN_W-1:0] awlen;
  logic [2:0]           awsize; logic [1:0] awburst; logic awvalid, awready;
  logic [DATA_W-1:0]    wdata; logic [DATA_W/8-1:0] wstrb; logic wlast, wvalid, wready;
  logic [WID_W-1:0]     bid;   logic [1:0] bresp; logic bvalid, bready;
  logic [RID_W-1:0]     arid;  logic [ADDR_W-1:0] araddr; logic [LEN_W-1:0] arlen;
  logic [2:0]            arsize; logic [1:0] arburst; logic arvalid, arready;
  logic [RID_W-1:0]      rid;   logic [DATA_W-1:0] rdata; logic [1:0] rresp; logic rlast, rvalid, rready;

  // ---- sticky status ----
  logic wstrb_partial_seen, hi_addr_seen, init_done;

  // --------------------------------------------------------------------
  // HyperBus device pins: DUT (real PHY) side + model side + resolution (tb_avalon pattern)
  // --------------------------------------------------------------------
  logic       hb_ck, hb_ck_n, hb_cs_n, hb_rst_n;
  logic [DQ_WIDTH-1:0] wrap_dq_o;  logic wrap_dq_oe;
  logic                 wrap_rwds_o; logic wrap_rwds_oe;
  logic [DQ_WIDTH-1:0] mdl_dq_o;   logic mdl_dq_oe;
  logic                 mdl_rwds_o; logic mdl_rwds_oe;

  // Shared, resolved bus lines (single active driver at a time, enforced by protocol).
  wire [DQ_WIDTH-1:0] dq_line   = mdl_dq_oe   ? mdl_dq_o   : (wrap_dq_oe   ? wrap_dq_o   : '0);
  wire                 rwds_line = mdl_rwds_oe ? mdl_rwds_o : (wrap_rwds_oe ? wrap_rwds_o : 1'b0);

  // Round-trip DQ/RWDS flight delay (device -> master); see tb_avalon.sv for the rationale.
  localparam realtime RTT = 3.0;    // ns
  wire [DQ_WIDTH-1:0] dq_line_dly;   assign #RTT dq_line_dly   = dq_line;
  wire                 rwds_line_dly; assign #RTT rwds_line_dly = rwds_line;

  // --------------------------------------------------------------------
  // DUT: the PH3 wrapper (split HyperBus pins; PHY_VARIANT="GENERIC" for this Verilator TB)
  // --------------------------------------------------------------------
  axc3000_hyperram_axi4 #(
      .DATA_W(DATA_W), .ADDR_W(ADDR_W), .WID_W(WID_W), .RID_W(RID_W), .LEN_W(LEN_W),
      .PHY_VARIANT      ("GENERIC"),
      .DIFF_CK          (1'b1),
      .LATENCY_CLOCKS   (LAT),
      .POR_DELAY_CYCLES (0),
      .RD_PREAMBLE_SKIP (0),
      .MAX_BURST_WORDS  (0)
  ) dut (
      .clk(clk), .clk2x(clk90), .reset_n(reset_n),
      .s_axi_awid(awid), .s_axi_awaddr(awaddr), .s_axi_awlen(awlen), .s_axi_awsize(awsize),
      .s_axi_awburst(awburst), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
      .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wlast(wlast),
      .s_axi_wvalid(wvalid), .s_axi_wready(wready),
      .s_axi_bid(bid), .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
      .s_axi_arid(arid), .s_axi_araddr(araddr), .s_axi_arlen(arlen), .s_axi_arsize(arsize),
      .s_axi_arburst(arburst), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
      .s_axi_rid(rid), .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rlast(rlast),
      .s_axi_rvalid(rvalid), .s_axi_rready(rready),
      .hb_ck(hb_ck), .hb_ck_n(hb_ck_n), .hb_cs_n(hb_cs_n), .hb_rst_n(hb_rst_n),
      .hb_dq_o(wrap_dq_o), .hb_dq_oe(wrap_dq_oe), .hb_dq_i(dq_line_dly),
      .hb_rwds_o(wrap_rwds_o), .hb_rwds_oe(wrap_rwds_oe), .hb_rwds_i(rwds_line_dly),
      .init_done(init_done),
      .wstrb_partial_seen(wstrb_partial_seen), .hi_addr_seen(hi_addr_seen));

  // --------------------------------------------------------------------
  // Golden device model (third_party/hyperram, pinned submodule; DO NOT edit)
  // --------------------------------------------------------------------
  hyperram_model #(
      .DQ_WIDTH       (DQ_WIDTH),
      // MEM_WORDS sizes the model's OWN backing array AND its internal `addr` register width
      // (AW = $clog2(MEM_WORDS), sim/model/hyperram_model.sv). The page-alias regression test
      // below (2026-07-11) probes out to byte address 0x10_0000 (word address 0x8_0000 =
      // 524288) to mirror the real silicon symptom's sentinel addresses
      // (scratch/hyperram_retest/addrbit_probe.tcl) -- 1<<16 (the original value, AW=16 bits)
      // would truncate the MODEL's own address register there and manufacture a false alias
      // that is a testbench artifact, not a DUT bug. 1<<21 gives AW=21 bits, comfortable margin
      // above 0x8_0000.
      .MEM_WORDS      (1 << 21),
      .LATENCY_CLOCKS (LAT),
      .FIXED_LATENCY  (1'b1),
      .ROW_WORDS      (0),          // disable mid-burst row-crossing gaps for this TB
      .ROW_PENALTY    (4),
      .REFRESH_EVERY  (0)
  ) model (
      .hb_ck (hb_ck), .hb_ck_n (hb_ck_n), .hb_cs_n (hb_cs_n), .hb_rst_n (hb_rst_n),
      .hb_dq_i  (dq_line),  .hb_dq_ie  (wrap_dq_oe),
      .hb_dq_o  (mdl_dq_o), .hb_dq_oe  (mdl_dq_oe),
      .hb_rwds_i (rwds_line), .hb_rwds_ie (wrap_rwds_oe),
      .hb_rwds_o (mdl_rwds_o), .hb_rwds_oe (mdl_rwds_oe)
  );

  // --------------------------------------------------------------------
  // Scoreboard
  // --------------------------------------------------------------------
  int errors = 0;
  task automatic check(input logic cond, input string msg);
    if (!cond) begin $display("FAIL: %s", msg); errors++; end
  endtask

  // Address-derived data: distinctive per WORD address (catches misorder / wrong address).
  function automatic logic [15:0] wordval(input logic [22:0] wa);
    return 16'(16'(wa) * 16'h9E37 + 16'h1234);   // odd multiplier -> injective over low 16 bits
  endfunction
  function automatic logic [255:0] beatval(input logic [22:0] base_word, input int beat_i);
    logic [255:0] v;
    v = '0;
    for (int i = 0; i < WORDS_PB; i++)
      v[16*i +: 16] = wordval(base_word + 23'(beat_i*WORDS_PB + i));
    return v;
  endfunction

  // ---- AXI4 master BFM (stimulus on negedge, sample on posedge -- avoids the driver/sampler race)
  task automatic axi_write(input logic [WID_W-1:0] id, input logic [ADDR_W-1:0] addr,
                           input logic [LEN_W-1:0] len, input logic [255:0] beats [],
                           input logic [31:0] strb,
                           output logic [WID_W-1:0] bid_o, output logic [1:0] bresp_o);
    int nb; nb = int'(len) + 1;
    @(negedge clk);
    awid = id; awaddr = addr; awlen = len; awsize = 3'd5; awburst = 2'b01; awvalid = 1'b1;
    forever begin @(posedge clk); if (awready) break; end
    @(negedge clk); awvalid = 1'b0;
    for (int b = 0; b < nb; b++) begin
      @(negedge clk);
      wdata = beats[b]; wstrb = strb; wlast = (b == nb-1); wvalid = 1'b1;
      forever begin @(posedge clk); if (wready) break; end
    end
    @(negedge clk); wvalid = 1'b0; wlast = 1'b0;
    @(negedge clk); bready = 1'b1;
    forever begin @(posedge clk); if (bvalid) break; end
    bid_o = bid; bresp_o = bresp;
    @(negedge clk); bready = 1'b0;
  endtask

  task automatic axi_read(input logic [RID_W-1:0] id, input logic [ADDR_W-1:0] addr,
                          input logic [LEN_W-1:0] len, output logic [255:0] beats [],
                          output logic [RID_W-1:0] rid_o, output logic [1:0] rresp_o,
                          output logic rlast_last);
    int nb; nb = int'(len) + 1;
    beats = new[nb];
    rlast_last = 1'b0;
    @(negedge clk);
    arid = id; araddr = addr; arlen = len; arsize = 3'd5; arburst = 2'b01; arvalid = 1'b1;
    forever begin @(posedge clk); if (arready) break; end
    @(negedge clk); arvalid = 1'b0;
    @(negedge clk); rready = 1'b1;
    for (int b = 0; b < nb; b++) begin
      forever begin @(posedge clk); if (rvalid) break; end
      beats[b] = rdata; rid_o = rid; rresp_o = rresp; if (b == nb-1) rlast_last = rlast;
      @(negedge clk);
    end
    rready = 1'b0;
  endtask

  // ---- per-case runner: write an INCR burst, read it back, check every word + id echo + resp ----
  task automatic run_case(input string name, input logic [WID_W-1:0] wid, input logic [RID_W-1:0] rd_id,
                          input logic [ADDR_W-1:0] addr, input logic [LEN_W-1:0] len);
    logic [255:0] wb [], rb [];
    logic [WID_W-1:0] bid_o; logic [1:0] bresp_o;
    logic [RID_W-1:0] rid_o; logic [1:0] rresp_o; logic rlast_o;
    logic [22:0] base_word; int nb, e0;
    e0 = errors;
    base_word = addr[23:1];
    nb = int'(len) + 1;
    wb = new[nb];
    for (int b = 0; b < nb; b++) wb[b] = beatval(base_word, b);

    axi_write(wid, addr, len, wb, {32{1'b1}}, bid_o, bresp_o);
    check(bid_o == wid, $sformatf("%s: bid %h != awid %h", name, bid_o, wid));
    check(bresp_o == 2'b00, $sformatf("%s: bresp %0d != OKAY", name, bresp_o));

    axi_read(rd_id, addr, len, rb, rid_o, rresp_o, rlast_o);
    check(rid_o == rd_id, $sformatf("%s: rid %h != arid %h", name, rid_o, rd_id));
    check(rresp_o == 2'b00, $sformatf("%s: rresp %0d != OKAY", name, rresp_o));
    check(rlast_o == 1'b1, $sformatf("%s: rlast not set on final beat", name));

    for (int b = 0; b < nb; b++)
      for (int i = 0; i < WORDS_PB; i++) begin
        logic [15:0] g, ex; logic [22:0] wa;
        wa = base_word + 23'(b*WORDS_PB + i);
        g  = rb[b][16*i +: 16];
        ex = wordval(wa);
        if (g !== ex) begin
          $display("FAIL: %s beat %0d word %0d (word_addr %0d): got %h exp %h",
                   name, b, i, wa, g, ex);
          errors++;
        end
      end

    if (errors == e0)
      $display("PASS: %s  (addr=%h len=%0d beats=%0d words=%0d)", name, addr, len, nb, nb*WORDS_PB);
    else
      $display("FAIL: %s had %0d error(s)", name, errors - e0);
  endtask

  // --------------------------------------------------------------------
  // Page-alias regression (2026-07-11): reproduces the on-board silicon symptom's exact SHAPE.
  //
  // Silicon symptom: HyperRAM aliases every byte address modulo 4096 B (2048 16-bit words) --
  // sentinel writes at 0x0/0x1000/0x2000/0x5000/0x10000/0x100000 all land in the same cell per
  // in-page offset; reads alias identically (scratch/hyperram_retest/addrbit_probe.tcl,
  // gate1b.tcl's chunked bulk write/readback: every 4096 B readback chunk matched chunk 0's
  // readback byte-for-byte, 4147/22528 total match -- i.e. everything BUT chunk 0 was wrong).
  //
  // The run_case() loop above is BLIND to a page alias by construction: it writes an address and
  // immediately reads that SAME address back. Even if addr 0x1000 secretly lands in the same
  // physical cell as addr 0x0, reading 0x1000 right after writing it still returns what was just
  // written -- the alias only surfaces when a LATER, DIFFERENT address's write clobbers it before
  // the read happens. This phase writes ALL pages first, THEN reads ALL pages back, matching the
  // real bulk-write-then-bulk-read reproduction that caught the bug on hardware.
  // --------------------------------------------------------------------
  localparam int ALIAS_NPAGES          = 6;
  localparam int ALIAS_PAGE_BYTES      = 4096;                              // = symptom's alias period
  localparam int ALIAS_BURST_BYTES     = 16 * (DATA_W / 8);                 // 16 beats x 32 B = 512 B
  localparam int ALIAS_BURSTS_PER_PAGE = ALIAS_PAGE_BYTES / ALIAS_BURST_BYTES; // 8

  logic [31:0] alias_page_addr [ALIAS_NPAGES];
  int alias_errors_base;

  // Per-(page,word-index) tag: page number in bits[15:12] (0..5, needs only 3 bits, given room),
  // in-page word index (0..2047, needs 11 bits) in bits[11:0]. A genuine 4KB alias makes page P's
  // readback equal page 0's write at the same in-page index -- i.e. the got value's page-tag
  // nibble reads back as 0 instead of P. That signature is printed on every mismatch below.
  function automatic logic [15:0] alias_wordval(input int page, input int widx);
    return {4'(page), 12'(widx)};
  endfunction

  task automatic alias_write_page(input int page, input logic [31:0] base);
    for (int burst = 0; burst < ALIAS_BURSTS_PER_PAGE; burst++) begin
      logic [255:0] beats [];
      logic [WID_W-1:0] bid_o; logic [1:0] bresp_o;
      beats = new[16];
      for (int b = 0; b < 16; b++) begin
        logic [255:0] v; v = '0;
        for (int i = 0; i < WORDS_PB; i++) begin
          int widx; widx = burst*16*WORDS_PB + b*WORDS_PB + i;
          v[16*i +: 16] = alias_wordval(page, widx);
        end
        beats[b] = v;
      end
      axi_write(WID_W'(5'(page + 1)), base + 32'(burst * ALIAS_BURST_BYTES), LEN_W'(15), beats,
                {32{1'b1}}, bid_o, bresp_o);
      check(bresp_o == 2'b00, $sformatf("alias-write page%0d burst%0d: bresp != OKAY", page, burst));
    end
  endtask

  task automatic alias_read_check_page(input int page, input logic [31:0] base);
    for (int burst = 0; burst < ALIAS_BURSTS_PER_PAGE; burst++) begin
      logic [255:0] rb []; logic [RID_W-1:0] rid_o; logic [1:0] rresp_o; logic rlast_o;
      axi_read(RID_W'(page[1:0]), base + 32'(burst * ALIAS_BURST_BYTES), LEN_W'(15), rb,
                rid_o, rresp_o, rlast_o);
      check(rresp_o == 2'b00, $sformatf("alias-read page%0d burst%0d: rresp != OKAY", page, burst));
      for (int b = 0; b < 16; b++)
        for (int i = 0; i < WORDS_PB; i++) begin
          int widx; logic [15:0] g, ex;
          widx = burst*16*WORDS_PB + b*WORDS_PB + i;
          g  = rb[b][16*i +: 16];
          ex = alias_wordval(page, widx);
          if (g !== ex) begin
            $display("ALIAS-FAIL: page%0d burst%0d beat%0d word%0d (widx %0d): got %h exp %h (got's page-tag nibble=%0d)",
                      page, burst, b, i, widx, g, ex, g[15:12]);
            errors++;
          end
        end
    end
  endtask

  logic [255:0] pb [];
  logic [WID_W-1:0] pbid; logic [1:0] pbresp;
  int guard;
  initial begin
    reset_n = 1'b0;
    awid = 0; awaddr = 0; awlen = 0; awsize = 0; awburst = 0; awvalid = 0;
    wdata = 0; wstrb = 0; wlast = 0; wvalid = 0; bready = 0;
    arid = 0; araddr = 0; arlen = 0; arsize = 0; arburst = 0; arvalid = 0;
    rready = 0;
    repeat (5) @(posedge clk);
    @(negedge clk);
    reset_n = 1'b1;

    // Wait for POR init + CR0 programming (through the real controller + GENERIC PHY) to complete.
    guard = 0;
    while (!init_done && guard < 200000) begin @(posedge clk); guard = guard + 1; end
    if (!init_done) begin
      $display("[%0t] FATAL: init_done never asserted", $time);
      errors = errors + 1;
    end else begin
      $display("[%0t] init_done asserted", $time);
    end
    repeat (4) @(posedge clk);

    // ---- INCR write bursts across the full AWLEN range (0..15), byte-exact read-back ----
    for (int len = 0; len <= 15; len++) begin
      run_case($sformatf("awlen%0d", len),
               WID_W'(len + 1), RID_W'(len[1:0]),
               ADDR_W'(32'h0000_1000 + len*32'h0000_1000), LEN_W'(len));
    end

    // ---- WSTRB partial-write DETECTION (v1: detect, do not RMW; see axi4_hbmc_bridge.sv) ----
    check(wstrb_partial_seen == 1'b0, "wstrb_partial_seen set before the partial-write case");
    pb = new[1];
    pb[0] = beatval(23'(32'h0000_0080 >> 1), 0);
    axi_write(WID_W'(5'h07), 32'h0000_0080, LEN_W'(0), pb, 32'h0000_FFFF, pbid, pbresp);
    check(wstrb_partial_seen == 1'b1, "wstrb_partial_seen NOT raised on partial WSTRB");
    if (wstrb_partial_seen) $display("PASS: partial-WSTRB detection (wstrb_partial_seen raised)");

    check(hi_addr_seen == 1'b0, "hi_addr_seen unexpectedly set (all test addresses are < 16 MB)");

    // ---- page-alias regression: write ALL pages first, THEN read ALL pages back (see task
    //      comment above alias_write_page/alias_read_check_page for why this ordering matters) ----
    alias_page_addr[0] = 32'h0000_0000;
    alias_page_addr[1] = 32'h0000_1000;
    alias_page_addr[2] = 32'h0000_2000;
    alias_page_addr[3] = 32'h0000_5000;
    alias_page_addr[4] = 32'h0001_0000;
    alias_page_addr[5] = 32'h0010_0000;
    alias_errors_base = errors;
    $display("---- page-alias regression: writing %0d pages of %0d B each ----",
              ALIAS_NPAGES, ALIAS_PAGE_BYTES);
    for (int p = 0; p < ALIAS_NPAGES; p++) alias_write_page(p, alias_page_addr[p]);
    $display("---- page-alias regression: reading back all %0d pages ----", ALIAS_NPAGES);
    for (int p = 0; p < ALIAS_NPAGES; p++) alias_read_check_page(p, alias_page_addr[p]);
    if (errors == alias_errors_base)
      $display("PASS: page-alias regression (%0d pages x %0d B, all independent)",
                ALIAS_NPAGES, ALIAS_PAGE_BYTES);
    else
      $display("FAIL: page-alias regression had %0d error(s)", errors - alias_errors_base);

    repeat (8) @(posedge clk);
    $display("==================================================================");
    $display("[%0t] tb_axc3000_hyperram_axi4 done: %0d error(s)", $time, errors);
    if (errors == 0) begin
      $display("ALL AXC3000-HYPERRAM-AXI4 TBS PASSED");
      $finish;
    end else begin
      $display("FAIL: %0d error(s)", errors);
      $fatal(1, "tb_axc3000_hyperram_axi4: %0d errors", errors);
    end
  end

  // Global watchdog.
  initial begin
    #3_000_000;
    $display("[%0t] TIMEOUT", $time);
    $fatal(1, "tb_axc3000_hyperram_axi4: global timeout");
  end

endmodule
