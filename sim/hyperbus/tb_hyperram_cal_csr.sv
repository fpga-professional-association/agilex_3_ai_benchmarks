// tb_hyperram_cal_csr — self-checking Verilator TB for rtl/hyperbus/hyperram_cal_csr.sv.
//
// Proves the per-fit calibration CSR:
//   1. Reset images: REG_ID==ID_MAGIC, REG_DBG==DBG_RESET (proven fix set 0x0007_1263), REG_CAL==
//      CAL_RESET (0x2), and the dbg_*/cal_* decode outputs match that reset image bit-for-bit
//      (INVARIANCE: un-poked == the static-tie behavior).
//   2. REG_DBG write/readback with the bit[8] cr0-reprog strobe read-as-0, and the dbg_* decode
//      tracking a poke live.
//   3. cr0_reprog fires for exactly ONE clock on a bit[8]=1 write and never otherwise.
//   4. REG_CAL write updates the cal_* decode.
//   5. STATUS sticky trip-wires (err_underrun pulse / wstrb_partial / hi_addr) latch, read back on
//      STATUS, and clear on a STATUS write.
`timescale 1ns/1ps
module tb_hyperram_cal_csr;
  logic        clk = 1'b0;
  logic        rst = 1'b1;
  always #5 clk = ~clk;

  logic [3:0]  csr_address = '0;
  logic        csr_read = 1'b0, csr_write = 1'b0;
  logic [31:0] csr_writedata = '0;
  logic [31:0] csr_readdata;   // fixed 1-clock read latency; no readdatavalid/waitrequest

  logic        sti_err_underrun = 1'b0, sti_wstrb_partial = 1'b0, sti_hi_addr = 1'b0;

  logic [3:0]  dbg_wr_lat_trim, dbg_lat_clocks;
  logic        dbg_cr0_reprog, dbg_prewin_drive, dbg_prewin_marker, dbg_postwin_hold;
  logic [2:0]  dbg_prewin_n;
  logic        dbg_ck_stretch_off, dbg_prewin_contig, dbg_end_cwrite, dbg_spray_defuse;
  logic        cal_capture_phase, cal_pair_skew;
  logic [2:0]  cal_preamble_skip;
  logic [4:0]  cal_rx_tap;

  localparam logic [31:0] ID_MAGIC  = 32'h4852_4331;
  localparam logic [31:0] DBG_RESET = 32'h0007_1263;
  localparam logic [31:0] CAL_RESET = 32'h0000_0002;

  hyperram_cal_csr #(
      .ID_MAGIC(ID_MAGIC), .DBG_RESET(DBG_RESET), .CAL_RESET(CAL_RESET)
  ) dut (
      .clk(clk), .rst(rst),
      .csr_address(csr_address), .csr_read(csr_read), .csr_write(csr_write),
      .csr_writedata(csr_writedata), .csr_readdata(csr_readdata),
      .sti_err_underrun(sti_err_underrun), .sti_wstrb_partial(sti_wstrb_partial),
      .sti_hi_addr(sti_hi_addr),
      .dbg_wr_lat_trim(dbg_wr_lat_trim), .dbg_lat_clocks(dbg_lat_clocks),
      .dbg_cr0_reprog(dbg_cr0_reprog), .dbg_prewin_drive(dbg_prewin_drive),
      .dbg_prewin_n(dbg_prewin_n), .dbg_prewin_marker(dbg_prewin_marker),
      .dbg_postwin_hold(dbg_postwin_hold), .dbg_ck_stretch_off(dbg_ck_stretch_off),
      .dbg_prewin_contig(dbg_prewin_contig), .dbg_end_cwrite(dbg_end_cwrite),
      .dbg_spray_defuse(dbg_spray_defuse),
      .cal_capture_phase(cal_capture_phase), .cal_preamble_skip(cal_preamble_skip),
      .cal_rx_tap(cal_rx_tap), .cal_pair_skew(cal_pair_skew));

  int errors = 0;
  task automatic chk(input logic cond, input string msg);
    if (!cond) begin $display("FAIL: %s", msg); errors++; end
  endtask

  // Avalon-MM agent: fixed 1-clock read latency, no waitrequest (always ready), no readdatavalid.
  task automatic csr_wr(input logic [3:0] a, input logic [31:0] d);
    @(posedge clk); #1;
    csr_address = a; csr_write = 1'b1; csr_writedata = d; csr_read = 1'b0;
    @(posedge clk); #1;
    csr_write = 1'b0;
  endtask

  task automatic csr_rd(input logic [3:0] a, output logic [31:0] d);
    @(posedge clk); #1;
    csr_address = a; csr_read = 1'b1; csr_write = 1'b0;
    @(posedge clk);            // module registers csr_readdata = decode(a) here (read latency = 1)
    #1; csr_read = 1'b0;
    d = csr_readdata;          // valid exactly one clock after the accepted read
  endtask

  logic [31:0] rd;
  int cr0_pulses;

  // Count cr0_reprog pulses over the whole run (must be exactly 1 = the single strobe below).
  always @(posedge clk) if (!rst && dbg_cr0_reprog) cr0_pulses++;

  initial begin
    cr0_pulses = 0;
    repeat (4) @(posedge clk);
    #1 rst = 1'b0;
    @(posedge clk);

    // ---- 1. reset images + decode ----
    csr_rd(4'h0, rd); chk(rd === ID_MAGIC,  "REG_ID != ID_MAGIC");
    csr_rd(4'h2, rd); chk(rd === DBG_RESET, "REG_DBG reset != 0x00071263");
    csr_rd(4'h3, rd); chk(rd === CAL_RESET, "REG_CAL reset != 0x2");
    chk(dbg_wr_lat_trim === 4'd3,  "reset wr_lat_trim != 3");
    chk(dbg_lat_clocks  === 4'd6,  "reset lat_clocks != 6");
    chk(dbg_prewin_drive === 1'b1, "reset prewin_drive != 1");
    chk(dbg_prewin_n    === 3'd4,  "reset prewin_n != 4");
    chk(dbg_prewin_contig === 1'b1,"reset prewin_contig != 1");
    chk(dbg_end_cwrite  === 1'b1,  "reset end_cwrite != 1");
    chk(dbg_spray_defuse === 1'b1, "reset spray_defuse != 1");
    chk(dbg_prewin_marker === 1'b0,"reset prewin_marker != 0");
    chk(dbg_postwin_hold === 1'b0, "reset postwin_hold != 0");
    chk(dbg_ck_stretch_off === 1'b0,"reset ck_stretch_off != 0");
    chk(cal_preamble_skip === 3'd1,"reset cal_preamble_skip != 1");
    chk(cal_capture_phase === 1'b0,"reset cal_capture_phase != 0");
    chk(cal_rx_tap === 5'd0,       "reset cal_rx_tap != 0");
    chk(cal_pair_skew === 1'b0,    "reset cal_pair_skew != 0");

    // ---- 2. REG_DBG poke: change wr_lat_trim=5, lat=7, ck_stretch_off=1; keep bit8=0 ----
    // 0x0005_1273 = wrtrim=3? no: build explicitly. wrtrim=5[3:0], lat=7[7:4], prewin=1[9], pn=4[12:10],
    // ckstretchoff=1[15], contig=1[16], endcw=1[17], defuse=1[18].
    csr_wr(4'h2, 32'h0007_9275);
    csr_rd(4'h2, rd); chk(rd === 32'h0007_9275, "REG_DBG readback after poke wrong");
    chk(dbg_wr_lat_trim === 4'd5,  "poke wr_lat_trim != 5");
    chk(dbg_lat_clocks === 4'd7,   "poke lat_clocks != 7");
    chk(dbg_ck_stretch_off === 1'b1, "poke ck_stretch_off != 1");

    // ---- 3. cr0_reprog strobe: write with bit8=1; must read back with bit8=0, dbg image otherwise held ----
    csr_wr(4'h2, 32'h0007_9375);   // bit8 set on top of 0x79275 -> 0x79375
    csr_rd(4'h2, rd); chk(rd === 32'h0007_9275, "REG_DBG bit8 must read 0 (strobe self-clears)");

    // ---- 4. REG_CAL poke ----
    csr_wr(4'h3, 32'h0000_03F1);   // capture_phase=1[0], preamble=0[3:1]? build: [0]=1,[3:1]=0,[8:4]=0x1F,[9]=0
    csr_rd(4'h3, rd); chk(rd === 32'h0000_03F1, "REG_CAL readback wrong");
    chk(cal_capture_phase === 1'b1, "cal_capture_phase != 1");
    chk(cal_rx_tap === 5'h1F,        "cal_rx_tap != 0x1F");

    // ---- 5. sticky trip-wires ----
    // err_underrun single-cycle pulse.
    @(posedge clk); #1 sti_err_underrun = 1'b1;
    @(posedge clk); #1 sti_err_underrun = 1'b0;
    repeat (2) @(posedge clk);
    csr_rd(4'h1, rd); chk(rd[0] === 1'b1, "STATUS err_underrun sticky not latched");
    // hold wstrb_partial + hi_addr high (level sources).
    #1 sti_wstrb_partial = 1'b1; sti_hi_addr = 1'b1;
    repeat (2) @(posedge clk);
    csr_rd(4'h1, rd);
    chk(rd[1] === 1'b1, "STATUS wstrb_partial not latched");
    chk(rd[2] === 1'b1, "STATUS hi_addr not latched");
    // clear: deassert sources, write STATUS, verify all clear.
    #1 sti_wstrb_partial = 1'b0; sti_hi_addr = 1'b0;
    csr_wr(4'h1, 32'h0);
    repeat (2) @(posedge clk);
    csr_rd(4'h1, rd); chk(rd[2:0] === 3'b000, "STATUS sticky bits did not clear on write");

    // ---- exactly one cr0_reprog pulse over the whole run ----
    chk(cr0_pulses == 1, "cr0_reprog pulse count != 1");

    if (errors == 0) $display("ALL HYPERRAM-CAL-CSR TBS PASSED");
    else             $display("HYPERRAM-CAL-CSR TB FAILED: %0d error(s)", errors);
    $finish;
  end

  initial begin
    #100000 $display("HYPERRAM-CAL-CSR TB TIMEOUT"); $finish;
  end
endmodule
