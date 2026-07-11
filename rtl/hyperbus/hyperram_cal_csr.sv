// hyperram_cal_csr — per-fit launch-trim calibration CSR for the PH3 HyperRAM subsystem.
//
// WHY THIS EXISTS (scratch/hyperram_retest/alias_diagnosis.md, coordinator addendum): the AXC3000
// DDIO_GPIO HyperRAM launch path (rtl/hyperbus/axc3000_hyperram_pads.sv, IO_VARIANT="DDIO_GPIO")
// is per-fit trim-calibrated and NOT SDC-constrained — "one refit met STA yet silently failed to
// commit" (third_party/hyperram/fpga/axc3000/README.md "Refit discipline"). With STATIC dbg_* tie-
// offs and no runtime knobs, each new ED fit inherits an uncalibrated launch timing that the CoreDLA
// ED build's 4 KB address-alias is the fit-flavour of. This module ports the submodule bench's
// REG_DBG/REG_CAL runtime-knob mechanism (third_party/hyperram/rtl/bench/hyperram_bw_test.sv words
// 13/14, driven from top.sv, poked by sysconsole/dbg_poke.tcl) into the ED as a tiny Avalon-MM CSR
// slave so each bitstream can be calibrated IN-SYSTEM over JTAG (scratch/hyperram_retest/
// calibrate_ed.tcl) with NO recompile.
//
// INVARIANCE: the REG_DBG/REG_CAL reset images are the ED's current STATIC proven tie-offs
// (DBG_RESET = 0x0007_1263 = the ERR_COUNT=0 25-run soak fix set: wrtrim=3 lat=6 prewin=1 pn=4
// contig=1 endcw=1 defuse=1; CAL_RESET = 0x0000_0002 = preamble_skip=1). So an UNcalibrated /
// never-poked bitstream behaves bit-identically to the pre-CSR ED build (the same silicon that
// aliases at 4 KB) — the CSR only ADDS the ability to retune, it changes nothing at reset.
//
// =====================================================================================
// CSR MAP (Avalon-MM agent; 32-bit registers; csr_address is a WORD address, byte offset = 4*addr).
// waitrequest tied low; reads are pipelined (readdatavalid one clock after an accepted read).
// =====================================================================================
//   byte off | word | name    | access | bits / meaning
//   ---------+------+---------+--------+-----------------------------------------------------------
//    0x00    |  0   | ID      |  R     | constant ID_MAGIC (default 0x48524331 = "HRC1"). Gates
//            |      |         |        | calibrate_ed.tcl on the right bitstream.
//    0x04    |  1   | STATUS  |  R     | sticky trip-wires: [0]=err_underrun (write-underrun pulse
//            |      |         |        | latched), [1]=wstrb_partial (a non-all-ones WSTRB / RMW),
//            |      |         |        | [2]=hi_addr (address above the HyperRAM window). ANY write
//            |      |  (clear)|  W     | to this word clears all three sticky bits.
//    0x08    |  2   | REG_DBG |  R/W   | live ctrl/PHY launch-trim knobs (drive the dbg_* bundle);
//            |      |         |        | bit map identical to the bench's REG_DBG so dbg_poke.tcl's
//            |      |         |        | field encoding is reused verbatim:
//            |      |         |        |   [3:0]=dbg_wr_lat_trim  [7:4]=dbg_lat_clocks (6/7)
//            |      |         |        |   [8]=cr0_reprog (W1 strobe, self-clearing, reads 0)
//            |      |         |        |   [9]=dbg_prewin_drive   [12:10]=dbg_prewin_n(0..7)
//            |      |         |        |   [13]=dbg_prewin_marker [14]=dbg_postwin_hold
//            |      |         |        |   [15]=dbg_ck_stretch_off
//            |      |         |        |   [16]=dbg_prewin_contig [17]=dbg_end_cwrite
//            |      |         |        |   [18]=dbg_spray_defuse
//            |      |         |        | reset = DBG_RESET
//    0x0C    |  3   | REG_CAL |  R/W   | live PHY read-eye cal image (drive the cal_* bundle; inert
//            |      |         |        | in the DDIO_GPIO branch, which has no runtime read-eye knob,
//            |      |         |        | but kept for parity + the SPLIT_PHY branch):
//            |      |         |        |   [0]=cal_capture_phase  [3:1]=cal_preamble_skip
//            |      |         |        |   [8:4]=cal_rx_tap       [9]=cal_pair_skew
//            |      |         |        | reset = CAL_RESET
// =====================================================================================
//
// RTL discipline (AGENTS.md / PLAN §3 LV1): all CSR-visible state is architectural, so it uses a
// synchronous active-high reset (not the reset-less datapath discipline); no async reset, no clock
// gating. Single clock domain (== the HyperRAM `clk`, == the JTAG master clock in ed_zero.tcl, so no
// synchronizer is needed on the quasi-static knob outputs — the host pokes only while idle, exactly
// the bench's §2 host contract). No vendor primitives: it lints + simulates cleanly under Verilator.
`ifndef HYPERRAM_CAL_CSR_SV
`define HYPERRAM_CAL_CSR_SV
module hyperram_cal_csr #(
    parameter logic [31:0] ID_MAGIC          = 32'h4852_4331,  // "HRC1"
    parameter logic [31:0] DBG_RESET         = 32'h0007_1263,  // proven ED fix set (see header)
    parameter logic [31:0] CAL_RESET         = 32'h0000_0002,  // preamble_skip=1
    parameter int unsigned CSR_AW            = 4,              // word-address bits (16 regs; 4 used)
    parameter int unsigned CAL_PREAMBLE_W    = 3,              // cal_preamble_skip width (REG_CAL[3:1])
    parameter int unsigned CAL_RX_TAP_W      = 5               // cal_rx_tap width       (REG_CAL[8:4])
) (
    input  logic                 clk,
    input  logic                 rst,            // synchronous, active-high

    // ---- Avalon-MM CSR agent (driven by the JTAG-to-Avalon master in ed_zero.tcl) ----
    input  logic [CSR_AW-1:0]    csr_address,    // WORD address (byte offset = 4*addr)
    input  logic                 csr_read,
    input  logic                 csr_write,
    input  logic [31:0]          csr_writedata,
    output logic [31:0]          csr_readdata,
    output logic                 csr_readdatavalid,
    output logic                 csr_waitrequest,

    // ---- sticky trip-wire sources (from the bridge / controller) ----
    input  logic                 sti_err_underrun,   // hyperbus_ctrl.err_underrun pulse
    input  logic                 sti_wstrb_partial,  // axi4_hbmc_bridge.wstrb_partial_seen (level)
    input  logic                 sti_hi_addr,        // axi4_hbmc_bridge.hi_addr_seen (level)

    // ---- issue #13 live controller knobs (REG_DBG decode; drive hyperbus_ctrl / hyperbus_gpio_io) ----
    output logic [3:0]           dbg_wr_lat_trim,
    output logic [3:0]           dbg_lat_clocks,
    output logic                 dbg_cr0_reprog,     // 1-clk pulse on a REG_DBG write with bit[8]=1
    output logic                 dbg_prewin_drive,
    output logic [2:0]           dbg_prewin_n,
    output logic                 dbg_prewin_marker,
    output logic                 dbg_postwin_hold,
    output logic                 dbg_ck_stretch_off,
    output logic                 dbg_prewin_contig,
    output logic                 dbg_end_cwrite,
    output logic                 dbg_spray_defuse,

    // ---- live PHY read-eye cal (REG_CAL decode; SPLIT_PHY only, inert on DDIO_GPIO) ----
    output logic                     cal_capture_phase,
    output logic [CAL_PREAMBLE_W-1:0] cal_preamble_skip,
    output logic [CAL_RX_TAP_W-1:0]   cal_rx_tap,
    output logic                     cal_pair_skew
);
    // ---- word-register indices (byte offset >> 2) ----
    localparam logic [CSR_AW-1:0] REG_ID     = CSR_AW'(0);
    localparam logic [CSR_AW-1:0] REG_STATUS = CSR_AW'(1);
    localparam logic [CSR_AW-1:0] REG_DBG    = CSR_AW'(2);
    localparam logic [CSR_AW-1:0] REG_CAL    = CSR_AW'(3);

    // ---- architectural state (CSR-visible) ----
    logic [31:0] r_dbg;
    logic [31:0] r_cal;
    logic        stk_err_underrun;
    logic        stk_wstrb_partial;
    logic        stk_hi_addr;

    // ---- REG_DBG / REG_CAL live decode (bit map == the bench's, see header) ----
    assign dbg_wr_lat_trim   = r_dbg[3:0];
    assign dbg_lat_clocks    = r_dbg[7:4];
    assign dbg_prewin_drive  = r_dbg[9];
    assign dbg_prewin_n      = r_dbg[12:10];
    assign dbg_prewin_marker = r_dbg[13];
    assign dbg_postwin_hold  = r_dbg[14];
    assign dbg_ck_stretch_off = r_dbg[15];
    assign dbg_prewin_contig = r_dbg[16];
    assign dbg_end_cwrite    = r_dbg[17];
    assign dbg_spray_defuse  = r_dbg[18];
    // dbg_cr0_reprog is a generated 1-clock pulse (below), NOT a decode of r_dbg[8].

    assign cal_capture_phase = r_cal[0];
    assign cal_preamble_skip = r_cal[3 -: CAL_PREAMBLE_W];  // bits [3:1] at width 3
    assign cal_rx_tap        = r_cal[8 -: CAL_RX_TAP_W];    // bits [8:4] at width 5
    assign cal_pair_skew     = r_cal[9];

    // ---- Avalon-MM agent: 0 wait states, pipelined read (readdatavalid one clock later) ----
    assign csr_waitrequest = 1'b0;

    // combinational readdata select (registered below into the readdatavalid contract)
    logic [31:0] rd_next;
    always_comb begin
        unique case (csr_address)
            REG_ID:     rd_next = ID_MAGIC;
            REG_STATUS: rd_next = {29'b0, stk_hi_addr, stk_wstrb_partial, stk_err_underrun};
            REG_DBG:    rd_next = {r_dbg[31:9], 1'b0, r_dbg[7:0]};   // bit8 (strobe) reads 0
            REG_CAL:    rd_next = r_cal;
            default:    rd_next = 32'h0;
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            r_dbg             <= DBG_RESET;
            r_cal             <= CAL_RESET;
            stk_err_underrun  <= 1'b0;
            stk_wstrb_partial <= 1'b0;
            stk_hi_addr       <= 1'b0;
            dbg_cr0_reprog    <= 1'b0;
            csr_readdata      <= 32'h0;
            csr_readdatavalid <= 1'b0;
        end else begin
            // Sticky trip-wire accumulate (set-dominant over the STATUS-write clear below is NOT
            // desired: a clear followed by a fresh trip in the same cycle should still trip, so OR
            // the incoming source in unconditionally and only clear when there is no new trip).
            stk_err_underrun  <= (stk_err_underrun  & ~(csr_write & (csr_address == REG_STATUS))) | sti_err_underrun;
            stk_wstrb_partial <= (stk_wstrb_partial & ~(csr_write & (csr_address == REG_STATUS))) | sti_wstrb_partial;
            stk_hi_addr       <= (stk_hi_addr       & ~(csr_write & (csr_address == REG_STATUS))) | sti_hi_addr;

            // cr0_reprog is a single-cycle pulse: default 0, asserted for exactly one clock on a
            // REG_DBG write with bit[8]=1 (host contract: poke only while the controller is idle,
            // else the ctrl edge-consumer drops the pulse — same as the bench's §2.2).
            dbg_cr0_reprog <= 1'b0;

            // ---- config writes ----
            if (csr_write) begin
                unique case (csr_address)
                    REG_DBG: begin
                        // Store all bits EXCEPT [8] (forced 0 so it reads 0); bit[8]=1 fires the pulse.
                        r_dbg <= {csr_writedata[31:9], 1'b0, csr_writedata[7:0]};
                        if (csr_writedata[8]) dbg_cr0_reprog <= 1'b1;
                    end
                    REG_CAL: r_cal <= csr_writedata;
                    default: /* ID + STATUS(clear handled above) + unused: no stored effect */ ;
                endcase
            end

            // ---- pipelined read: capture on the accept cycle, present next cycle ----
            csr_readdata      <= rd_next;
            csr_readdatavalid <= csr_read;   // waitrequest is 0, so an asserted read is accepted now
        end
    end
endmodule
`endif
