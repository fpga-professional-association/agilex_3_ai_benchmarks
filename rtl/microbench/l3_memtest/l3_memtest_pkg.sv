// l3_memtest_pkg — shared constants/functions for the L3 HyperRAM memtest + bandwidth engines
// (issue #14, PLAN §7 L3). Register offsets are the canonical source for docs/hyperbus.md's #14
// addendum and sw/host/hyperbus.py; add shared params here as they appear (AGENTS.md: no magic
// numbers scattered across the RTL).
`ifndef L3_MEMTEST_PKG_SV
`define L3_MEMTEST_PKG_SV
package l3_memtest_pkg;

  // hbmc_core's av_burstcount is 8 bits (rtl/hyperbus/hbmc_core.sv, issue #13) -> a single HyperBus
  // command can move at most 255 words (510 B). Both engines below chunk any longer span into
  // back-to-back sub-bursts of at most this many words; l3_bw_engine's header comment explains why
  // this matters for the burst-length sweep (PLAN §4/§7 L3 "controller dead cycles" risk).
  localparam int MAX_SUBBURST_WORDS = 255;

  // ---- 16-bit maximal-length Fibonacci LFSR (poly x^16+x^14+x^13+x^11+1, taps 16/14/13/11) ----
  function automatic logic [15:0] lfsr16_next(input logic [15:0] s);
    logic fb;
    fb = s[15] ^ s[13] ^ s[12] ^ s[10];
    return {s[14:0], fb};
  endfunction

  // "address-in-data": fold the target word address into the LFSR-derived pattern so a stuck
  // address bit / wrong-bank fault shows up as a data mismatch instead of aliasing onto another
  // location's expected value (classic memtest technique, issue #14 deliverables).
  function automatic logic [15:0] memtest_expected(input logic [15:0] lfsr_state,
                                                    input logic [22:0] addr);
    // fold in the full 23-bit address (low 16 bits direct, top 7 bits wrapped into the low 7) so
    // every address bit participates -- a stuck bit anywhere in the address can't alias undetected
    return lfsr_state ^ addr[15:0] ^ {9'd0, addr[22:16]};
  endfunction

  // ---- l3_memtest_engine CSR map (word-addressed byte offsets, 32-bit registers) ----
  localparam logic [4:0] MT_CTRL        = 5'h00;  // bit0 START (self-clearing)
  localparam logic [4:0] MT_SEED        = 5'h04;  // LFSR seed (0 is remapped to a fixed nonzero default)
  localparam logic [4:0] MT_BASE_ADDR   = 5'h08;  // HyperRAM word base address under test
  localparam logic [4:0] MT_SPAN_WORDS  = 5'h0C;  // words covered per write/read-verify pass
  localparam logic [4:0] MT_PASS_TARGET = 5'h10;  // number of write+read-verify passes to run
  localparam logic [4:0] MT_STATUS      = 5'h14;  // bit0 BUSY, bit1 DONE
  localparam logic [4:0] MT_PASS_DONE   = 5'h18;  // RO passes completed
  localparam logic [4:0] MT_ERR_COUNT   = 5'h1C;  // RO cumulative mismatch count (all passes)
  localparam logic [4:0] MT_ERR_ADDR    = 5'h20;  // RO word address of the first mismatch seen

  // ---- l3_bw_engine CSR map ----
  localparam logic [4:0] BW_CTRL        = 5'h00;  // bit0 START (self-clearing), bit1 DIR(1=read)
  localparam logic [4:0] BW_BASE_ADDR   = 5'h04;
  localparam logic [4:0] BW_BURST_WORDS = 5'h08;  // words per logical N-byte burst under test
  localparam logic [4:0] BW_BURST_COUNT = 5'h0C;  // consecutive logical bursts to run back-to-back
  localparam logic [4:0] BW_STATUS      = 5'h10;  // bit0 BUSY, bit1 DONE
  localparam logic [4:0] BW_CYCLES_LO   = 5'h14;  // RO elapsed cycles, low 32 (frozen once DONE)
  localparam logic [4:0] BW_CYCLES_HI   = 5'h18;  // RO elapsed cycles, high 32
  localparam logic [4:0] BW_BURSTS_DONE = 5'h1C;  // RO logical bursts completed

endpackage
`endif
