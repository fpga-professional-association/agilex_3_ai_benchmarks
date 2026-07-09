# Timing constraints for the L0b soft-MAC density sweep (issue #10, PLAN §7 L0b). Shared by every
# (W, M) revision in quartus/l0b_soft_mac/ — the top-level port list (clk, rst_n, checksum_q) is
# identical across the whole grid.
#
# The clock target is intentionally aggressive (1 ns / 1000 MHz — unreachable by any of these soft-
# logic arrays) rather than a "realistic" period. This is deliberate: Quartus's Fitter scales its
# retiming/optimization effort to how hard the constraint is, and PLAN §3 LV1's Hyperflex discipline
# relies on that retiming to find the true achievable frequency. An aggressive, guaranteed-to-fail
# target forces full effort every time regardless of grid point, so the number this sweep reports
# (scripts/report_fmax.py's slow-corner plain *Fmax*, not this constraint — see that module's
# docstring: Restricted Fmax on this device/speed-grade is pinned to a fixed clock-network floor,
# not the design's own logic-limited fmax) reflects each design's real achievable ceiling rather
# than however much effort a looser target happened to elicit.
create_clock -name clk -period 1.000 [get_ports clk]
set_false_path -from [get_ports rst_n]
