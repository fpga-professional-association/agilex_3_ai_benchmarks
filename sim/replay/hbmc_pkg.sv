// hbmc_pkg — HyperBus command/address encoding + controller CSR map (issue #13).
//
// Test infrastructure only (moved here from rtl/hyperbus/ during the CoreDLA-HyperRAM rename
// cleanup): rtl/coredla_hyperram/ now owns the production PH3 datapath (axi4_hbmc_bridge ->
// third_party/hyperram's hyperram_avalon); this package + sim/replay/hbmc_core.sv survive only as
// the golden HyperBus controller model used by sim/replay/tb_replay_integ.sv's integration TB.
// Renamed from `hyperbus_pkg` to `hbmc_pkg` specifically to kill the name collision with
// third_party/hyperram/rtl/hyperbus_pkg.sv (see docs/ph3_submodule.md) — same short name, different
// package, previously required careful filelist separation to avoid a double-`package` compile
// error; now the names simply differ.
//
// Protocol model note: this project models the 8-bit DDR HyperBus at BYTE-PER-BEAT granularity
// (one DQ byte per simulation clock — an SDR abstraction of the real DDR bus). CA is 6 beats, read
// data is RWDS-gated, latency is counted in beats. This is protocol-accurate (CA fields, latency
// handling, RWDS behavior) but NOT AC-timing-accurate — datasheet timing is closed by the PHY and
// the .sdc (docs/hyperbus.md, PLAN §3 LV6), not the sim.
`ifndef HBMC_PKG_SV
`define HBMC_PKG_SV
package hbmc_pkg;

  // ---- Command-Address (48 bits, sent MSB byte first over 6 beats) ----
  // CA[47]=R/W# (1=read), CA[46]=AddressSpace (0=memory,1=register), CA[45]=Burst (1=linear),
  // CA[44:16]=addr[31:3], CA[15:3]=reserved, CA[2:0]=addr[2:0].
  function automatic logic [47:0] pack_ca(input logic rw, input logic as_, input logic [31:0] addr);
    logic [47:0] ca;
    ca          = '0;
    ca[47]      = rw;
    ca[46]      = as_;
    ca[45]      = 1'b1;              // always linear burst
    ca[44:16]   = addr[31:3];
    ca[2:0]     = addr[2:0];
    return ca;
  endfunction

  /* verilator lint_off UNUSEDSIGNAL */
  function automatic logic [31:0] ca_addr(input logic [47:0] ca);
    return {ca[44:16], ca[2:0]};   // CA[47:45] and CA[15:3] are control/reserved, intentionally dropped
  endfunction
  /* verilator lint_on UNUSEDSIGNAL */

  // ---- controller CSR map (32-bit, byte offsets) ----
  localparam logic [5:0] CSR_CONFIG   = 6'h00;  // bit0 fixed_latency
  localparam logic [5:0] CSR_LATENCY  = 6'h04;  // base latency in beats
  localparam logic [5:0] CSR_CAPDELAY = 6'h08;  // capture-delay taps (PHY hook for #14 training)
  localparam logic [5:0] CSR_STATUS   = 6'h0C;  // bit0 busy
  localparam logic [5:0] CSR_DEV_ADDR = 6'h10;  // device register-space address
  localparam logic [5:0] CSR_DEV_WDAT = 6'h14;  // device register write data
  localparam logic [5:0] CSR_DEV_CTRL = 6'h18;  // bit0 GO (self-clearing), bit1 RW (1=read)
  localparam logic [5:0] CSR_DEV_RDAT = 6'h1C;  // device register read data (after GO completes)

  localparam int CFG_FIXED_LATENCY = 0;
  localparam int DEV_GO   = 0;
  localparam int DEV_RW   = 1;

endpackage
`endif
