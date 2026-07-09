# CoreDLA CSR start/done handshake — resolved (Track B)

**Status: RESOLVED from vendor source.** The handshake that `smoke_infer.py`'s
`SystemConsoleTransport.run_inference()` left as `NotImplementedError` is **fully determined** — it
is not a hidden secret inside a compiled `.so`. The FPGA AI Suite 2026.1.1 Docker image ships the
CoreDLA **RTL** (which *defines* the CSR contract) and the **host runtime C++ source** (which
*drives* it). Both were read directly. No hardware, no JTAG capture, and no guessing were needed to
produce the sequence below.

The only remaining board-dependent seam is the mechanical plumbing of a `system-console`
subprocess (spawn + claim masters + one `_send()` line-writer). All *sequence logic* — which offset,
which bit, which order, how to detect done — is nailed down and unit-tested off-board.

---

## Where it lives (all paths inside `alterafpga/fpgaaisuite:2026.1.1-quartus`)

`$COREDLA_ROOT = /opt/altera/fpga_ai_suite/ubuntu/dla`

| Role | File |
|------|------|
| CSR hardware (the contract) | `$COREDLA_ROOT/fpga/dma/rtl/dla_dma_csr.sv` |
| Numeric register offsets | `$COREDLA_ROOT/fpga/dma/dual_inc/dla_dma_constants.svh` |
| PCIe↔CSR adapter | `$COREDLA_ROOT/fpga/platform_adapter/rtl/dla_platform_csr_adapter.sv` |
| **Start sequence** | `$COREDLA_ROOT/runtime/coredla_device/src/coredla_batch_job.cpp` (`StartDla`) |
| **Done polling** | `$COREDLA_ROOT/runtime/coredla_device/src/coredla_device.cpp` (`WaitForDla`) |
| Interrupt clear/enable | `coredla_device.cpp` ctor (lines 95–98) |
| Intermediate-buffer addr | `$COREDLA_ROOT/runtime/coredla_device/src/device_memory_allocator.cpp:44` |
| JTAG CSR/DDR I/O forms | `$COREDLA_ROOT/runtime/coredla_device/mmd/system_console/mmd_wrapper.cpp` |
| System Console masters | `.../system_console/system_console_script.tcl` |

Copies of the exact files read are staged (for review) under
`scratchpad/vendor_runtime/` (git-ignored, vendor-copyrighted — do **not** commit them).

---

## Register map (CoreDLA DMA CSR window, base `0x8000_0000`)

Byte offsets, verbatim from `dla_dma_constants.svh`. The System Console master adds the
`0x8000_0000` base (`mmd_wrapper.cpp:69`, added inside `write_to_csr`/`read_from_csr`). **Inside the
RTL the byte offset is compared as a word address (`offset/4`)** — so the host writes the *byte*
offset directly; the hardware divides by 4 itself. Do **not** pre-scale, and do **not** pre-add the
base when calling the handshake (the transport's `csr_*` methods own that, exactly like the vendor
MMD).

| Offset (dec / hex) | Name | Access | Meaning |
|---|---|---|---|
| 512 / 0x200 | `INTERRUPT_CONTROL` | R / **W1C** | bit0=error, bit1=done. Write 1 to a bit to clear it. |
| 516 / 0x204 | `INTERRUPT_MASK` | R/W | bit0=error, bit1=done. Gates the interrupt line only. |
| 528 / 0x210 | `CONFIG_BASE_ADDR` | W | DDR base of the config-reader blob (config words + weights). |
| 532 / 0x214 | `CONFIG_RANGE_MINUS_TWO` | W | `(#config_words / 8) − 2` (down-counter, sign bit = done). |
| 536 / 0x218 | `INPUT_OUTPUT_BASE_ADDR` | W | **Writing this enqueues one descriptor = one inference (GO).** |
| 540 / 0x21c | `DESC_DIAGNOSTICS` | R | bit0=overflow, bit1=almost_full, bit2=out_of_inferences. |
| 544 / 0x220 | `INTERMEDIATE_BASE_ADDR` | W | DDR base of intermediate scratch (stock runtime uses 0). |
| 548 / 0x224 | `COMPLETION_COUNT` | R | Free-running count of finished jobs. **Poll this for done.** |
| 552 / 0x228 | `IP_RESET` | W | Write nonzero → soft-reset the IP. |
| 556 / 0x22c | `READY_STREAMING_IFACE` | R/W | Input-streaming "go" (istream path only). |
| 576 / 0x240 | `CLOCKS_ACTIVE_LO/HI` | R | Perf counters (busy clocks), 64-bit split. |
| 608 / 0x260 | `LICENSE_FLAG` | R | Nonzero if the bitstream is licensed. |

Interrupt bits: `ERROR_BIT=0`, `DONE_BIT=1`. `CONFIG_READER_DATA_BYTES = 8`
(`coredla_batch_job.cpp:19`).

---

## The sequence (DDR-backed, non-streaming — what an EMIF+CSR JTAG design exposes)

This is the `!disableExternalMemory_ && !enableIstream_` branch of `StartDla`, i.e. tensors and
config live in a global memory window (the `agx3c_jtag` EMIF the `smoke_infer.py` map targets).

**Preconditions** (host must place in DDR via `write_ddr` before starting):
- config-words blob (with filter/weights immediately after) at `config_base_addr`;
- input tensor at `input_addr`, output region reserved immediately after it (allocator rule
  "output must come immediately after input");
- intermediate scratch at `intermediate_addr` (stock runtime: 0).

**Start** (`coredla_batch_job.cpp::StartDla`):

```
0.  baseline = csr_read32(COMPLETION_COUNT)                        # jobs finished so far
1.  csr_write32(INTERMEDIATE_BASE_ADDR, intermediate_addr)         # device_memory_allocator.cpp:44
2.  csr_write32(CONFIG_BASE_ADDR,       config_base_addr)          # coredla_batch_job.cpp:136
3.  csr_write32(CONFIG_RANGE_MINUS_TWO, total_config_bytes/8 - 2)  # coredla_batch_job.cpp:140
4.  csr_write32(INPUT_OUTPUT_BASE_ADDR, input_addr)   # *** GO ***  # coredla_batch_job.cpp:150
```

Step 4 is the trigger. `dla_dma_csr.sv` decodes a write to `INPUT_OUTPUT_BASE_ADDR` into
`enqueue_descriptor`; after the AXI write-response the FSM enters `STATE_DESCRIPTOR`, which pushes
8 words into the descriptor queue = one unit of work. **There is no separate "start bit" — writing
the I/O base address IS the start, and it must be written LAST.**

**Done** (`coredla_device.cpp::WaitForDla`, `runtimePolling_` branch, lines 287–303):

```
5.  poll csr_read32(COMPLETION_COUNT) until (current - baseline) mod 2^32 >= 1   # then done
```

`COMPLETION_COUNT` increments once per finished job (`dla_dma_csr.sv`:
`completion_count <= completion_count + i_token_done`). The compare is 32-bit-wrap-safe. On the
JTAG/System-Console path this poll is the *only* thing needed — `RegisterISR()` explicitly throws
"System Console plugin requires polling", and `COMPLETION_COUNT` advances regardless of the
interrupt mask (the mask only gates the level interrupt line). `configure_interrupts()` (clear +
unmask, mirroring the ctor) is provided for parity but is **not** required for a single inference.

**Error guards:** while polling, also read `DESC_DIAGNOSTICS`; bit0 (overflow) or bit2
(out_of_inferences) means the run will never complete — fail fast instead of waiting for timeout.

### DDR-free / streaming variant (documented, not implemented here)
When CoreDLA is built DDR-free (`disableExternalMemory_`), `StartDla` **skips** steps 1–4 entirely
(config/weights are in on-chip M20K ROM, addresses absolute) and instead writes
`READY_STREAMING_IFACE=1` and streams the input via the MMD's `StreamInData`. That path needs the
streaming MMD, not simple CSR pokes, so it is out of scope for a System-Console smoke test. The
EMIF+CSR sequence above is the right one for a JTAG bring-up smoke run.

---

## Deliverables

- **`sw/host/coredla_csr_handshake.py`** — register-map constants, `InferenceJob`,
  `CoreDlaCsrHandshake` (pure `start`/`wait_for_done`/`run_inference` over any `CsrPort`), and a
  drop-in `SystemConsoleTransport` whose `run_inference` runs the sequence. Every constant and step
  is cited inline to the vendor file/line above.
- **`sw/host/tests/test_coredla_csr_handshake.py`** — 14 passing tests over a mock CSR port:
  exact offsets, trigger-is-last ordering, `−2` range arithmetic, 32-bit-wrap done detection,
  timeout, overflow/out-of-inferences guards, and interoperability with `smoke_infer.MockTransport`.
  `pytest sw/host/tests/test_coredla_csr_handshake.py` → 14 passed.

### How to wire into `smoke_infer.py` (no edit to `smoke_infer.py` needed)
`InferenceTransport.run_inference(*, timeout_s)` takes no addresses, so job parameters attach to the
transport:

```python
from coredla_csr_handshake import SystemConsoleTransport, InferenceJob
job = InferenceJob(config_base_addr=..., total_config_bytes=..., input_addr=0x0, intermediate_addr=0x0)
t = SystemConsoleTransport(sof_path="top.sof", job=job)
t.open()   # <-- the only board-dependent step (spawns system-console, claims the two masters)
import smoke_infer
smoke_infer.smoke_infer(t, input_bytes=..., input_addr=job.input_addr,
                        output_addr=..., output_bytes=...)
```

Or drive the handshake directly over any `csr_read32`/`csr_write32` object:
`CoreDlaCsrHandshake().run_inference(port, job, timeout_s=30.0)`.

---

## What is known vs. the smallest remaining unknown

**Fully known (from source, no hardware):** the complete register map; that the I/O-base write is
the trigger and must be last; the four config writes and their order; that done = poll
`COMPLETION_COUNT` for an increment; the `−2` range convention; the JTAG CSR command forms
(`master_write_32`/`master_read_32` at `0x8000_0000 + offset`) and DDR block forms
(`master_write_from_file`/`master_read_to_file`).

**Remaining, and it is NOT the handshake:** the mechanical `system-console` subprocess wiring —
spawn `system-console`, source `system_console_script.tcl`, run `load_sof` /
`claim_emif_ddr_service` / `claim_dla_csr_service`, and implement one `_send()` line-writer plus the
two temp-file DDR helpers. That is board bring-up plumbing (needs a programmed `top.sof` on the
AXC3000), left as the single `NotImplementedError` seam in `SystemConsoleTransport.open()/_send()`.
It carries **no** undetermined register semantics.

**One design-level caveat for the smoke test itself:** `smoke_infer.smoke_infer()` only writes the
*input* tensor before `run_inference`. A real DDR-backed run also needs the compiled network's
config-words + weights resident at `config_base_addr` first (produced by `dla_compiler`; laid out by
the device memory allocator). For a smoke run either (a) pre-stage that blob via `write_ddr` and
pass its real `config_base_addr`/`total_config_bytes` in the `InferenceJob`, or (b) use a DDR-free
bitstream (config in on-chip ROM) and the streaming variant. This is a *harness* gap, not a
handshake unknown.
