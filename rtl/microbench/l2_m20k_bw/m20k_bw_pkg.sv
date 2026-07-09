// m20k_bw_pkg — shared constants/functions for the L2 M20K aggregate-bandwidth microbench (issue
// #12, PLAN §7 L2 + §3 LV3). CSR register offsets are the canonical source for
// rtl/microbench/l2_m20k_bw/README.md's register-map table and scripts/l2_golden.py /
// scripts/run_l2.py (a test enforces they stay in sync, same convention as bench_pkg's L0/L1
// sections and l3_memtest_pkg).
`ifndef M20K_BW_PKG_SV
`define M20K_BW_PKG_SV
package m20k_bw_pkg;

  // ---- geometry knob (m20k_bw's GEOMETRY parameter) ----
  // BANKED_PER_READER: each of the R=B readers owns a dedicated M20K bank + read port; all R ports
  //   fire every cycle in parallel (PLAN §3 LV3 "good" geometry, config (a)/(c)).
  // SHARED_ROUND_ROBIN: the SAME B banks exist, but only one bank's port is driven per cycle,
  //   selected by a free-running round-robin counter — models the "all readers funnelled through
  //   one shared/arbitrated port" anti-pattern (config (b)), independent of whatever the Quartus
  //   optimizer does to physical bank count (checked separately in the fitter RAM summary per the
  //   issue's "do not let all readers hit one physical bank via optimizer merging" warning).
  // lint_off UNUSEDPARAM: a standalone `--lint-only` elaboration of m20k_bw only ever picks ONE of
  // these two constants as its default GEOMETRY parameter value, so the other is unreferenced in
  // that specific elaboration (both are exercised across the run.sh variant builds via -GGEOMETRY=).
  /* verilator lint_off UNUSEDPARAM */
  localparam bit GEOM_BANKED = 1'b0;
  localparam bit GEOM_SHARED = 1'b1;
  /* verilator lint_on UNUSEDPARAM */

  // ---- m20k_bw CSR map (word-addressed byte offsets, 32-bit registers) ----
  localparam logic [7:0] L2_ADDR_CTRL       = 8'h00;  // bit0 START (self-clearing)
  localparam logic [7:0] L2_ADDR_K          = 8'h04;  // reads per reader for this run (RW, idle/done only)
  localparam logic [7:0] L2_ADDR_CYCLES_LO  = 8'h08;  // RO elapsed cycles, low 32 (frozen once DONE)
  localparam logic [7:0] L2_ADDR_CYCLES_HI  = 8'h0C;  // RO elapsed cycles, high 32
  localparam logic [7:0] L2_ADDR_STATUS     = 8'h10;  // bit0 RUNNING, bit1 DONE
  localparam logic [7:0] L2_ADDR_CS_ADDR    = 8'h14;  // W: select bank/reader index for checksum readback
  localparam logic [7:0] L2_ADDR_CS_DATA    = 8'h18;  // R: checksum of the bank selected by CS_ADDR
  localparam logic [7:0] L2_ADDR_AGG_CS     = 8'h1C;  // R: XOR of every bank's checksum (frozen once DONE)
  localparam logic [7:0] L2_ADDR_DIMS       = 8'h20;  // R: compile-time geometry, see decode below

  // L2_ADDR_DIMS field layout (all compile-time constants, so a host can size its readback,
  // reconstruct each bank's expected content with scripts/l2_golden.py, and compute the
  // theoretical banks*bytes/port/cycle*fclk bound without hardcoding the .sof variant):
  //   [15:0]  NUM_BANKS
  //   [23:16] WORD_BYTES (DATA_WIDTH/8)
  //   [24]    GEOMETRY (0=BANKED_PER_READER, 1=SHARED_ROUND_ROBIN)
  //   [25]    OUTPUT_REG (1 = M20K dedicated output register stage enabled)
  //   [31:26] ADDR_WIDTH (per-bank depth = 2**ADDR_WIDTH; 6 bits, max 63 -- far past any realistic
  //           bank depth, see rtl/microbench/l2_m20k_bw/README.md sizing guidance)

  // ---- deterministic 32-bit xorshift fill/readback pattern (issue #12 step 3: "precompute
  // expected checksums in Python" needs an RTL-and-host-identical generator). Not a whitening/
  // crypto PRNG — only property needed is that scripts/l2_golden.py reproduces the exact same
  // sequence bit-for-bit (same idea as l0_lfsr.sv / sw/host/l0_golden.py). ----
  function automatic logic [31:0] xorshift32_next(input logic [31:0] s);
    logic [31:0] x;
    x = (s == 32'd0) ? 32'hACE1_2024 : s;  // xorshift is fixed-point at 0; never seed/observe 0
    x = x ^ (x << 13);
    x = x ^ (x >> 17);
    x = x ^ (x << 5);
    return x;
  endfunction

  // Per-bank initial seed: fold BANK_ID into a fixed base seed so every bank's content is unique
  // and reproducible (same technique as l3_memtest_pkg's per-address folding, applied per-bank
  // here since content only needs to differ bank-to-bank, not word-to-word within instantiation).
  function automatic logic [31:0] bank_seed(input int bank_id);
    return xorshift32_next(32'hB16B_00B5 ^ (32'(bank_id) * 32'h9E37_79B9));
  endfunction

endpackage
`endif
