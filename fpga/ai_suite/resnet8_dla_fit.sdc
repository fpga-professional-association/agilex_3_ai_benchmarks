create_clock -name dla_clock -period 10.000 [get_ports clk]
set_false_path -from [get_ports reset_n]
