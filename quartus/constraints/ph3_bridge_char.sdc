# Timing constraints for the PH3 bridge+controller LOGIC characterization (branch
# ph3-hyperram-axi4-coredla). Single fabric clock at 250 MHz (4.000 ns), the HyperRAM-class target.
# Every wide functional port is a virtual pin (see ph3_bridge_char.qsf), so there is no board IO to
# constrain here — only the internal register-to-register paths of axi4_hbmc_bridge + hbmc_core. The
# reported Fmax is therefore the combined LOGIC fmax, excluding the real DDR-IO PHY (not written yet).

create_clock -name clk -period 4.000 [get_ports clk]
derive_clock_uncertainty
