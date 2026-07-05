// tb_l3_memtest_engine — self-checking Verilator testbench for l3_memtest_engine (issue #14).
//
// Wires l3_memtest_engine as hbmc_core's sole Avalon-MM master (the engine never touches hbmc_core's
// CSR bus, so the TB drives hbmc_core's CSR directly for the one-time LATENCY/CONFIG setup, exactly
// like a host would). MAX_SUBBURST is overridden small (4 words) so a modest SPAN_WORDS already
// exercises the multi-sub-burst chunking path (hbmc_core's av_burstcount is 8 bits in real designs;
// the chunking algorithm itself is size-independent, so a small override proves it without needing
// a multi-hundred-word BFM memory / long sim).
//
// Covers: (1) a clean multi-pass run over a span that needs several sub-bursts per pass, zero
// errors; (2) a single word deliberately corrupted in the BFM's backing memory between runs ->
// ERR_COUNT and ERR_ADDR must catch it exactly.
`timescale 1ns/1ps
module tb_l3_memtest_engine;
  import l3_memtest_pkg::*;

  localparam int LAT = 6;
  localparam int SMALL_SUBBURST = 4;

  logic clk = 0; always #5 clk = ~clk;
  logic rst;

  // ---- hbmc_core CSR (TB-driven directly; the engine never touches it) ----
  logic [5:0]  hbmc_csr_address; logic hbmc_csr_read, hbmc_csr_write;
  logic [31:0] hbmc_csr_readdata, hbmc_csr_writedata;

  // ---- engine CSR (host-facing) ----
  logic [5:0]  csr_address; logic csr_read, csr_write;
  logic [31:0] csr_readdata, csr_writedata;

  // ---- engine <-> hbmc_core Avalon data path ----
  logic [22:0] av_address; logic [7:0] av_burstcount;
  logic        av_read, av_write; logic [15:0] av_writedata, av_readdata;
  logic        av_readdatavalid, av_waitrequest;

  l3_memtest_engine #(.MAX_SUBBURST(SMALL_SUBBURST)) dut (
      .clk(clk), .rst(rst),
      .csr_address(csr_address), .csr_read(csr_read), .csr_readdata(csr_readdata),
      .csr_write(csr_write), .csr_writedata(csr_writedata),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read), .av_write(av_write),
      .av_writedata(av_writedata), .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid),
      .av_waitrequest(av_waitrequest));

  logic       core_cs_n, core_dq_oe, core_rwds_o, core_rwds_oe;
  logic [7:0] core_dq_o;
  logic       bfm_dq_oe, bfm_rwds_o, bfm_rwds_oe;
  logic [7:0] bfm_dq_o;

  wire [7:0] dq_bus   = core_dq_oe ? core_dq_o : (bfm_dq_oe ? bfm_dq_o : 8'h00);
  wire       rwds_bus = core_rwds_oe ? core_rwds_o : (bfm_rwds_oe ? bfm_rwds_o : 1'b0);

  hbmc_core #(.LAT_BEATS_DEFAULT(LAT)) u_hbmc (
      .clk(clk), .rst(rst),
      .csr_address(hbmc_csr_address), .csr_read(hbmc_csr_read), .csr_readdata(hbmc_csr_readdata),
      .csr_write(hbmc_csr_write), .csr_writedata(hbmc_csr_writedata),
      .av_address(av_address), .av_burstcount(av_burstcount), .av_read(av_read), .av_write(av_write),
      .av_writedata(av_writedata), .av_readdata(av_readdata), .av_readdatavalid(av_readdatavalid),
      .av_waitrequest(av_waitrequest),
      .hb_cs_n(core_cs_n), .hb_dq_o(core_dq_o), .hb_dq_oe(core_dq_oe), .hb_dq_i(dq_bus),
      .hb_rwds_o(core_rwds_o), .hb_rwds_oe(core_rwds_oe), .hb_rwds_i(rwds_bus),
      .hb_capture_delay());

  w957d8nb_bfm #(.MEM_BYTES(65536), .LAT_BEATS(LAT), .ROW_BYTES(128), .ROW_PENALTY(4)) u_bfm (
      .clk(clk), .cs_n(core_cs_n), .dq_i(dq_bus), .rwds_i(rwds_bus),
      .dq_o(bfm_dq_o), .dq_oe(bfm_dq_oe), .rwds_o(bfm_rwds_o), .rwds_oe(bfm_rwds_oe),
      .collision(1'b0));

  int errors = 0;
  task automatic check(input logic cond, input string msg);
    if (!cond) begin $display("FAIL: %s", msg); errors++; end
  endtask

  task automatic hbmc_csr_wr(input logic [5:0] a, input logic [31:0] d);
    @(negedge clk); hbmc_csr_address = a; hbmc_csr_writedata = d; hbmc_csr_write = 1'b1;
    @(negedge clk); hbmc_csr_write = 1'b0;
  endtask

  task automatic eng_csr_wr(input logic [5:0] a, input logic [31:0] d);
    @(negedge clk); csr_address = a; csr_writedata = d; csr_write = 1'b1;
    @(negedge clk); csr_write = 1'b0;
  endtask
  task automatic eng_csr_rd(input logic [5:0] a, output logic [31:0] d);
    @(negedge clk); csr_address = a; csr_read = 1'b1;
    @(posedge clk); #1 d = csr_readdata;
    @(negedge clk); csr_read = 1'b0;
  endtask

  task automatic run_and_wait(input logic [31:0] seed, input logic [31:0] base,
                              input logic [31:0] span, input logic [31:0] passes,
                              output logic [31:0] err, output logic [31:0] pdone,
                              output logic [31:0] erraddr);
    logic [31:0] status;
    int guard;
    eng_csr_wr(MT_SEED, seed);
    eng_csr_wr(MT_BASE_ADDR, base);
    eng_csr_wr(MT_SPAN_WORDS, span);
    eng_csr_wr(MT_PASS_TARGET, passes);
    eng_csr_wr(MT_CTRL, 32'd1);
    guard = 0;
    do begin
      eng_csr_rd(MT_STATUS, status);
      guard++;
      if (guard > 2_000_000) begin $display("FAIL: memtest run never finished"); $finish; end
    end while (!status[1]);
    eng_csr_rd(MT_ERR_COUNT, err);
    eng_csr_rd(MT_PASS_DONE, pdone);
    eng_csr_rd(MT_ERR_ADDR, erraddr);
  endtask

  logic [31:0] err, pdone, erraddr;
  initial begin
    rst = 1;
    hbmc_csr_address = 0; hbmc_csr_read = 0; hbmc_csr_write = 0; hbmc_csr_writedata = 0;
    csr_address = 0; csr_read = 0; csr_write = 0; csr_writedata = 0;
    repeat (4) @(negedge clk);
    rst = 0;

    hbmc_csr_wr(6'h04, 32'd6);  // CSR_LATENCY
    hbmc_csr_wr(6'h00, 32'd1);  // CSR_CONFIG (fixed)

    // ---- 1. clean run: span=20 words with a 4-word sub-burst cap -> 5 sub-bursts/pass, 3 passes ----
    run_and_wait(32'hBEEF_0001, 32'd1000, 32'd20, 32'd3, err, pdone, erraddr);
    check(pdone == 32'd3, $sformatf("clean run pass_done got %0d exp 3", pdone));
    check(err == 32'd0, $sformatf("clean run err_count got %0d exp 0", err));

    // ---- 2. corrupt one word's data the instant the read-verify phase starts (right after this
    // run's own write pass finishes, so the write can't just re-overwrite it): word address 1005
    // -> byte address 2010/2011. av_read only ever asserts during read-verify (never during the
    // write pass), so its first rising edge after START is exactly "write pass just completed".
    fork
      begin
        @(posedge clk iff (av_read === 1'b1));
        u_bfm.mem[2010] = u_bfm.mem[2010] ^ 8'hFF;
      end
    join_none
    run_and_wait(32'hBEEF_0001, 32'd1000, 32'd20, 32'd1, err, pdone, erraddr);
    check(pdone == 32'd1, $sformatf("corrupt run pass_done got %0d exp 1", pdone));
    check(err == 32'd1, $sformatf("corrupt run err_count got %0d exp 1", err));
    check(erraddr == 32'd1005, $sformatf("corrupt run err_addr got %0d exp 1005", erraddr));

    // ---- 3. regression guard: MT_ERR_ADDR (0x20) must be a genuinely distinct register from
    // MT_CTRL (0x00), not an address-width truncation alias (9 registers span 0x00-0x20, which
    // needs 6 csr_address bits -- a 5-bit port would silently wrap 0x20 to 0x00). MT_CTRL has no
    // read case of its own, so reading it must fall through to the default sentinel, not ERR_ADDR.
    begin
      logic [31:0] ctrl_readback;
      eng_csr_rd(MT_CTRL, ctrl_readback);
      check(ctrl_readback == 32'hDEAD_C0DE,
            $sformatf("MT_CTRL readback got %h, expected default sentinel DEAD_C0DE (MT_ERR_ADDR may be aliasing onto MT_CTRL's offset)",
                      ctrl_readback));
    end

    if (errors == 0) $display("PASS");
    else             $display("FAIL: %0d error(s)", errors);
    $finish;
  end

  initial begin #5000000; $display("FAIL: timeout"); $finish; end
endmodule
