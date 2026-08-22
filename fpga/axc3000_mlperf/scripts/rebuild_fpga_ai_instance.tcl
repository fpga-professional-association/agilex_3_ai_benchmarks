package require -exact qsys 26.1

# Phase 9: rebuild the fpga_ai instance around the VENDOR reference architecture
# (AGX3_Small_NoSoftmax) with DDR-resident parameters.
#
# The architecture V1_8x8_AGX3 is the vendor example architecture with only two
# forced deviations (pe_array.enable_scale / activation.enable_round_clamp,
# which the compiler *requires* for a FakeQuantize graph) plus k_vector and
# c_vector reduced 16 -> 8 to fit Agilex 3.  Critically:
#   enable_on_chip_parameters : absent (false)  <- the prime suspect, now OFF
#   config + filter now live in the pseudo-DDR and are streamed by the DMA
#
#   input_stream_interface  : absent -> no fpga_ai.axi_istream, no axi_clk
#   output_stream_interface : absent -> no fpga_ai.axi_ostream
#   disable_external_memory : false  -> fpga_ai.ddr_axi (AXI4, 256-bit) is live
#
# TRAP (learned the hard way): a stale ARCH_OPTION name makes add_connection
# fail silently and save_system then persists a broken system.  If this script
# errors, restore NIOSV_lab.qsys from git before retrying.

# qsys-script runs a restricted Tcl (no "file normalize"), so the MIF location
# is spelled out.  An absolute path is required anyway: it makes the on-chip
# memory IP copy the MIF into its generated folder.
set repo_mem "D:/altera_demo/chat_gpt_mlperf_demo/fpga/axc3000_mlperf/mem"

# ---------------------------------------------------------------- stream path
if {[lsearch -exact [get_instances] stream_bridge] >= 0} {
    remove_instance stream_bridge
}

# ------------------------------------------------------------------- fpga_ai
remove_instance fpga_ai
add_instance fpga_ai altera_ai_ip 0.6
set_instance_parameter_value fpga_ai ARCH_OPTION V1_8x8_AGX3

# axi_clk is only created when a stream interface is enabled - do not connect it.
foreach iface {dla_clk ddr_clk irq_clk} {
    add_connection iopll_0.outclk0 fpga_ai.$iface
}

add_connection reset_in.out_reset fpga_ai.dla_resetn
add_connection reset_release.ninit_done fpga_ai.dla_resetn

add_connection niosv_m.data_manager fpga_ai.csr_axi
set_connection_parameter_value niosv_m.data_manager/fpga_ai.csr_axi baseAddress 0x000A0000

add_connection niosv_m.platform_irq_rx fpga_ai.irq_level
set_connection_parameter_value niosv_m.platform_irq_rx/fpga_ai.irq_level irqNumber 1

# ----------------------------------------------------------- pseudo-DDR RAMs
# Sized from the compiler's ddr_buffer_info for this graph/arch:
#   configFilterBuffer 186880 B (config 0..19455, filter 19456..186879)
#   inputOutputBuffer   16896 B (input 0..16383, output 16384..16895)
#   interBuffer             0 B
#
# intel_onchip_memory rounds its address width up to a power of two, so a
# single 203776-byte RAM would cost a full 256 KiB (128 M20K).  Splitting the
# parameter region into 128 KiB + 64 KiB and giving the io buffer its own
# 32 KiB block costs 112 M20K instead.  Both parameter halves are 4 KiB-aligned,
# so no AXI burst (which may not cross a 4 KiB boundary) can ever straddle the
# slave boundary at 0x20000.
#
# DLA-side map (what the CSRs are programmed with):
#   0x00000  dla_par0  128 KiB  config+filter bytes 0..131071      (MIF-init)
#   0x20000  dla_par1   64 KiB  config+filter bytes 131072..186879 (MIF-init)
#   0x30000  dla_io     32 KiB  input at +0, output at +16384
# Nios-side map: the same three blocks, contiguous from 0x00100000.

proc add_pseudo_ddr {name bytes dla_base nios_base init_file} {
    if {[lsearch -exact [get_instances] $name] >= 0} {
        remove_instance $name
    }
    add_instance $name intel_onchip_memory 1.4.11
    set_instance_parameter_value $name {memorySize}                $bytes
    set_instance_parameter_value $name {interfaceType}             {0}
    set_instance_parameter_value $name {dualPort}                  {1}
    set_instance_parameter_value $name {enableDiffWidth}           {1}
    set_instance_parameter_value $name {dataWidth}                 {64}
    set_instance_parameter_value $name {dataWidth2}                {32}
    set_instance_parameter_value $name {singleClockOperation}      {1}
    set_instance_parameter_value $name {writable}                  {1}
    set_instance_parameter_value $name {blockType}                 {AUTO}
    set_instance_parameter_value $name {readDuringWriteMode_Mixed} {DONT_CARE}
    set_instance_parameter_value $name {allowInSystemMemoryContentEditor} {0}
    set_instance_parameter_value $name {ecc_check}                 {0}

    if {[string length $init_file] == 0} {
        set_instance_parameter_value $name {initMemContent}        {0}
        set_instance_parameter_value $name {useNonDefaultInitFile} {0}
    } else {
        # An absolute path makes the IP copy the MIF into the generated folder
        # and reference it by leaf name (see altera_avalon_onchip_memory2_hw.tcl).
        set_instance_parameter_value $name {initMemContent}        {1}
        set_instance_parameter_value $name {useNonDefaultInitFile} {1}
        set_instance_parameter_value $name {copyInitFile}          {1}
        set_instance_parameter_value $name {initializationFileName} $init_file
    }

    add_connection iopll_0.outclk0 $name.clk1
    add_connection reset_in.out_reset $name.reset1
    add_connection reset_release.ninit_done $name.reset1

    add_connection fpga_ai.ddr_axi $name.s1
    set_connection_parameter_value fpga_ai.ddr_axi/$name.s1 baseAddress $dla_base

    add_connection niosv_m.data_manager $name.s2
    set_connection_parameter_value niosv_m.data_manager/$name.s2 baseAddress $nios_base
}

# The old single pseudo-DDR is replaced by the three blocks below.
if {[lsearch -exact [get_instances] dla_ddr] >= 0} {
    remove_instance dla_ddr
}

add_pseudo_ddr dla_par0 131072 0x00000000 0x00100000 "$repo_mem/dla_par0.mif"
add_pseudo_ddr dla_par1  65536 0x00020000 0x00120000 "$repo_mem/dla_par1.mif"
add_pseudo_ddr dla_io    32768 0x00030000 0x00130000 ""

sync_sysinfo_parameters
save_system
