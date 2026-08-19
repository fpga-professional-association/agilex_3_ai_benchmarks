# Arrow AXC3000 HyperRAM baseline

Date: 2026-08-19 (America/Chicago)

## Scope and command

The unmodified cloned source at `third_party/arrow_refdes` was built from
`third_party/arrow_refdes/axc3000/niosv_qspi_hyperram_refdes`:

```powershell
& 'C:\altera_pro\26.1\quartus\bin64\quartus_sh.exe' --flow compile axc3000_top
```

The command was run in Windows PowerShell with a
`[System.Diagnostics.Stopwatch]`; exact wall time was **98.6 seconds** and
the process exit code was **3**. No hardware programming was attempted.
Quartus was **26.1.0 Build 110 03/26/2026 SC Pro Edition**. The QSF device is
`A3CY100BM16AE7S` (Agilex 3), and the top-level entity/revision is
`axc3000_top`.

## Result and blockers

IP generation completed successfully: **0 errors, 49 warnings**. HSSI
support-logic generation completed successfully: **0 errors, 0 warnings**.
Analysis & Synthesis failed, so full compilation was unsuccessful: **6
errors, 60 warnings** (Quartus shell summary: 13 errors, 60 warnings).
The exact blocking messages were:

```text
Error (292014): Can't find valid feature line for core SLL_CA_XSPI_MC_V3_3_demo_edition (AE7C_0062) (Vendor: Synaptic Labs) in current license.
Error (13223): Verilog HDL or VHDL error: cannot open Verilog file 'ip/niosv_system/niosv_system_sll_xspi_mc_avmm_top_0/sll_xspi_mc_avmm_top_33116/synth/sll_ca_xspi_mc_top_enc.sv'
Error: Failed to elaborate design:
Error (21794): Quartus Prime Full Compilation was unsuccessful: 6 errors, 60 warnings
```

The Nios V/g General Purpose Processor IP license was acquired
(`6AF7_018C`), but the Synaptic Labs SLL xSPI/HyperRAM demo feature was not
licensed. The missing encrypted source is therefore a downstream symptom of
the license failure. The log reports no explicit IP-migration-required
failure; it did report that the revision was previously opened in 25.3 and
that 26.1 default assignment values changed. Generation also reported IP
variant/project-setting mismatch warnings, including the SLL component.

Other notable exact warnings include the SLL notice that high clock speeds
are not supported on Agilex 3 in this configuration without an R&D or
Production license, and the `niosv_system` SLL clock-domain mismatch (system
info 2 versus IP-file declaration 4). The complete unabridged console log is
in the ignored generated file
`third_party/arrow_refdes/axc3000/niosv_qspi_hyperram_refdes/baseline_compile.log`.

## Utilization, timing, and clocks

No fitter, assembler, or TimeQuest stage ran. Therefore utilization,
slack, and timing closure are **not available**. No final `.sta.rpt`, clock
summary, or post-fit clock report was produced; consequently there is no
final-report evidence that all HyperRAM-related clocks are <=150 MHz.
The generated Platform Designer connectivity identifies the relevant clocks
as `clock_subsystem_0.hyper_clk` and `hyper_clk_90` feeding the SLL
`hbus_clk`/`hbus_clk_90`, but source-level connectivity is not a substitute
for final timing analysis.

Evidence files: `baseline_compile.log`, `output_files/axc3000_top.ipg.rpt`,
`output_files/axc3000_top.syn.rpt`, and `output_files/axc3000_top.flow.rpt`.
