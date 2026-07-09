## Timing constraints for the L0 tensor-chain microbench (issue #9). Single free-running hot clock
## (PLAN §3 LV4 domain split doesn't apply here — this microbench is CSR + datapath in one clock).
## 300 MHz is PLAN §2's "aggressive" end of the 250-300 MHz first-pass planning window; step 5 of
## the issue asks to compile at an aggressive target and record what the Fitter actually reports —
## an unmet timing constraint (negative slack) is expected/informative here, not a build failure.

create_clock -name clk -period 3.333 [get_ports clk]
set_false_path -from [get_ports rst_n]
