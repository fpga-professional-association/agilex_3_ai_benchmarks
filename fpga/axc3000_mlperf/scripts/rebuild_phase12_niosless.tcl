package require -exact qsys 26.1

# ---------------------------------------------------------------------------
# Phase 12: Nios-less, JTAG-hosted, wide-core (k16c8 INT8) system.
#
# Removes the entire Nios V subsystem (niosv_m, jtag_uart, sysid and the 64 KiB
# onchip_memory) and replaces it with altera_jtag_avalon_master, so the host PC
# drives the CoreDLA CSRs and the pseudo-DDR directly over the USB-Blaster III.
# That frees 36 M20K (32 for the Nios RAM, 2 each for the CPU and the JTAG UART)
# and ~2.4k ALMs, which is what pays for the bigger PE array.
#
# fpga_ai is rebuilt around fpga/ai_suite/resnet8_agx3_int8_k16c8.arch:
#   k_vector 16, c_vector 8, FP12AGX  ->  24 DSPs, 128 int8 MACs/cycle
#   (the shipping V1_8x8_AGX3 arch is 12 DSPs, 64 MACs/cycle)
#   configFilterBuffer 205,056 B   inputOutputBuffer 16,896 B   interBuffer 0 B
#
# Pseudo-DDR (on-chip RAM standing in for DDR; the AXC3000 has no DRAM).
# Every AXI burst is <= 512 B and AXI forbids crossing a 4 KiB boundary, and
# every slave boundary below is at least 4 KiB-aligned, so no transaction can
# ever straddle two slaves.
#
#   instance   size     DLA ddr_axi         JTAG master (host)   contents
#   dla_par0   128 KiB  0x00000-0x1ffff     0x00100000           param 0..131071      (MIF)
#   dla_par1    64 KiB  0x20000-0x2ffff     0x00120000           param 131072..196607 (MIF)
#   dla_par2    16 KiB  0x30000-0x33fff     0x00130000           param 196608..205055 (MIF)
#   dla_io0     16 KiB  0x40000-0x43fff     0x00140000           input  (16,384 B) at +0
#   dla_io1      4 KiB  0x44000-0x44fff     0x00144000           output (512 B) at io+16384
#   fpga_ai.csr_axi      -                  0x00040000           CSR window (8 KiB)
#
# So for every RAM: host address = DLA address + 0x00100000.
#
# TRAPS (learned in phases 9-11, all still apply):
#   * add_connection only WARNS on failure; save_system then persists a broken
#     system.  This script hard-gates save_system on an explicit expected
#     connection set.
#   * a stale ARCH_OPTION silently breaks the fpga_ai instance -- ARCH_OPTION
#     must be the directory name dla_create_ip produced under
#     build/fpga_ai/generated_ip/altera_ai_ip/verilog/.
#   * re-adding instances mints new ip/NIOSV_lab/NIOSV_lab_<inst>_<n>.ip names;
#     the .qsf IP_FILE list must be re-derived from the saved .qsys afterwards.
#   * qsys-script runs a restricted Tcl: no "file normalize", no "eq".
# ---------------------------------------------------------------------------

set repo_mem "D:/altera_demo/chat_gpt_mlperf_demo/fpga/axc3000_mlperf/mem"
set arch_option "resnet8_agx3_int8_k16c8_AGX3"

proc has_instance {name} {
    return [expr {[lsearch -exact [get_instances] $name] >= 0}]
}

# ------------------------------------------------------- remove the Nios side
foreach inst {stream_bridge niosv_m jtag_uart sysid onchip_memory mtime timer dla_ddr} {
    if {[has_instance $inst]} {
        remove_instance $inst
        puts "removed instance $inst"
    }
}

# ------------------------------------------------------------------ fpga_ai
if {[has_instance fpga_ai]} { remove_instance fpga_ai }
add_instance fpga_ai altera_ai_ip 0.6
set_instance_parameter_value fpga_ai ARCH_OPTION $arch_option

# axi_clk only exists when a stream interface is enabled - this arch has none.
add_connection iopll_0.outclk0 fpga_ai.ddr_clk
add_connection iopll_0.outclk0 fpga_ai.irq_clk
add_connection iopll_0.outclk1 fpga_ai.dla_clk
add_connection reset_in.out_reset fpga_ai.dla_resetn
add_connection reset_release.ninit_done fpga_ai.dla_resetn

# fpga_ai.irq_level is deliberately left unconnected: there is no CPU to take
# the interrupt.  The host polls CSR 548 (completion count) instead.

# ------------------------------------------------------- JTAG to Avalon host
if {[has_instance jtag_master]} { remove_instance jtag_master }
add_instance jtag_master altera_jtag_avalon_master 19.1
add_connection iopll_0.outclk0 jtag_master.clk
add_connection reset_in.out_reset jtag_master.clk_reset
add_connection reset_release.ninit_done jtag_master.clk_reset
# jtag_master.master_reset (the host's resetrequest) is left unconnected on
# purpose: a stray System Console reset must not be able to knock the DLA over
# mid-run.  IP_RESET (CSR 552) is the supported way to reset the core.

add_connection jtag_master.master fpga_ai.csr_axi
set_connection_parameter_value jtag_master.master/fpga_ai.csr_axi baseAddress 0x00040000

# ----------------------------------------------------------- pseudo-DDR RAMs
proc add_pseudo_ddr {name bytes dla_base host_base init_file} {
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

    add_connection jtag_master.master $name.s2
    set_connection_parameter_value jtag_master.master/$name.s2 baseAddress $host_base
}

# The Phase 11 dla_io is replaced by the dla_io0/dla_io1 pair.
if {[has_instance dla_io]} { remove_instance dla_io }

add_pseudo_ddr dla_par0 131072 0x00000000 0x00100000 "$repo_mem/dla_par0.mif"
add_pseudo_ddr dla_par1  65536 0x00020000 0x00120000 "$repo_mem/dla_par1.mif"
add_pseudo_ddr dla_par2  16384 0x00030000 0x00130000 "$repo_mem/dla_par2.mif"
add_pseudo_ddr dla_io0   16384 0x00040000 0x00140000 ""
add_pseudo_ddr dla_io1    4096 0x00044000 0x00144000 ""

# --------------------------------------------------------------- HARD GATE
set expected {
    clock_in.out_clk/iopll_0.refclk
    clock_in.out_clk/reset_in.clk
    iopll_0.outclk0/fpga_ai.ddr_clk
    iopll_0.outclk0/fpga_ai.irq_clk
    iopll_0.outclk1/fpga_ai.dla_clk
    iopll_0.outclk0/jtag_master.clk
    iopll_0.outclk0/dla_par0.clk1
    iopll_0.outclk0/dla_par1.clk1
    iopll_0.outclk0/dla_par2.clk1
    iopll_0.outclk0/dla_io0.clk1
    iopll_0.outclk0/dla_io1.clk1
    reset_in.out_reset/fpga_ai.dla_resetn
    reset_in.out_reset/jtag_master.clk_reset
    reset_in.out_reset/iopll_0.reset
    reset_in.out_reset/dla_par0.reset1
    reset_in.out_reset/dla_par1.reset1
    reset_in.out_reset/dla_par2.reset1
    reset_in.out_reset/dla_io0.reset1
    reset_in.out_reset/dla_io1.reset1
    reset_release.ninit_done/fpga_ai.dla_resetn
    reset_release.ninit_done/jtag_master.clk_reset
    reset_release.ninit_done/iopll_0.reset
    reset_release.ninit_done/dla_par0.reset1
    reset_release.ninit_done/dla_par1.reset1
    reset_release.ninit_done/dla_par2.reset1
    reset_release.ninit_done/dla_io0.reset1
    reset_release.ninit_done/dla_io1.reset1
    fpga_ai.ddr_axi/dla_par0.s1
    fpga_ai.ddr_axi/dla_par1.s1
    fpga_ai.ddr_axi/dla_par2.s1
    fpga_ai.ddr_axi/dla_io0.s1
    fpga_ai.ddr_axi/dla_io1.s1
    jtag_master.master/fpga_ai.csr_axi
    jtag_master.master/dla_par0.s2
    jtag_master.master/dla_par1.s2
    jtag_master.master/dla_par2.s2
    jtag_master.master/dla_io0.s2
    jtag_master.master/dla_io1.s2
}

set actual [get_connections]
puts ""
puts "=== CONNECTIONS AFTER SURGERY ==="
foreach c [lsort $actual] { puts "  $c" }

set missing {}
foreach e $expected {
    if {[lsearch -exact $actual $e] < 0} { lappend missing $e }
}
set extra {}
foreach a $actual {
    if {[lsearch -exact $expected $a] < 0} { lappend extra $a }
}

puts ""
if {[llength $missing]} {
    puts "MISSING CONNECTIONS:"
    foreach m $missing { puts "  $m" }
}
if {[llength $extra]} {
    puts "UNEXPECTED CONNECTIONS:"
    foreach x $extra { puts "  $x" }
}
if {[llength $missing] || [llength $extra]} {
    error "REFUSING TO SAVE: connection set does not match the intended system"
}
puts "verified: all [llength $expected] connections present, none extra"

puts ""
puts "=== INSTANCES ==="
foreach i [get_instances] { puts "  $i" }

puts ""
puts "=== BASE ADDRESSES ==="
foreach c $actual {
    if {[string equal [get_connection_property $c TYPE] "avalon"]} {
        puts "  $c  baseAddress=[get_connection_parameter_value $c baseAddress]"
    }
}

sync_sysinfo_parameters
save_system
puts ""
puts "system saved"
