# AXC3000 minimal Nios V/g derivative

Date: 2026-08-19 (America/Chicago)

## Scope and source

`fpga/axc3000_mlperf` is a tracked derivative of the official Arrow
`third_party/arrow_refdes/axc3000/first_niosv_refdes` source. It targets the
exact Arrow device `A3CY100BM16AE7S` and uses the official Arrow pin map. No
SLL xSPI, HyperRAM, QSPI controller, or other Synaptic Labs licensed IP is
present in the QSF, QSYS, or source tree. Generated Quartus/Platform Designer
trees are ignored by the repository rules.

The top-level system retains Nios V/m with the Nios debug/JTAG path, JTAG
UART, the Nios internal timer/performance counter, reset, and 25 MHz input.
The on-chip RAM is 524,288 bytes. The verified generated
`NIOSV_lab/NIOSV_lab.sopcinfo` span is `0x00000000..0x0007FFFF`; the map moves
the Nios debug agent to `0x00080000`, the timer to `0x00090000`, sysid to
`0x00090040`, and JTAG UART to `0x00090048` so the 512 KiB RAM does not
overlap those regions.

The system metadata was regenerated after conversion and is verified in the
generated SOPCINFO as `A3CY100BM16AE7S` / `Agilex 3` (no stale Agilex 5 device
metadata remains).

## HyperRAM and UART behavior

HyperRAM is deliberately inactive at the top level: `HR_CLK` is tied to 0,
`HR_CSn` to 1, `HR_HRESETn` is asserted low, and `HR_DQ[7:0]`/`HR_RWDS` are
high impedance. Thus the effective HyperRAM clock is exactly 0 MHz and no
licensed controller can toggle the pins. The Arrow TCL source and direct QSF
assignments map the pins as follows: `HR_CLK=D7`, `HR_CSn=D8`,
`HR_HRESETn=F7`, `HR_RWDS=A6`, and `HR_DQ={C3,C2,B4,B6,D3,A4,B3,C6}` for
bits 0 through 7. (The official Arrow TCL files in this checkout specify
`F7` for `HR_HRESETn`; no official TCL source specifies C10.)

The Arrow FTDI pins are constrained as `DBG_RX=AG23` and `DBG_TX=AG24`.
`DBG_TX` is held at UART idle-high because this minimal system has JTAG UART,
not a physical serial UART peripheral; COM5 is therefore electrically
reserved but not a functional serial console. `LED1=AG21` is a 25 MHz-domain
heartbeat divider, and the three color LEDs are held off. `VSEL_1V3=AJ24` is
held high.

## Exact build command and elapsed time

From `D:\altera_demo\chat_gpt_mlperf_demo\fpga\axc3000_mlperf`:

```powershell
$sw=[System.Diagnostics.Stopwatch]::StartNew(); & 'C:\altera_pro\26.1\quartus\bin64\quartus_sh.exe' --flow compile axc3000_top *> final_metadata_compile.log; $ec=$LASTEXITCODE; $sw.Stop(); $line=("EXIT={0} ELAPSED_SECONDS={1:N6}" -f $ec,$sw.Elapsed.TotalSeconds); Add-Content final_metadata_compile.log $line; Write-Output $line; exit $ec
```

Quartus: **26.1.0 Build 110 03/26/2026 SC Pro Edition**. Exact PowerShell
Stopwatch result for this final regenerated source set: **EXIT=0,
245.051386 seconds**. The compile produced
`output_files/axc3000_top.sof`; hardware programming was not attempted.

## Clocks and final reports

- `CLK_25M_C` (A7): 25.0 MHz, 40.000 ns.
- `u0|iopll_0|iopll_0_outclk0`: 100.0 MHz, 10.000 ns (Nios/system clock).
- `u0|iopll_0|iopll_0_refclk` and PLL reference: 25.0 MHz, 40.000 ns.
- HyperRAM `HR_CLK`: constant 0, no generated or analyzed memory clock.

Fitter and signoff results (`output_files/axc3000_top.fit.summary` and
`axc3000_top.sta.summary`):

- 3,062 / 34,000 ALMs (9%); 3,523 dedicated registers.
- 4,197,376 / 5,365,760 block-memory bits (78%); 260 / 262 RAM blocks
  (99%); 0 DSPs; 1 / 11 PLLs; 21 / 254 pins.
- Worst setup slack: **1.985 ns**; worst hold slack: **0.035 ns**;
  recovery 7.578 ns; removal 0.179 ns. Timing requirements were met.
- Fmax summary: 124.77 MHz for the 100 MHz Nios clock and 520.29 MHz for
  the 25 MHz PLL reference (the latter limited by minimum pulse width).

## Non-fatal warnings/blockers

Full compilation succeeded with 28 warnings. The only relevant limitations
are Quartus's expected constant/high-impedance pin warnings for the inactive
HyperRAM interface, default drive/slew warnings on several outputs, the
unconstrained reserved JTAG clock, three unconstrained input ports
(`altera_reserved_tdi`, `altera_reserved_tms`, `USER_BTN`), two unconstrained
outputs (`LED1`, `altera_reserved_tdo`), and six generated Nios debug clock
crosser `set_net_delay` warnings. Timing signoff still reports met setup and
hold requirements. RAM usage is 99%, leaving two RAM blocks; no further model
or activation capacity should be assumed without reducing the 512 KiB image.
