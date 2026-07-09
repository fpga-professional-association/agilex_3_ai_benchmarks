// bench_pkg — shared constants for the benchmark harness RTL (issue #15).
// Register offsets mirror docs/register_map.md (the canonical source). Add shared params here as
// they appear across modules; no magic numbers scattered in the RTL (AGENTS.md).
`ifndef BENCH_PKG_SV
`define BENCH_PKG_SV
package bench_pkg;

  // ---- CSR byte offsets (docs/register_map.md) ----
  localparam logic [7:0] ADDR_CTRL       = 8'h00;
  localparam logic [7:0] ADDR_N_RECORDS  = 8'h04;
  localparam logic [7:0] ADDR_REC_STRIDE = 8'h08;
  localparam logic [7:0] ADDR_REC_BASE   = 8'h0C;
  localparam logic [7:0] ADDR_CYCLES_LO  = 8'h10;
  localparam logic [7:0] ADDR_CYCLES_HI  = 8'h14;
  localparam logic [7:0] ADDR_DONE       = 8'h18;
  localparam logic [7:0] ADDR_PASS       = 8'h1C;
  localparam logic [7:0] ADDR_LAT_MIN    = 8'h20;
  localparam logic [7:0] ADDR_LAT_MAX    = 8'h24;
  localparam logic [7:0] ADDR_STATUS     = 8'h28;
  localparam logic [7:0] ADDR_HIST_SHIFT = 8'h2C;
  localparam logic [7:0] ADDR_HIST_ADDR  = 8'h30;
  localparam logic [7:0] ADDR_HIST_DATA  = 8'h34;
  localparam logic [7:0] ADDR_LOG_BASE   = 8'h38;

  // ---- CTRL bit positions ----
  localparam int CTRL_START      = 0;
  localparam int CTRL_LOOP_EN    = 1;
  localparam int CTRL_SOFT_RESET = 2;

  // ---- STATUS bit positions ----
  localparam int ST_RUNNING   = 0;
  localparam int ST_DONE      = 1;
  localparam int ST_ISSUE_OVF = 2;  // issue timestamp FIFO overflowed (issues > MAX_INFLIGHT)
  localparam int ST_TS_UNDER  = 3;  // retire with no pending issue (pairing underflow)
  localparam int ST_CLEARING  = 4;  // counter/histogram clear FSM in progress

  // ---- histogram ----
  localparam int HIST_ENTRIES = 64;

  // ---- L0 tensor-chain microbench CSR byte offsets (issue #9; rtl/microbench/l0_tensor_chain/,
  // documented in rtl/microbench/l0_tensor_chain/README.md — a different register map than the
  // scoreboard's above, so distinct names, not distinct bit-position conventions: CTRL/STATUS bit
  // positions are deliberately kept identical to CTRL_START/CTRL_SOFT_RESET/ST_RUNNING/ST_DONE
  // above and reused as-is). ----
  // lint_off UNUSEDPARAM: this is a shared constants package (AGENTS.md), so any single consumer
  // compiles params it doesn't use — the scoreboard/replay TBs (issue #15/#16) include bench_pkg
  // but not the L0 microbench, and lint it with strict -Wall. Same reason hyperbus_pkg waives
  // UNUSEDSIGNAL. Waived at the declaration so it holds for every consumer, not per-TB run.sh.
  /* verilator lint_off UNUSEDPARAM */
  localparam logic [7:0] L0_ADDR_CTRL       = 8'h00;
  localparam logic [7:0] L0_ADDR_N_VECTORS = 8'h04;
  localparam logic [7:0] L0_ADDR_CYCLES_LO  = 8'h08;
  localparam logic [7:0] L0_ADDR_CYCLES_HI  = 8'h0C;
  localparam logic [7:0] L0_ADDR_DONE       = 8'h10;
  localparam logic [7:0] L0_ADDR_CHECKSUM   = 8'h14;
  localparam logic [7:0] L0_ADDR_STATUS     = 8'h18;
  localparam logic [7:0] L0_ADDR_N_BLOCKS   = 8'h1C;
  /* verilator lint_on UNUSEDPARAM */

  // ---- L1 PE-array microbench CSR byte offsets (issue #11; rtl/microbench/l1_pe_array/). Same
  // CTRL/STATUS bit conventions as the scoreboard/L0 above. lint_off for the same shared-package
  // reason as the L0 block. ----
  /* verilator lint_off UNUSEDPARAM */
  localparam logic [7:0] L1_ADDR_CTRL      = 8'h00;  // bit0 START (self-clearing)
  localparam logic [7:0] L1_ADDR_N_VECTORS = 8'h04;
  localparam logic [7:0] L1_ADDR_CYCLES_LO = 8'h08;  // read latches the atomic snapshot
  localparam logic [7:0] L1_ADDR_CYCLES_HI = 8'h0C;
  localparam logic [7:0] L1_ADDR_DONE      = 8'h10;  // vectors retired
  localparam logic [7:0] L1_ADDR_CHECKSUM  = 8'h14;
  localparam logic [7:0] L1_ADDR_STATUS    = 8'h18;  // bit0 RUNNING, bit1 DONE
  localparam logic [7:0] L1_ADDR_DIMS      = 8'h1C;  // {16'NUM_COLS, 16'NUM_ROWS}, compile-time
  /* verilator lint_on UNUSEDPARAM */

endpackage
`endif
