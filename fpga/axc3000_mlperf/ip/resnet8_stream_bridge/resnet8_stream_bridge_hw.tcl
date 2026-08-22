package require -exact qsys 26.1

set_module_property NAME resnet8_stream_bridge
set_module_property DISPLAY_NAME "ResNet-8 MM/AXI-stream bridge"
set_module_property VERSION 1.0
set_module_property GROUP "MLPerf Tiny"
set_module_property DESCRIPTION "Three 32-bit writes form one 96-bit DLA input beat; two reads drain one 64-bit output beat."
set_module_property OPAQUE_ADDRESS_MAP true

add_fileset synth QUARTUS_SYNTH ""
set_fileset_property synth TOP_LEVEL resnet8_stream_bridge
add_fileset_file resnet8_stream_bridge.sv SYSTEM_VERILOG PATH resnet8_stream_bridge.sv TOP_LEVEL_FILE

add_interface clk clock sink
add_interface_port clk clk clk Input 1

add_interface reset reset sink
set_interface_property reset associatedClock clk
set_interface_property reset synchronousEdges DEASSERT
add_interface_port reset reset_n reset_n Input 1

add_interface s0 avalon end
set_interface_property s0 associatedClock clk
set_interface_property s0 associatedReset reset
set_interface_property s0 addressUnits WORDS
set_interface_property s0 bitsPerSymbol 8
set_interface_property s0 explicitAddressSpan 64
set_interface_property s0 maximumPendingReadTransactions 0
set_interface_property s0 maximumPendingWriteTransactions 0
set_interface_property s0 readLatency 0
set_interface_property s0 readWaitTime 0
set_interface_property s0 writeWaitTime 0
add_interface_port s0 av_address address Input 4
add_interface_port s0 av_read read Input 1
add_interface_port s0 av_readdata readdata Output 32
add_interface_port s0 av_write write Input 1
add_interface_port s0 av_writedata writedata Input 32
add_interface_port s0 av_byteenable byteenable Input 4
add_interface_port s0 av_waitrequest waitrequest Output 1

add_interface source axi4stream start
set_interface_property source associatedClock clk
set_interface_property source associatedReset reset
add_interface_port source m_axis_tdata tdata Output 96
add_interface_port source m_axis_tvalid tvalid Output 1
add_interface_port source m_axis_tready tready Input 1

add_interface sink axi4stream end
set_interface_property sink associatedClock clk
set_interface_property sink associatedReset reset
add_interface_port sink s_axis_tdata tdata Input 64
add_interface_port sink s_axis_tvalid tvalid Input 1
add_interface_port sink s_axis_tready tready Output 1
add_interface_port sink s_axis_tlast tlast Input 1
add_interface_port sink s_axis_tstrb tstrb Input 8
