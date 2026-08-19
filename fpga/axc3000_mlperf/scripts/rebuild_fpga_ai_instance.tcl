package require -exact qsys 26.1

# Recreate the component from the currently generated FPGA AI Suite catalog.
# This is required when the architecture name changes because the saved system
# stores the child IP as a generic logical view.
remove_instance fpga_ai
add_instance fpga_ai altera_ai_ip 0.6
set_instance_parameter_value fpga_ai ARCH_OPTION resnet8_agx3_logits_AGX3

foreach iface {dla_clk ddr_clk axi_clk irq_clk} {
    add_connection iopll_0.outclk0 fpga_ai.$iface
}

add_connection reset_in.out_reset fpga_ai.dla_resetn
add_connection reset_release.ninit_done fpga_ai.dla_resetn

add_connection niosv_m.data_manager fpga_ai.csr_axi
set_connection_parameter_value niosv_m.data_manager/fpga_ai.csr_axi baseAddress 0x000A0000

add_connection stream_bridge.source fpga_ai.axi_istream
add_connection fpga_ai.axi_ostream stream_bridge.sink

add_connection niosv_m.platform_irq_rx fpga_ai.irq_level
set_connection_parameter_value niosv_m.platform_irq_rx/fpga_ai.irq_level irqNumber 1

sync_sysinfo_parameters
save_system
