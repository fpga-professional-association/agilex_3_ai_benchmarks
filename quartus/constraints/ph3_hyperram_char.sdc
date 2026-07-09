# Timing constraints for the PH3 HyperRAM standalone char/bring-up build (branch
# ph3-hyperram-axi4-coredla, quartus/ph3_hyperram_char, PH3_SUBMODULE_SPEC.md DELIVERABLE 5).
#
# Base clock: the real 25 MHz AXC3000 board oscillator (CLK_25M_C, PIN_A7), same as
# axc3000_board.sdc. char_clkgen (qsys/char_clkgen.qsys) is a Platform Designer IOPLL that derives
# clk (50 MHz, CK word rate) and clk2x (100 MHz, SDR byte rate) from it — `derive_pll_clocks` finds
# and constrains those generated clocks automatically instead of hand-authored create_clocks here.
#
# HyperBus pins (hb_dq[7:0], hb_rwds, hb_cs_n, hb_ck, hb_rst_n) are REAL board pins in this build
# (unlike ph3_bridge_char, which virtualized them because there was no real PHY yet), but this is a
# BRING-UP characterization pass, not a closed board timing signoff: the W957D8NB source-synchronous
# DDR/SDR I/O timing (device datasheet tDSS/tDSH/tCKD etc.) has not been characterized against this
# board's trace lengths (third_party/hyperram/docs/INTEGRATION.md SS5 flags this as board-specific,
# not something the RTL ships closed). Per the spec, false-path the off-chip HyperBus pins so the
# Fitter closes on the FABRIC side of the SDR PHY (registers, gearbox, controller, bridge) and
# reports a real fmax/ALM/DSP/M20K for that logic; the PHY <-> physical-pin timing is future
# hardware bring-up work (calibrated read-capture phase, output-delay budget from the schematic).

set_time_format -unit ns -decimal_places 3

create_clock -name CLK_25M_C -period 40.000 [get_ports {CLK_25M_C}]
derive_pll_clocks
derive_clock_uncertainty

# USER_BTN is a mechanical, debounced-in-fabric input (feeds char_clkgen's reset_in) — not a
# clock-relative timing path.
set_false_path -from [get_ports {USER_BTN}]

# Off-chip HyperBus pins: bring-up style, per PH3_SUBMODULE_SPEC.md DELIVERABLE 5 (see header
# above). Board timing closure to the W957D8NB is a separate future hardware bring-up task.
set_false_path -to   [get_ports {hb_dq[*] hb_rwds hb_cs_n hb_ck hb_rst_n}]
set_false_path -from [get_ports {hb_dq[*] hb_rwds}]

# Status LEDs: async-ish visual indicators, not timing-critical.
set_false_path -to [get_ports {LED1 RLED GLED}]
