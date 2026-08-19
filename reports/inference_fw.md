# ResNet8 int8 inference firmware

The implementation in `software/resnet8` is a static, dependency-free path
for the exact `pretrainedResnet_quant.tflite` identified by
`model/model_manifest.json` (SHA-256
`3c002613d1b2475eb51dd78dfb85a546c8ae658dee71cf6ade43b022fe205415`).
`tools/generate_resnet8_model.py` imports the repository's
`tools/tflite_introspect.py`, checks that model hash, reads the FlatBuffer
constant buffers and builtin options, and emits the ignored/reproducible
`software/resnet8/generated_model.h`.

The graph contains nine Conv2D operators, three Add residuals, 8x8 VALID
AveragePool, Reshape, FullyConnected, and ten-class Softmax.  Conv2D uses the
FlatBuffer's NHWC/OHWI layout, SAME padding, strides, asymmetric activation
zero points, per-output-channel weight scales, int32 bias, TFLite's
quantized-multiplier rounding and int8 saturation.  Fused RELU is applied in
the output quantized domain.  The three-slot, 49,152-byte arena is sized from
the model's largest 32x32x16 activation and retains residual branches while
the 16/32/64-channel tensors are scheduled.

`resnet8_benchmark()` performs five warmups followed by twenty timed
inferences.  It only requires a `uint64_t tick(context)` callback, so Nios V
HAL timer reads and UART formatting remain in a small platform adapter.  The
optional `host_benchmark.c` is a host-only example.  A target adapter should
report `output_class`, the FNV-1a output checksum, elapsed ticks, and convert
ticks to seconds using the configured timer frequency.  No token metric is
exposed by this firmware.

## Nios V 26.1 integration

`software/resnet8/niosv_main.c` and `niosv_adapter.c` are the tracked target
entry point and HAL adapter. They use the JTAG UART-backed `printf`,
`alt_timestamp_start()`, `alt_timestamp()`, and `alt_timestamp_freq()`; the
adapter reports elapsed seconds without floating-point formatting and records
the expected 100 MHz compute-clock configuration. The build wrapper is
`software/resnet8/scripts/build_niosv_resnet8.ps1`. It regenerates the model,
validates the corrected SOPCINFO, invokes `niosv-bsp`, `niosv-app`, and CMake,
and writes an ELF, linker map, and section-accounting report under ignored
`software/resnet8/generated/`. The wrapper prepends the installed roots
`C:\altera_pro\26.1\niosv\bin`,
`C:\altera_pro\26.1\riscfree\toolchain\riscv32-unknown-elf\bin`, and
`C:\altera_pro\26.1\riscfree\build_tools\cmake\bin`; its BSP/app arguments
follow the checked-in Arrow `build_scripts.txt` syntax.

The corrected SOPCINFO address map is on-chip memory `0x00000000..0x00080000`
(524,288 bytes), Nios V debug `0x80000`, internal timer `0x90000`, sysid
`0x90040`, and JTAG UART `0x90048`. No hardware was programmed.

The installed Nios V 26.1 BSP/app/CMake and RISC-V toolchain completed the
optimized `-O2` link (the generated BSP toolchain adds `-O2`, with section and
data garbage collection enabled). The resulting ELF/map and exact section accounting are in
`reports/resnet8_niosv_memory.txt`; no hardware was programmed, so target
inference timing remains unavailable. The PowerShell build wrapper took
`12.6219946` seconds wall time for the measured rebuild. Host validation
remains `0.161` seconds for 20 timed inferences; a PowerShell `Stopwatch`
wrapper around the same host command measured `0.24507269999999998` seconds
including process startup; token timing is unavailable.

The optimized ELF accounts for `.text` code 93,440 bytes, startup
`.entry + .exceptions` 488 bytes, `.rodata` 84,272 bytes, `.rwdata` 6,368
bytes, and `.bss` 50,148 bytes. The requested static total (excluding the
488-byte startup/exception overhead) is 234,228 bytes, leaving 290,060 bytes
of the 512 KiB image capacity; the link end is address 241,092. BSP linker
symbols expose a shared 283,196-byte stack/heap window from address 241,092 to
0x80000. Stack and heap are not independent reservations and must not be added
together.

The current host self-test result is `class=6`, checksum
`0x867c28f5`; `resnet8_self_test()` verifies this end-to-end checksum and all
sixteen per-output layer checksums, returning nonzero on a regression.  The
host harness also prints `elapsed_seconds` using the C
clock tick frequency; `scripts/benchmark_resnet8.ps1` wraps any target/host
command with a PowerShell `Stopwatch` and emits a round-trip seconds value.

The independently implemented Python path in `tools/resnet8_golden.py` emits
the same regression vector (FNV-1a over signed-int8 bytes):

| output tensor | checksum |
|---|---|
| 22, 23, 24, 25 | `70826a1b`, `17fe510c`, `c1e7bf33`, `8c8976f5` |
| 26, 27, 28, 29 | `750876c7`, `36ff6295`, `bbeb0dcf`, `b4133f8b` |
| 30, 31, 32, 33 | `cd6d1218`, `79508a4f`, `963f9c7a`, `5bd06036` |
| 34, 35, 36, 37 | `60d67475`, `60d67475`, `a8daf8c1`, `867c28f5` |

This is explicitly an independent implementation regression vector, not a
TFLite-runtime oracle and not an official golden/accuracy result.

## Reproducibility and fidelity limits

The permitted MLCommons checkout was searched for a committed ResNet8 input
and golden output.  It contains the model and unrelated visual-wakewords
`ic_inputs.cc`, but no ResNet8 golden tensor/output.  Therefore the firmware
uses a deterministic linear-congruential synthetic 3072-byte int8 input and reports
a checksum; this is a self-test/repeatability vector, not an official accuracy
oracle.

The convolution and residual arithmetic follows TFLite/gemmlowp integer
multiplier rules.  AveragePool uses the exact TFLite power-of-two multiplier
(`1/64`) and gemmlowp rounding-divide-by-power-of-two behavior for the model's
64-element VALID window.  Residual Add inputs are passed in FlatBuffer order
because different branch scales make rounding non-commutative.  Softmax is intentionally a
small deterministic approximation: a generated 256-entry Q15 LUT for
`exp(-delta * output_scale)` followed by normalized Q0.8 probabilities.  This
keeps the firmware free of libm and bounded in integer arithmetic, but it is
not bit-identical to every TFLite Softmax implementation.  Consequently the
checksum is only an implementation regression check, and **no official
MLPerf/MLCommons accuracy claim is made**.

## Compiler check

With the generated header present, the portable sources compile without a BSP:

```text
riscv32-unknown-elf-gcc -std=c99 -ffreestanding -fsyntax-only -Isoftware/resnet8 \
  software/resnet8/resnet8.c software/resnet8/benchmark.c
```

If that compiler is unavailable, a host C99 object compile exercises the same
headers and diagnostics.  Regenerate first when the model or manifest changes:

```text
python tools/generate_resnet8_model.py
```
