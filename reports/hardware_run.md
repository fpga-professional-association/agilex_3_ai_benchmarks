# AXC3000 hardware run

Date: 2026-08-19 (America/Chicago)

This is a deterministic synthetic-input hardware regression and scalar
performance baseline. It is not an official MLPerf Tiny submission or CIFAR-10
accuracy result.

## Audited inputs

- Git base commit: `5ddebd575660dbef663d4f86386bf66c5d8f6a50`.
- Cable: `USB Blaster III [USB-1]`.
- FPGA IDCODE: `4361B0DD`, Agilex 3 `A3C(W100BM16A|Y100BM16A)`.
- Programmed SOF SHA-256:
  `3A609DD4199C4095E941BE150E99337A46F3A7BA43083744FC2DFA6602E138C8`.
- Downloaded ELF SHA-256:
  `72CD7E4DE0F6C574CB4F5522DBC5ACE2A329DE127242D1F9E6D4482CD22FAD55`.
- JTAG nodes after configuration: `Nios V #0` and `JTAG UART #0`.
- HyperRAM was held inactive: clock 0 MHz, chip select high, reset low, data
  and RWDS high impedance. This is below the board's 150 MHz maximum.

## Programming and download

Quartus Programmer configured the FPGA successfully with 0 errors and 0
warnings in `5.5929446` seconds (PowerShell Stopwatch).

The first explicitly indexed Nios download was rejected before a memory write
because `niosv-download` uses a different device-index convention from
`juart-terminal` (`0.5790162` seconds). A subsequent auto-detected attempt
connected to the correct hart but stripped backslashes from the Windows ELF
path, so GDB explicitly reported that no file was loaded (`4.835908` seconds).
Neither attempt ran the benchmark.

The successful command used auto-detection and a forward-slash Windows path.
It loaded 184,568 bytes, reported entry address `0x00001a74`, detached with the
hart running, and completed in `4.6261979` seconds.

## Captured target output

```text
resnet8_niosv begin clock_hz=100000000
self_test=pass class=6 checksum=0x867c28f5
class=6 checksum=0x867c28f5 ticks=70811326870 warmups=5 timed=20
elapsed_seconds=708.113268
token_metric=not_exposed
```

The independent host regression expected class 6 and checksum `0x867c28f5`,
so the hardware self-test and final output match it exactly.

## Derived scalar baseline

- Timed iterations: 20, after 5 warmups.
- Board timer duration: 70,811,326,870 ticks at 100 MHz, or
  `708.11326870` seconds. The firmware display truncates this to six decimal
  places.
- Mean latency: `35.405663435` seconds per inference.
- Throughput: `0.02824406897` inferences per second.

JTAG UART viewer timeouts required reconnecting the host viewer without
flushing. The Nios application was not reset or re-downloaded during those
viewer reconnections, and the measured interval came from the target timer.

## Scope limit

The permitted source set does not include CIFAR-10 evaluation data or an
independent permitted TFLite runtime oracle. Therefore this run proves
deterministic execution of the repository's integer regression implementation
on the AXC3000 and establishes its scalar latency; it does not establish the
official MLPerf Tiny accuracy threshold, submission compliance, or TFLite
bit-exactness.
