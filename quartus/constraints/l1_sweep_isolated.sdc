## L1 sweep — ISOLATED clock architecture (issue #11, PLAN §3 LV4 "isolated" point).
## Hot domain (PE array + its M20Ks) on `clk_hot`, constrained aggressively at 300 MHz (PLAN §2).
## Cool domain (CSR slave) on `clk` at 150 MHz (PLAN §3 LV4: "Cool domain 100-150 MHz"). The two are
## declared asynchronous — the rtl/common CDC wrappers (pulse_sync, async_fifo, cdc_bit_sync) own the
## seam, so STA must not time paths across it. The sweep records the HOT clock (`clk_hot`) fmax; that
## is the number LV4 improves by freeing the array of the CSR logic's pull on its clock.
create_clock -name clk     -period 6.666 [get_ports clk]
create_clock -name clk_hot -period 3.333 [get_ports clk_hot]
set_clock_groups -asynchronous -group {clk} -group {clk_hot}
set_false_path -from [get_ports rst_n]
