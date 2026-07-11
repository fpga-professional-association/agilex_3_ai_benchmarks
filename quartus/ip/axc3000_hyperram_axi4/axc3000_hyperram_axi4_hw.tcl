# axc3000_hyperram_axi4_hw.tcl — Platform Designer component for the PH3 HyperRAM memory subsystem.
#
# Declares rtl/hyperbus/axc3000_hyperram_pads.sv as a Qsys/Platform Designer IP: ONE AXI4 slave (the
# byte-for-byte CoreDLA "DDR" master contract — DATA=256, ADDR=32, WRITE_ID=5, READ_ID=2), a
# two-clock domain (word-rate `clk` + 2x byte-rate `clk2x`, both required by the submodule's SDR
# PHY), an active-low reset, an `init_done` status output, and the HyperBus pin conduit exported to
# the top level. This is the drop-in replacement for the LPDDR4 EMIF (emif_0) in
# _ph3_ed/hw/qsys/ed_zero.tcl — see docs/ph3_integration.md for the exact swap recipe.
#
# COMPONENT-TOP CHOICE (documented, see rtl/hyperbus/axc3000_hyperram_axi4.sv header): the datapath
# module (axc3000_hyperram_axi4.sv) exposes SPLIT HyperBus pins (hb_dq_o/oe/i, hb_rwds_o/oe/i,
# `inout`-free) so it stays Verilator-clean and testable against a second bus driver. That is NOT
# what a Platform Designer conduit should present at the top level, though: this component's
# `hyperbus` interface is a real bidirectional (`Bidir`) conduit, matching an actual HyperRAM
# package ball. So this component instantiates the SPLIT-to-`inout` board-pads wrapper,
# rtl/hyperbus/axc3000_hyperram_pads.sv (TOP_LEVEL below), which is the one place the tristate is
# reintroduced for synthesis/board use — exactly what a board top.sv / the standalone
# quartus/ph3_hyperram_char/ char build also instantiate at the pins.
#
# NOTE (honest, updated): axc3000_hyperram_pads.sv (the TOP_LEVEL below) now defaults its
# IO_VARIANT parameter to "DDIO_GPIO" — the board-proven chain (axi4_hbmc_bridge -> hyperbus_avalon
# -> hyperbus_ctrl -> hyperbus_gpio_io) that measured 341.1/332.3 MB/s write/read at CK=175 MHz on
# THIS board (third_party/hyperram/README.md, DDR x8 row; docs/ph3_submodule.md). This superseded
# the earlier default of routing through hyperram_avalon's PHY_VARIANT dispatch (hyperbus_phy_altera,
# "SDR"/"INTEL") — that path is still present (axc3000_hyperram_pads.sv IO_VARIANT="SPLIT_PHY") for
# the sim TB (PHY_VARIANT="GENERIC") and the standalone quartus/ph3_hyperram_char/ char build
# (PHY_VARIANT="SDR"), but it was never actually reachable through THIS PD component before (no
# PHY_VARIANT parameter was ever exposed here, so it silently defaulted to axc3000_hyperram_axi4.sv's
# own "GENERIC"/sim PHY) — see the git history of this file. clk2x is wired below to the IOPLL in
# the instantiating ed_zero.tcl (quartus/coredla_hyperram_ed/platform/hw/qsys/ed_zero.tcl); board
# pinout + .sdc closure live in that platform directory too.
#
# Discover with:  qsys-script/qsys-generate --search-path="quartus/ip/axc3000_hyperram_axi4,$"
# or index with:  ip-make-ipx --source-directory=quartus/ip/axc3000_hyperram_axi4

package require -exact qsys 26.1

# -----------------------------------------------------------------------------------------------
# Module
# -----------------------------------------------------------------------------------------------
set_module_property NAME         axc3000_hyperram_axi4
set_module_property DISPLAY_NAME "AXC3000 HyperRAM AXI4 memory subsystem (PH3)"
set_module_property VERSION      1.0
set_module_property GROUP        "PH3 HyperRAM"
set_module_property DESCRIPTION  "Reduced-AXI4 slave (CoreDLA DDR contract) over the third_party/hyperram submodule's HyperBus controller + real SDR PHY (silicon-proven on this board); drop-in for the LPDDR4 EMIF."
set_module_property AUTHOR       "agilex_3_ai_benchmarks / PH3"
set_module_property EDITABLE     false
set_module_property ELABORATION_CALLBACK elaborate

# -----------------------------------------------------------------------------------------------
# Synthesis + simulation filesets. Paths are relative to THIS file
# (quartus/ip/axc3000_hyperram_axi4/ -> repo root is ../../..). Submodule package FIRST (per
# docs/ph3_submodule.md's package-name-collision note: this is the third_party/hyperram copy of
# `package hyperbus_pkg`, NEVER rtl/hyperbus/hyperbus_pkg.sv, and hbmc_core.sv is retired — the
# submodule's hyperram_avalon is the controller now). Top level is the `inout`-pin board-pads
# wrapper (see COMPONENT-TOP CHOICE above), which instantiates axc3000_hyperram_axi4.sv, which in
# turn instantiates axi4_hbmc_bridge.sv (unchanged) and the submodule's hyperram_avalon.
# -----------------------------------------------------------------------------------------------
set HR_RTL ../../../third_party/hyperram/rtl
set HR_FPGA_AXC3000 ../../../third_party/hyperram/fpga/axc3000

# Note: hyperbus_phy_generic/sdr/phy.sv + hyperram_avalon.sv are only reachable through
# axc3000_hyperram_axi4.sv's own PHY_VARIANT dispatch (the IO_VARIANT="SPLIT_PHY" generate branch
# in axc3000_hyperram_pads.sv). fpga/axc3000/hyperbus_gpio_io.sv is the IO_VARIANT="DDIO_GPIO"
# (default) branch's real I/O layer -- device-primitive-based (tennm_ph2_ddio_out, hbgpio_ck_cell),
# NOT Verilator-simulable, exactly like hyperbus_phy_altera.sv already was in this fileset.
add_fileset QUARTUS_SYNTH QUARTUS_SYNTH "" ""
set_fileset_property QUARTUS_SYNTH TOP_LEVEL axc3000_hyperram_pads
add_fileset_file hyperbus_pkg.sv          SYSTEM_VERILOG PATH ${HR_RTL}/hyperbus_pkg.sv
add_fileset_file hyperbus_ctrl.sv         SYSTEM_VERILOG PATH ${HR_RTL}/hyperbus_ctrl.sv
add_fileset_file hyperbus_avalon.sv       SYSTEM_VERILOG PATH ${HR_RTL}/if/hyperbus_avalon.sv
add_fileset_file hyperbus_phy_generic.sv  SYSTEM_VERILOG PATH ${HR_RTL}/phy/hyperbus_phy_generic.sv
add_fileset_file hyperbus_phy_sdr.sv      SYSTEM_VERILOG PATH ${HR_RTL}/phy/hyperbus_phy_sdr.sv
add_fileset_file hyperbus_phy.sv          SYSTEM_VERILOG PATH ${HR_RTL}/phy/hyperbus_phy.sv
add_fileset_file hyperram_avalon.sv       SYSTEM_VERILOG PATH ${HR_RTL}/hyperram_avalon.sv
add_fileset_file hyperbus_gpio_io.sv      SYSTEM_VERILOG PATH ${HR_FPGA_AXC3000}/hyperbus_gpio_io.sv
add_fileset_file axi4_hbmc_bridge.sv      SYSTEM_VERILOG PATH ../../../rtl/hyperbus/axi4_hbmc_bridge.sv
add_fileset_file axc3000_hyperram_axi4.sv SYSTEM_VERILOG PATH ../../../rtl/hyperbus/axc3000_hyperram_axi4.sv
add_fileset_file hyperram_cal_csr.sv      SYSTEM_VERILOG PATH ../../../rtl/hyperbus/hyperram_cal_csr.sv
add_fileset_file axc3000_hyperram_pads.sv SYSTEM_VERILOG PATH ../../../rtl/hyperbus/axc3000_hyperram_pads.sv

add_fileset SIM_VERILOG SIM_VERILOG "" ""
set_fileset_property SIM_VERILOG TOP_LEVEL axc3000_hyperram_pads
add_fileset_file hyperbus_pkg.sv          SYSTEM_VERILOG PATH ${HR_RTL}/hyperbus_pkg.sv
add_fileset_file hyperbus_ctrl.sv         SYSTEM_VERILOG PATH ${HR_RTL}/hyperbus_ctrl.sv
add_fileset_file hyperbus_avalon.sv       SYSTEM_VERILOG PATH ${HR_RTL}/if/hyperbus_avalon.sv
add_fileset_file hyperbus_phy_generic.sv  SYSTEM_VERILOG PATH ${HR_RTL}/phy/hyperbus_phy_generic.sv
add_fileset_file hyperbus_phy_sdr.sv      SYSTEM_VERILOG PATH ${HR_RTL}/phy/hyperbus_phy_sdr.sv
add_fileset_file hyperbus_phy.sv          SYSTEM_VERILOG PATH ${HR_RTL}/phy/hyperbus_phy.sv
add_fileset_file hyperram_avalon.sv       SYSTEM_VERILOG PATH ${HR_RTL}/hyperram_avalon.sv
add_fileset_file hyperbus_gpio_io.sv      SYSTEM_VERILOG PATH ${HR_FPGA_AXC3000}/hyperbus_gpio_io.sv
add_fileset_file axi4_hbmc_bridge.sv      SYSTEM_VERILOG PATH ../../../rtl/hyperbus/axi4_hbmc_bridge.sv
add_fileset_file axc3000_hyperram_axi4.sv SYSTEM_VERILOG PATH ../../../rtl/hyperbus/axc3000_hyperram_axi4.sv
add_fileset_file hyperram_cal_csr.sv      SYSTEM_VERILOG PATH ../../../rtl/hyperbus/hyperram_cal_csr.sv
add_fileset_file axc3000_hyperram_pads.sv SYSTEM_VERILOG PATH ../../../rtl/hyperbus/axc3000_hyperram_pads.sv

# -----------------------------------------------------------------------------------------------
# HDL parameters (passed through to the SV module; fixed to the CoreDLA DDR contract).
# -----------------------------------------------------------------------------------------------
# NOTE on IDs: CoreDLA's real DDR master is asymmetric (AWID=5, ARID=2). Platform Designer's axi4
# interface models a SINGLE ID width (awid==arid==bid==rid), so — exactly like the stock
# emif_data_bridge_0.s0 this replaces (S0_ID_WIDTH=5) — we present a UNIFIED 5-bit ID. CoreDLA's
# 2-bit read ID occupies the low 2 bits; the top.sv connection zero-pads it, identical to the stock
# design (see docs/ph3_integration.md). rid still echoes arid, so read-data routing is preserved.
add_parameter DATA_W            INTEGER 256 "AXI data width (bits)"
add_parameter ADDR_W            INTEGER 32  "AXI byte-address width"
add_parameter WID_W             INTEGER 5   "AXI write-ID width"
add_parameter RID_W             INTEGER 5   "AXI read-ID width (unified to 5 for the PD axi4 model)"
add_parameter LEN_W             INTEGER 8   "AXI AxLEN width"
add_parameter HB_ADDR_W         INTEGER 23  "HyperRAM word-address width"
add_parameter HB_BURST_W        INTEGER 8   "hbmc av_burstcount width"
# FIX (this session): this used to be declared as "LAT_BEATS_DEFAULT", a name that does not match
# any port on axc3000_hyperram_pads.sv (nor did it on the file this component pointed at before the
# DDIO_GPIO rewrite) -- Platform Designer forwards every HDL_PARAMETER by NAME as a Verilog
# parameter override, so that name was a real, previously-uncaught bug: any full `quartus_syn`
# elaboration of this component hits "Error (13452): module ... has no parameter named
# LAT_BEATS_DEFAULT". (Never exercised before because the only prior full quartus_syn pass, recorded
# in docs/ph3_integration.md, predates this parameter's addition.) Renamed to LATENCY_CLOCKS, which
# IS a real parameter on axc3000_hyperram_pads.sv (forwarded to both the DDIO_GPIO and SPLIT_PHY
# branches) -- same default (6), now actually wired.
add_parameter LATENCY_CLOCKS INTEGER 6   "CA1 -> data, clocks (fixed-latency, POR default); forwarded to axc3000_hyperram_pads.sv"
foreach p {DATA_W ADDR_W WID_W RID_W LEN_W HB_ADDR_W HB_BURST_W LATENCY_CLOCKS} {
    set_parameter_property $p HDL_PARAMETER true
    set_parameter_property $p AFFECTS_ELABORATION true
}
# NOTE on IDs, part 2: when this slave is SHARED by more than one AXI manager (in ed_zero.tcl both
# CoreDLA's emif_data_bridge.m0 AND the host jtag_address_span_extender drive it), the interconnect
# appends arbitration bits, so the slave ID must be >= max_manager_id + ceil(log2(num_managers)).
# The integration sets WID_W=RID_W=6 at the instance for that reason (see docs/ph3_integration.md).

# -----------------------------------------------------------------------------------------------
# Elaboration: build the interfaces with widths derived from the parameters.
# -----------------------------------------------------------------------------------------------
proc elaborate {} {
    set DATA_W    [get_parameter_value DATA_W]
    set ADDR_W    [get_parameter_value ADDR_W]
    set WID_W     [get_parameter_value WID_W]
    set RID_W     [get_parameter_value RID_W]
    set LEN_W     [get_parameter_value LEN_W]
    set STRB_W    [expr {$DATA_W / 8}]

    # ---- clock sink (word/CK rate; s_axi and reset live in this domain) ----
    add_interface clk clock end
    add_interface_port clk clk clk Input 1

    # ---- clock sink (2x byte-rate clock for the submodule SDR PHY; == hyperram_avalon .clk90 /
    #      axc3000_hyperram_axi4.sv's clk2x port. NOT yet derived from an IOPLL in this repo — the
    #      instantiating system (e.g. ed_zero.tcl) must supply a clock exactly 2x `clk`, phase-locked
    #      to it, until the PD clock-plan regen lands (docs/ph3_status.md "What remains")). ----
    add_interface clk2x clock end
    add_interface_port clk2x clk2x clk Input 1

    # ---- reset sink (active-low, synchronous, tied to clk) ----
    add_interface reset reset end
    set_interface_property reset associatedClock clk
    set_interface_property reset synchronousEdges DEASSERT
    add_interface_port reset reset_n reset_n Input 1

    # ---- AXI4 slave (CoreDLA "DDR" master target) ----
    add_interface s_axi axi4 end
    set_interface_property s_axi associatedClock clk
    set_interface_property s_axi associatedReset reset
    # This controller is single-outstanding; advertise minimal acceptance. The CoreDLA master's
    # up-to-64 issuing is absorbed by ready/valid backpressure (docs/ph3_bridge_design.md v1 lim 3).
    set_interface_property s_axi readAcceptanceCapability   1
    set_interface_property s_axi writeAcceptanceCapability  1
    set_interface_property s_axi combinedAcceptanceCapability 1
    set_interface_property s_axi readDataReorderingDepth    1

    # AW
    add_interface_port s_axi s_axi_awid    awid    Input  $WID_W
    add_interface_port s_axi s_axi_awaddr  awaddr  Input  $ADDR_W
    add_interface_port s_axi s_axi_awlen   awlen   Input  $LEN_W
    add_interface_port s_axi s_axi_awsize  awsize  Input  3
    add_interface_port s_axi s_axi_awburst awburst Input  2
    add_interface_port s_axi s_axi_awvalid awvalid Input  1
    add_interface_port s_axi s_axi_awready awready Output 1
    # W
    add_interface_port s_axi s_axi_wdata   wdata   Input  $DATA_W
    add_interface_port s_axi s_axi_wstrb   wstrb   Input  $STRB_W
    add_interface_port s_axi s_axi_wlast   wlast   Input  1
    add_interface_port s_axi s_axi_wvalid  wvalid  Input  1
    add_interface_port s_axi s_axi_wready  wready  Output 1
    # B
    add_interface_port s_axi s_axi_bid     bid     Output $WID_W
    add_interface_port s_axi s_axi_bresp   bresp   Output 2
    add_interface_port s_axi s_axi_bvalid  bvalid  Output 1
    add_interface_port s_axi s_axi_bready  bready  Input  1
    # AR
    add_interface_port s_axi s_axi_arid    arid    Input  $RID_W
    add_interface_port s_axi s_axi_araddr  araddr  Input  $ADDR_W
    add_interface_port s_axi s_axi_arlen   arlen   Input  $LEN_W
    add_interface_port s_axi s_axi_arsize  arsize  Input  3
    add_interface_port s_axi s_axi_arburst arburst Input  2
    add_interface_port s_axi s_axi_arvalid arvalid Input  1
    add_interface_port s_axi s_axi_arready arready Output 1
    # R
    add_interface_port s_axi s_axi_rid     rid     Output $RID_W
    add_interface_port s_axi s_axi_rdata   rdata   Output $DATA_W
    add_interface_port s_axi s_axi_rresp   rresp   Output 2
    add_interface_port s_axi s_axi_rlast   rlast   Output 1
    add_interface_port s_axi s_axi_rvalid  rvalid  Output 1
    add_interface_port s_axi s_axi_rready  rready  Input  1

    # ---- HyperBus pin conduit (exported to top-level HyperRAM balls; real inout, from
    #      axc3000_hyperram_pads.sv's board balls). Single-ended CK only: the AXC3000 HyperRAM
    #      ball-out has no hb_ck_n pin (third_party/hyperram/fpga/axc3000/top.sv, pins.tcl) and
    #      axc3000_hyperram_pads.sv's default IO_VARIANT="DDIO_GPIO" branch never had one either. ----
    add_interface hyperbus conduit end
    add_interface_port hyperbus hb_dq     dq     Bidir  8
    add_interface_port hyperbus hb_rwds   rwds   Bidir  1
    add_interface_port hyperbus hb_cs_n   cs_n   Output 1
    add_interface_port hyperbus hb_ck     ck     Output 1
    add_interface_port hyperbus hb_rst_n  rst_n  Output 1

    # ---- status conduit (sticky trip-wires + init state; all optional to connect) ----
    add_interface status conduit end
    add_interface_port status wstrb_partial_seen wstrb_partial_seen Output 1
    add_interface_port status hi_addr_seen       hi_addr_seen       Output 1
    # init_done: hyperram_avalon's POR + CR0-programming-complete flag (see
    # axc3000_hyperram_axi4.sv). Not gating logic elsewhere is required to consume this — the
    # submodule's own avs_waitrequest already holds off traffic until init completes — but it is a
    # useful bring-up/debug status bit to export (e.g. to an LED or a JTAG-visible register).
    add_interface_port status init_done          init_done          Output 1

    # ---- per-fit launch-trim calibration CSR (Avalon-MM agent) --------------------------------
    # Ported from the third_party/hyperram bench's REG_DBG/REG_CAL runtime knobs (hyperram_cal_csr.sv,
    # wired into axc3000_hyperram_pads.sv's DDIO_GPIO launch path). The host JTAG-Avalon master pokes
    # this to calibrate the trim-calibrated (NOT SDC-constrained) DQ/CK launch per fit, in-system, no
    # recompile — the fix for the ED-only 4 KB address alias (scratch/hyperram_retest/
    # alias_diagnosis.md; scratch/hyperram_retest/calibrate_ed.tcl runs the sweep). ed_zero.tcl maps
    # it at 0x9000_0000, disjoint from the CSR data bridge (0x8000_0000) and the global-memory window.
    # Word-addressed (each 32-bit register at byte offset 4*addr). PLAIN FIXED read latency = 1 clock
    # (readLatency 1): the RTL registers readdata one clock after the read is accepted; there is NO
    # readdatavalid and NO waitrequest port. This is the exact shape of the silicon-proven l2_m20k_bw
    # JTAG-Avalon CSR slave on this board (rtl/microbench/l2_m20k_bw/m20k_bw.sv). It REPLACES the
    # earlier variable-latency declaration (readdatavalid + readLatency 0 + maximumPendingReadTransactions
    # + a stray bridgesToMaster ""), which was the ROOT CAUSE of the on-silicon cal-CSR readback bug:
    # with a readdatavalid port but readLatency declared 0, the interconnect / SLD master did not honour
    # the one-clock readdatavalid handshake and sampled the shared jtag_master response bus a clock
    # early, returning stale interconnect data (the last HyperRAM word, 0x8888_0000) instead of this
    # slave's ID/registers. A fixed readLatency==1 makes the interconnect sample exactly when the RTL
    # presents the data — no handshake left to mis-model. Same clk/reset domain as s_axi.
    add_interface cal_csr avalon end
    set_interface_property cal_csr associatedClock  clk
    set_interface_property cal_csr associatedReset  reset
    set_interface_property cal_csr addressUnits     WORDS
    set_interface_property cal_csr burstOnBurstBoundariesOnly false
    set_interface_property cal_csr holdTime         0
    set_interface_property cal_csr linewrapBursts   false
    set_interface_property cal_csr readLatency      1
    set_interface_property cal_csr readWaitTime     0
    set_interface_property cal_csr setupTime        0
    set_interface_property cal_csr timingUnits      Cycles
    set_interface_property cal_csr writeWaitTime    0
    add_interface_port cal_csr csr_address       address       Input  4
    add_interface_port cal_csr csr_read          read          Input  1
    add_interface_port cal_csr csr_write         write         Input  1
    add_interface_port cal_csr csr_writedata     writedata     Input  32
    add_interface_port cal_csr csr_readdata      readdata      Output 32
}
