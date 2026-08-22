package require -exact qsys 26.1

# Read-only inventory of the current system: instances, every connection, the
# clock/reset associations of the fpga_ai instance, and the IOPLL parameters we
# are about to change.  Run with:
#   qsys-script --script=scripts/dump_system.tcl --system-file=NIOSV_lab.qsys

puts "=== INSTANCES ==="
foreach i [get_instances] {
    puts "  $i : [get_instance_property $i CLASS_NAME]"
}

puts ""
puts "=== CONNECTIONS ==="
foreach c [get_connections] {
    puts "  [get_connection_property $c TYPE]  $c"
}

puts ""
puts "=== fpga_ai INTERFACES ==="
foreach iface [get_instance_interfaces fpga_ai] {
    set kind [get_instance_interface_property fpga_ai $iface TYPE]
    set aclk ""
    set arst ""
    catch { set aclk [get_instance_interface_property fpga_ai $iface associatedClock] }
    catch { set arst [get_instance_interface_property fpga_ai $iface associatedReset] }
    puts "  $iface  type=$kind  clock=$aclk  reset=$arst"
}

puts ""
puts "=== iopll_0 PARAMETERS (clock-related) ==="
foreach p [get_instance_parameters iopll_0] {
    if {[string match "gui_number_of_clocks" $p]
        || [string match "gui_output_clock_frequency?" $p]
        || [string match "gui_output_clock_frequency_ps?" $p]
        || [string match "gui_vco_frequency" $p]
        || [string match "gui_reference_clock_frequency" $p]
        || [string match "gui_divide_factor_c?" $p]
        || [string match "gui_clock_name_string?" $p]} {
        puts "  $p = [get_instance_parameter_value iopll_0 $p]"
    }
}

puts ""
puts "=== iopll_0 INTERFACES ==="
foreach iface [get_instance_interfaces iopll_0] {
    puts "  $iface  type=[get_instance_interface_property iopll_0 $iface TYPE]"
}
