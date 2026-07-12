// tb_axi4_hbmc_bridge — self-checking Verilator TB for the AXI4->HyperRAM bridge (PH3).
//
// Instantiates the REAL datapath: axi4_hbmc_bridge (DUT) -> hbmc_core -> w957d8nb_bfm. The
// hbmc<->BFM PHY wiring and CSR-latency config are reused verbatim from sim/hyperbus/tb_hyperbus.sv,
// so every readback is proven through the actual HyperRAM controller and device model, not a stub.
//
// A compact AXI4 master BFM (tasks below) issues write/read bursts with proper valid/ready
// handshakes. Test vectors cover awlen=0 (single 256-bit beat), a mid-size burst (len=3), and the
// max len=15 (16-beat) burst, at several 32-byte-aligned addresses including non-zero bases, with
// address-derived per-word data so a mis-ordered or wrong-address word is caught. One case drives a
// partial WSTRB and asserts wstrb_partial_seen (detection, not RMW — v1 scope).
`timescale 1ns/1ps
module tb_axi4_hbmc_bridge;
  import hyperbus_pkg::*;

  localparam int LAT       = 6;
  localparam int DATA_W    = 256;
  localparam int WORDS_PB  = DATA_W / 16;   // 16 hbmc words per AXI beat
  localparam int MEM_BYTES = 65536;

  logic clk = 0; always #5 clk = ~clk;
  logic rst;

  // ---- hbmc CSR (driven by the TB, same as tb_hyperbus) ----
  logic [5:0]  csr_address; logic csr_read, csr_write;
  logic [31:0] csr_readdata, csr_writedata;

  // ---- AXI4 master -> bridge slave ----
  logic [4:0]   awid;  logic [31:0] awaddr; logic [7:0] awlen; logic [2:0] awsize; logic [1:0] awburst;
  logic         awvalid, awready;
  logic [255:0] wdata; logic [31:0] wstrb; logic wlast, wvalid, wready;
  logic [4:0]   bid;   logic [1:0]  bresp; logic bvalid, bready;
  logic [1:0]   arid;  logic [31:0] araddr; logic [7:0] arlen; logic [2:0] arsize; logic [1:0] arburst;
  logic         arvalid, arready;
  logic [1:0]   rid;   logic [255:0] rdata; logic [1:0] rresp; logic rlast, rvalid, rready;

  // ---- Avalon-MM: bridge master <-> hbmc slave ----
  logic [22:0] av_address; logic [7:0] av_burstcount;
  logic        av_read, av_write; logic [15:0] av_writedata, av_readdata;
  logic        av_readdatavalid, av_waitrequest;

  // ---- sticky status ----
  logic wstrb_partial_seen, hi_addr_seen;

  // ---- HyperBus PHY nets (resolved in the TB, exactly as tb_hyperbus.sv) ----
  logic       core_cs_n, core_dq_oe, core_rwds_o, core_rwds_oe;
  logic [7:0] core_dq_o;
  logic       bfm_dq_oe, bfm_rwds_o, bfm_rwds_oe;
  logic [7:0] bfm_dq_o;
  logic       collision;
  wire [7:0] dq_bus   = core_dq_oe   ? core_dq_o   : (bfm_dq_oe   ? bfm_dq_o   : 8'h00);
  wire       rwds_bus = core_rwds_oe ? core_rwds_o : (bfm_rwds_oe ? bfm_rwds_o : 1'b0);

  // ---- DUT: AXI4 -> Avalon bridge ----
  axi4_hbmc_bridge dut (
      .clk(clk), .rst(rst),
      .awid(awid), .awaddr(awaddr), .awlen(awlen), .awsize(awsize), .awburst(awburst),
      .awvalid(awvalid), .awready(awready),
      .wdata(wdata), .wstrb(wstrb), .wlast(wlast), .wvalid(wvalid), .wready(wready),
      .bid(bid), .bresp(bresp), .bvalid(bvalid), .bready(bready),
      .arid(arid), .araddr(araddr), .arlen(arlen), .arsize(arsize), .arburst(arburst),
      .arvalid(arvalid), .arready(arready),
      .rid(rid), .rdata(rdata), .rresp(rresp), .rlast(rlast), .rvalid(rvalid), .rready(rready),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read), .av_write(av_write),
      .av_writedata(av_writedata), .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid),
      .av_waitrequest(av_waitrequest),
      .wstrb_partial_seen(wstrb_partial_seen), .hi_addr_seen(hi_addr_seen));

  // ---- real HyperRAM controller ----
  hbmc_core #(.LAT_BEATS_DEFAULT(LAT)) u_hbmc (
      .clk(clk), .rst(rst),
      .csr_address(csr_address), .csr_read(csr_read), .csr_readdata(csr_readdata),
      .csr_write(csr_write), .csr_writedata(csr_writedata),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read), .av_write(av_write),
      .av_writedata(av_writedata), .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid),
      .av_waitrequest(av_waitrequest),
      .hb_cs_n(core_cs_n), .hb_dq_o(core_dq_o), .hb_dq_oe(core_dq_oe), .hb_dq_i(dq_bus),
      .hb_rwds_o(core_rwds_o), .hb_rwds_oe(core_rwds_oe), .hb_rwds_i(rwds_bus),
      .hb_capture_delay());

  // ---- real device model ----
  w957d8nb_bfm #(.MEM_BYTES(MEM_BYTES), .LAT_BEATS(LAT), .ROW_BYTES(128), .ROW_PENALTY(4)) u_bfm (
      .clk(clk), .cs_n(core_cs_n), .dq_i(dq_bus), .rwds_i(rwds_bus),
      .dq_o(bfm_dq_o), .dq_oe(bfm_dq_oe), .rwds_o(bfm_rwds_o), .rwds_oe(bfm_rwds_oe),
      .collision(collision));

  int errors = 0;
  task automatic check(input logic cond, input string msg);
    if (!cond) begin $display("FAIL: %s", msg); errors++; end
  endtask

  // ---- hbmc CSR access (to program latency to match the BFM), from tb_hyperbus ----
  task automatic csr_wr(input logic [5:0] a, input logic [31:0] d);
    @(negedge clk); csr_address = a; csr_writedata = d; csr_write = 1'b1;
    @(negedge clk); csr_write = 1'b0;
  endtask

  // ---- address-derived data: distinctive per WORD address (catches misorder / wrong address) ----
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

  // ---- AXI4 master BFM ----
  task automatic axi_write(input logic [4:0] id, input logic [31:0] addr, input logic [7:0] len,
                           input logic [255:0] beats [], input logic [31:0] strb,
                           output logic [4:0] bid_o);
    int nb; nb = int'(len) + 1;
    // AW
    @(negedge clk);
    awid = id; awaddr = addr; awlen = len; awsize = 3'd5; awburst = 2'b01; awvalid = 1'b1;
    forever begin @(posedge clk); if (awready) break; end
    @(negedge clk); awvalid = 1'b0;
    // W beats
    for (int b = 0; b < nb; b++) begin
      @(negedge clk);
      wdata = beats[b]; wstrb = strb; wlast = (b == nb-1); wvalid = 1'b1;
      forever begin @(posedge clk); if (wready) break; end
    end
    @(negedge clk); wvalid = 1'b0; wlast = 1'b0;
    // B
    @(negedge clk); bready = 1'b1;
    forever begin @(posedge clk); if (bvalid) break; end
    bid_o = bid;
    @(negedge clk); bready = 1'b0;
  endtask

  task automatic axi_read(input logic [1:0] id, input logic [31:0] addr, input logic [7:0] len,
                          output logic [255:0] beats [], output logic [1:0] rid_o,
                          output logic rlast_last);
    int nb; nb = int'(len) + 1;
    beats = new[nb];
    rlast_last = 1'b0;
    // AR
    @(negedge clk);
    arid = id; araddr = addr; arlen = len; arsize = 3'd5; arburst = 2'b01; arvalid = 1'b1;
    forever begin @(posedge clk); if (arready) break; end
    @(negedge clk); arvalid = 1'b0;
    // R beats
    @(negedge clk); rready = 1'b1;
    for (int b = 0; b < nb; b++) begin
      forever begin @(posedge clk); if (rvalid) break; end
      beats[b] = rdata; rid_o = rid; if (b == nb-1) rlast_last = rlast;
      @(negedge clk);
    end
    rready = 1'b0;
  endtask

  // ---- per-case runner: write a burst, read it back, check every word + id echo ----
  task automatic run_case(input string name, input logic [4:0] wid, input logic [1:0] rd_id,
                          input logic [31:0] addr, input logic [7:0] len);
    logic [255:0] wb [], rb [];
    logic [4:0] bid_o; logic [1:0] rid_o; logic rlast_o;
    logic [22:0] base_word; int nb, e0;
    e0 = errors;
    base_word = addr[23:1];
    nb = int'(len) + 1;
    wb = new[nb];
    for (int b = 0; b < nb; b++) wb[b] = beatval(base_word, b);

    axi_write(wid, addr, len, wb, {32{1'b1}}, bid_o);
    check(bid_o == wid, $sformatf("%s: bid %h != awid %h", name, bid_o, wid));

    axi_read(rd_id, addr, len, rb, rid_o, rlast_o);
    check(rid_o == rd_id, $sformatf("%s: rid %h != arid %h", name, rid_o, rd_id));
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

  logic [255:0] pb [];
  logic [4:0]   pbid;
  initial begin
    // idle the AXI + CSR buses
    rst = 1; collision = 0;
    csr_address = 0; csr_read = 0; csr_write = 0; csr_writedata = 0;
    awid = 0; awaddr = 0; awlen = 0; awsize = 0; awburst = 0; awvalid = 0;
    wdata = 0; wstrb = 0; wlast = 0; wvalid = 0; bready = 0;
    arid = 0; araddr = 0; arlen = 0; arsize = 0; arburst = 0; arvalid = 0;
    rready = 0;
    repeat (4) @(negedge clk);
    rst = 0;

    // program hbmc to the BFM's default fixed latency of 6 beats (BFM CR0 bit3=1)
    csr_wr(CSR_LATENCY, 32'd6);
    csr_wr(CSR_CONFIG,  32'd1);   // fixed latency

    // ---- functional cases: write-then-readback through the real hbmc + BFM ----
    // Case 1: single 256-bit beat (awlen=0) at base address 0.
    run_case("len0 @0x0",      5'h11, 2'h0, 32'h0000_0000, 8'd0);
    // Case 2: two beats at a non-zero 32-byte-aligned base (word 32).
    run_case("len1 @0x40",     5'h1A, 2'h2, 32'h0000_0040, 8'd1);
    // Case 3: mid-size burst, 4 beats.
    run_case("len3 @0x1000",   5'h05, 2'h1, 32'h0000_1000, 8'd3);
    // Case 4: maximum burst, 16 beats (256 words).
    run_case("len15 @0x2000",  5'h1F, 2'h3, 32'h0000_2000, 8'd15);

    // ---- WSTRB partial-write DETECTION ----
    check(wstrb_partial_seen == 1'b0, "wstrb_partial_seen set before the partial-write case");
    pb = new[1];
    pb[0] = beatval(23'(32'h0080 >> 1), 0);
    axi_write(5'h07, 32'h0000_0080, 8'd0, pb, 32'h0000_FFFF, pbid);  // partial byte-enable mask
    check(wstrb_partial_seen == 1'b1, "wstrb_partial_seen NOT raised on partial WSTRB");
    if (wstrb_partial_seen) $display("PASS: partial-WSTRB detection (wstrb_partial_seen raised)");

    // ---- WSTRB WRITE-COMBINING (v3): 8 partial (4-byte) writes to ONE 32-byte beat are buffered and
    //      flushed as a single full-strobe beat write, so the beat is written ONCE. The read triggers
    //      the flush (read-your-writes) and returns the fully-assembled beat. This is the fix for the
    //      on-silicon "beat written more than once" corruption. ----
    begin
      logic [255:0] part [], rb2 []; logic [4:0] bo; logic [1:0] ro; logic rl;
      logic [22:0] bw; int e0;
      e0 = errors; bw = 23'(32'h0000_3000 >> 1);
      part = new[1];
      // 8 partial writes, each carrying one 32-bit (4-byte) group of the target beatval(bw,0):
      for (int g = 0; g < 8; g++) begin
        logic [31:0] strb;
        part[0] = beatval(bw, 0);              // full pattern; wstrb selects this group's 4 bytes
        strb    = 32'h0000_000F << (4*g);      // bytes [4g .. 4g+3]
        axi_write(5'h10 + 5'(g), 32'h0000_3000, 8'd0, part, strb, bo);
      end
      axi_read(2'h1, 32'h0000_3000, 8'd0, rb2, ro, rl);   // flush + read the assembled beat
      for (int i = 0; i < WORDS_PB; i++)
        if (rb2[0][16*i +: 16] !== wordval(bw + 23'(i))) begin
          $display("FAIL: combine word %0d got %h exp %h", i, rb2[0][16*i +: 16], wordval(bw+23'(i)));
          errors++;
        end
      if (errors == e0) $display("PASS: write-combining (8 partial writes -> one full beat, all 16 words correct)");

      // Two ADJACENT beats, each assembled from partial writes, must stay independent and correct.
      e0 = errors;
      for (int b = 0; b < 2; b++) begin
        logic [22:0] bwb; bwb = 23'((32'h0000_4000 + b*32) >> 1);
        for (int g = 0; g < 8; g++) begin
          logic [31:0] strb;
          part[0] = beatval(bwb, 0);
          strb    = 32'h0000_000F << (4*g);
          axi_write(5'h18, 32'h0000_4000 + b*32, 8'd0, part, strb, bo);   // AF_LOAD flush between beats
        end
      end
      for (int b = 0; b < 2; b++) begin
        logic [22:0] bwb; bwb = 23'((32'h0000_4000 + b*32) >> 1);
        axi_read(2'h1, 32'h0000_4000 + b*32, 8'd0, rb2, ro, rl);
        for (int i = 0; i < WORDS_PB; i++)
          if (rb2[0][16*i +: 16] !== wordval(bwb + 23'(i))) begin
            $display("FAIL: combine-2beat b%0d word %0d", b, i); errors++; end
      end
      if (errors == e0) $display("PASS: write-combining across adjacent beats (independent, correct)");
    end

    check(hi_addr_seen == 1'b0, "hi_addr_seen unexpectedly set (all test addresses are < 16 MB)");

    if (errors == 0) $display("ALL AXI4-HBMC BRIDGE TBS PASSED");
    else             $display("FAIL: %0d error(s)", errors);
    $finish;
  end

  initial begin #500000; $display("FAIL: timeout"); $finish; end
endmodule
