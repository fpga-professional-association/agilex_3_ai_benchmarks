# HyperBus timing constraints (issue #13). RWDS-referenced read capture (PLAN §3 LV6).
# First pass targets 100 MHz (200 MB/s peak); #14 trains capture and pushes the clock.
# NOT validated here (no Quartus in CI) — starting point for timing closure during bring-up.

# 100 MHz system / HyperBus clock
create_clock -name sys_clk -period 10.000 [get_ports clk]
create_clock -name hb_ck   -period 10.000 [get_ports hb_ck]

# RWDS is a source-synchronous read strobe DRIVEN BY THE DEVICE during reads. Treat it as an incoming
# clock so DQ capture is constrained relative to it, not to the fabric clock — the whole point of LV6.
create_clock -name rwds_in -period 10.000 [get_ports hb_rwds]

# Read data (DQ) is edge-aligned to RWDS at the device -> center-aligned capture window vs rwds_in.
# The real numbers come from the W957D8NB AC timing + board trace skew; these are placeholders.
set_input_delay -clock rwds_in -max  2.000 [get_ports hb_dq*]
set_input_delay -clock rwds_in -min -2.000 [get_ports hb_dq*]

# Write data + the RWDS mask are launched by the controller against hb_ck.
set_output_delay -clock hb_ck -max  2.000 [get_ports {hb_dq* hb_rwds}]
set_output_delay -clock hb_ck -min -1.000 [get_ports {hb_dq* hb_rwds}]

# CS# / RST# are quasi-static relative to hb_ck.
set_output_delay -clock hb_ck -max 2.000 [get_ports {hb_cs_n hb_rst_n}]
set_output_delay -clock hb_ck -min -1.000 [get_ports {hb_cs_n hb_rst_n}]

# Reset is asynchronous / synchronized in fabric.
set_false_path -from [get_ports rst]

# rwds_in only clocks the capture registers; it and hb_ck are the same physical net at 100 MHz but
# analyzed as separate domains. Refine with set_clock_groups once the PHY is in place.
