// tb_m20k_bw — self-checking Verilator testbench for m20k_bw (issue #12, PLAN §7 L2).
//
// Drives the Avalon-MM CSR slave exactly as scripts/run_l2.py would (write K, pulse CTRL.START,
// poll STATUS.DONE, read the CYCLES_LO/HI + per-bank + aggregate checksum snapshot) and checks the
// result against scripts/l2_golden.py's INDEPENDENT cycle-accurate model for the SAME
// (NUM_BANKS=4, ADDR_WIDTH=4, K=20, geometry, OUTPUT_REG). K=20 > DEPTH=16 deliberately exercises
// per-bank address wraparound. The expected constants below were generated with:
//   python3 scripts/l2_golden.py --num-banks 4 --addr-width 4 --k 20 --geometry banked --output-reg 1
//   -> cycles=22 agg_checksum=0x980CE1D8
//   python3 scripts/l2_golden.py --num-banks 4 --addr-width 4 --k 20 --geometry shared --output-reg 1
//   -> cycles=82 agg_checksum=0x980CE1D8
//   python3 scripts/l2_golden.py --num-banks 4 --addr-width 4 --k 20 --geometry banked --output-reg 0
//   -> cycles=21 agg_checksum=0x980CE1D8
// (per-bank checksums, per bank 0..3: 0x73E65CF1 0x59D5F3F0 0x63D7F011 0xD1E8BEC8 — identical
// across all three configs, since every config moves the exact same per-bank data in the exact
// same per-bank order; only elapsed cycles differ. That equality is itself a cross-config
// equivalence check: any config whose checksums differ from the other two has a real bug, not just
// a slower cycle count, the same idea as sim/l1_pe_array's variant-equivalence check.)
//
// This module (m20k_bw + m20k_bw_bank) contains no device primitives beyond an inferred RAM array
// (no DSP/M20K WYSIWYG instantiation, no IO primitives), so it is fully Verilator-simulatable per
// AGENTS.md/sim/README.md; the actual M20K packing/banking is instead checked in the Quartus
// Fitter RAM summary per the issue ("check the fitter RAM summary" / quartus/l2_m20k_bw/README.md).
//
// GEOMETRY and OUTPUT_REG are compile-time `parameter` overrides (`-GGEOMETRY=... -GOUTPUT_REG=...`
// from run.sh) so each config gets its own Verilator build, exactly like sim/l1_pe_array's
// RESET_HEAVY/ISOLATE variant builds.
`timescale 1ns/1ps
module tb_m20k_bw;
  import m20k_bw_pkg::*;

  parameter bit GEOMETRY   = GEOM_BANKED;
  parameter bit OUTPUT_REG = 1'b1;

  localparam int NUM_BANKS  = 4;
  localparam int ADDR_WIDTH = 4;   // DEPTH=16
  localparam int DATA_WIDTH = 32;
  localparam int K          = 20;  // > DEPTH: exercises per-bank address wraparound

  // expected values, selected per-variant from the golden-model runs documented above.
  localparam logic [63:0] EXP_CYCLES =
      GEOMETRY ? (OUTPUT_REG ? 64'd82 : 64'd81) : (OUTPUT_REG ? 64'd22 : 64'd21);
  localparam logic [31:0] EXP_AGG_CHECKSUM = 32'h980C_E1D8;
  localparam logic [31:0] EXP_BANK_CS[0:NUM_BANKS-1] =
      '{32'h73E6_5CF1, 32'h59D5_F3F0, 32'h63D7_F011, 32'hD1E8_BEC8};

  logic clk = 0;
  always #5 clk = ~clk;
  logic rst;

  logic [7:0]  csr_address;
  logic        csr_read, csr_write;
  logic [31:0] csr_readdata, csr_writedata;

  int errors = 0;
  task automatic check(input logic cond, input string msg);
    if (!cond) begin
      $display("FAIL: %s", msg);
      errors++;
    end
  endtask

  m20k_bw #(
      .NUM_BANKS(NUM_BANKS), .DATA_WIDTH(DATA_WIDTH), .ADDR_WIDTH(ADDR_WIDTH),
      .GEOMETRY(GEOMETRY), .OUTPUT_REG(OUTPUT_REG)
  ) dut (
      .clk(clk), .rst(rst),
      .csr_address(csr_address), .csr_read(csr_read), .csr_readdata(csr_readdata),
      .csr_write(csr_write), .csr_writedata(csr_writedata)
  );

  task automatic csr_wr(input logic [7:0] addr, input logic [31:0] data);
    @(negedge clk);
    csr_address = addr; csr_writedata = data; csr_write = 1'b1; csr_read = 1'b0;
    @(negedge clk);
    csr_write = 1'b0;
  endtask

  task automatic csr_rd(input logic [7:0] addr, output logic [31:0] data);
    @(negedge clk);
    csr_address = addr; csr_read = 1'b1; csr_write = 1'b0;
    @(negedge clk);
    data = csr_readdata;
    csr_read = 1'b0;
  endtask

  logic [31:0] dims, status, cyc_lo, cyc_hi, agg, cs;
  logic [63:0] cycles;
  int timeout;

  initial begin
    csr_address = '0; csr_read = 1'b0; csr_write = 1'b0; csr_writedata = '0;
    rst = 1'b1;
    repeat (3) @(posedge clk);
    rst = 1'b0;
    @(posedge clk);

    // ---- DIMS compile-time readback ----
    csr_rd(L2_ADDR_DIMS, dims);
    check(dims[15:0] == 16'(NUM_BANKS), "DIMS.NUM_BANKS mismatch");
    check(dims[23:16] == 8'(DATA_WIDTH / 8), "DIMS.WORD_BYTES mismatch");
    check(dims[24] == GEOMETRY, "DIMS.GEOMETRY mismatch");
    check(dims[25] == OUTPUT_REG, "DIMS.OUTPUT_REG mismatch");
    check(dims[31:26] == 6'(ADDR_WIDTH), "DIMS.ADDR_WIDTH mismatch");

    // ---- configure + run ----
    csr_wr(L2_ADDR_K, 32'(K));
    csr_wr(L2_ADDR_CTRL, 32'h1);

    timeout = 0;
    do begin
      csr_rd(L2_ADDR_STATUS, status);
      timeout++;
      check(timeout < 100000, "STATUS.DONE never set (stalled)");
    end while (!status[1] && timeout < 100000);

    check(status[0] == 1'b0, "STATUS.RUNNING still set after STATUS.DONE");

    // ---- atomic snapshot ----
    csr_rd(L2_ADDR_CYCLES_LO, cyc_lo);
    csr_rd(L2_ADDR_CYCLES_HI, cyc_hi);
    cycles = {cyc_hi, cyc_lo};
    check(cycles == EXP_CYCLES,
          $sformatf("cycles mismatch: got %0d expected %0d", cycles, EXP_CYCLES));

    csr_rd(L2_ADDR_AGG_CS, agg);
    check(agg == EXP_AGG_CHECKSUM,
          $sformatf("agg checksum mismatch: got 0x%08X expected 0x%08X", agg, EXP_AGG_CHECKSUM));

    // ---- per-bank checksum readback (CS_ADDR select -> CS_DATA) ----
    for (int b = 0; b < NUM_BANKS; b++) begin
      csr_wr(L2_ADDR_CS_ADDR, 32'(b));
      csr_rd(L2_ADDR_CS_DATA, cs);
      check(cs == EXP_BANK_CS[b],
            $sformatf("bank %0d checksum mismatch: got 0x%08X expected 0x%08X",
                      b, cs, EXP_BANK_CS[b]));
    end

    // ---- non-triviality: a broken read path that always returns 0 would still "match" a bug in
    // the golden model computing 0, so also assert the checksum is not the trivial all-zero value.
    check(agg != 32'h0, "aggregate checksum is trivially zero");

    // ---- re-run without an intervening reset (K reconfigure + START from DONE) must reproduce
    // the identical result — catches state left over from the previous run leaking into the next.
    csr_wr(L2_ADDR_K, 32'(K));
    csr_wr(L2_ADDR_CTRL, 32'h1);
    timeout = 0;
    do begin
      csr_rd(L2_ADDR_STATUS, status);
      timeout++;
      check(timeout < 100000, "second run: STATUS.DONE never set (stalled)");
    end while (!status[1] && timeout < 100000);
    csr_rd(L2_ADDR_CYCLES_LO, cyc_lo);
    csr_rd(L2_ADDR_CYCLES_HI, cyc_hi);
    cycles = {cyc_hi, cyc_lo};
    check(cycles == EXP_CYCLES, "second run: cycles differ from first run (state leaked)");
    csr_rd(L2_ADDR_AGG_CS, agg);
    check(agg == EXP_AGG_CHECKSUM, "second run: agg checksum differs from first run (state leaked)");

    if (errors == 0) $display("PASS");
    else $display("FAIL: %0d error(s)", errors);
    $finish;
  end

endmodule
