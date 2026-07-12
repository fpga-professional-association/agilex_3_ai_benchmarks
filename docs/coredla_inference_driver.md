# CoreDLA host inference driver (Track DRV) — resolved memory layout + real transport

Goal (task brief): everything needed to run the 4 MLPerf Tiny models on the AXC3000 and measure
them, **without touching the board** — build/investigate/prep only, orchestrator programs +
measures. This document is the write-up: the resolved `.aot` → HyperRAM memory layout (with vendor
citations), the exact command the orchestrator runs per model, and what remains unresolved.

Status: **DDR-backed / HyperRAM path fully implemented and unit-tested off-board.** DDR-free /
streaming path: protocol resolved from vendor source and implemented, but this repo's own DDR-free
Qsys address map has not been built yet, so four platform-specific addresses are flagged as an open
TODO rather than invented (see §5).

---

## 0. Files this document explains

| File | What it is |
|---|---|
| `sw/host/aot_layout.py` | Parses `dla_compiler`'s own `ddr_buffer_info_*.txt` and resolves a guard-banded HyperRAM address layout from it |
| `sw/host/coredla_csr_handshake.py` | CSR register map + start/done handshake (already resolved pre-task) + **new**: on-chip hw_timer methods, and a REAL `SystemConsoleTransport` (was all `NotImplementedError`) |
| `sw/host/system_console_process.py` | **New.** Low-level `system-console -cli` subprocess driver (spawn, `%`-prompt framing, `send`) |
| `sw/host/hyperram_loader.py` | **New.** Guard-banded load → CSR handshake → read output → parity gate, for the HyperRAM path |
| `sw/host/streaming_driver.py` | **New.** DDR-free/streaming driver (ingress/egress on-chip memory + mSGDMA), partially resolved — see §5 |
| `sw/host/run_tiny_benchmark.py` | Updated: `CoreDlaCsrTransport` now really drives either path; new `--path {hyperram,ddrfree}` CLI flag |
| `sw/host/tests/test_{aot_layout,system_console_process,hyperram_loader,streaming_driver}.py` | Unit tests, all board-free (see §6) |

---

## 1. The crux: what bytes go where (HyperRAM / DDR-backed path)

### 1.1 Why the `.aot` isn't hand-parsed directly

The compiled `.aot` files this repo already has
(`quartus/coredla_hyperram_ed/ip/models/<model>/<model>.aot`, produced by Track M's
`dla_compiler --march models/arch/AGX3_Performance.arch --network-file <IR>.xml
--foutput-format open_vino_hetero --fplugin HETERO:FPGA --o <model>.aot`) are **not** a raw
`dla::CompiledResult` byte stream. Hex-dumping one confirms it starts:

```
4845 5445 524f 3a46 5047 4100 3c3f 786d  HETERO:FPGA.<?xm
6c20 7665 7273 696f 6e3d 2231 2e30 223f  l version="1.0"?
3e3c 6865 7465 726f 206e 616d 653d 2274  ><hetero name="t
```

i.e. a `"HETERO:FPGA\0"` device tag followed by OpenVINO's own HETERO-plugin export XML — confirmed
against the vendor's own splitter tool source,
`$COREDLA_ROOT/runtime/dla_aot_splitter/src/main.cpp:379-398` (`$COREDLA_ROOT` =
`/opt/altera/fpga_ai_suite/ubuntu/dla`, inside the `alterafpga/fpgaaisuite:2026.1.1-quartus` image):

```cpp
std::string compiled_model_device;
{ char c; while (objIstream.get(c) && c != '\0') { compiled_model_device += c; } ... }
std::string rest_of_data(...);
*exeNetwork = core.import_model(rest_of_data_stream, device_name, {});   // device_name = "HETERO:FPGA"
```

The actual `dla::CompiledResult` (`$COREDLA_ROOT/compiled_result/inc/compiled_result.h`, serialized
with the `alpaca` library per `compiled_result_reader_writer.cpp`'s
`export_compiled_results_to_stream`/`import_stream_to_compiled_results`) only exists **inside** that
HETERO envelope, at an offset only resolvable by actually running OpenVINO's `ov::Core` with the
closed-source `libcoreDLAHeteroPlugin.so` + `libcoreDLAAotPlugin.so` loaded (confirmed experimentally
— linking a small tool directly against `libdla_compiled_result.so`'s
`dla::import_stream_to_compiled_results` and feeding it the raw `.aot` bytes throws
`"Value too large for defined data type"` immediately, both for the committed `open_vino_hetero` and
a freshly-recompiled `--foutput-format dla_compiled_result` .aot — the latter *also* starts with the
same `HETERO:FPGA\0` tag because `--fplugin HETERO:FPGA` forces the HETERO export path regardless of
`--foutput-format`). Reimplementing `alpaca`'s wire format around an offset this repo can't determine
without the closed-source plugin would be exactly the kind of fabricated-looking number AGENTS.md
forbids, so `aot_layout.py` does not do that.

### 1.2 What it does instead: `ddr_buffer_info_<subgraph>_0.txt`

The **same** `dla_compiler` invocation Track M already used **already writes an exact, byte-precise
text dump of this layout**, unprompted, no special flag needed — confirmed by re-running it fresh:

```
source scripts/env.sh
dla_compiler --march models/arch/AGX3_Performance.arch \
  --network-file quartus/coredla_hyperram_ed/ip/models/resnet8-cifar10/resnet8-cifar10.xml \
  --foutput-format open_vino_hetero --fplugin HETERO:FPGA \
  --o <dumpdir>/out.aot --dumpdir <dumpdir> --overwrite-output-files
```

stdout includes `Exporting DDR buffer info to file`, and `<dumpdir>/tf2onnx/ddr_buffer_info_tf2onnx_0.txt`
contains (real output, resnet8-cifar10):

```
inputOutputBuffer size: 33280
	Inputs:
		input_1: offset 0, size: 32768
	Output: offset 32768, size: 512

configFilterBuffer size: 208896
	 Config: offset 0, size: 22528
	 Filter: offset 22528, size: 186368
	 Bias+Scale: offset 208896, size: 0

interBuffer size: 0
```

Re-running the exact same command for all four models (fresh, this session — bit-identical inputs to
what produced the committed `.aot`s, so this is not a new/independent estimate) gives:

| Model | inputOutputBuffer | Config | Filter | Bias+Scale | interBuffer |
|---|---|---|---|---|---|
| resnet8-cifar10 | 33280 (in 32768 @0, out 512 @32768) | 22528 @0 | 186368 @22528 | 0 @208896 | 0 |
| ad-toycar | 3072 (in 1536 @0, out 1536 @1536) | 11264 @0 | 552960 @11264 | 0 @564224 | 0 |
| ds-cnn-kws | 2048 (in 1536 @0, out 512 @1536) | 32768 @0 | 1540352 @32768 | 0 @1573120 | 16960 |
| mobilenetv1-025-vww | 295424 (in 294912 @0, out 512 @294912) | 72704 @0 | 854016 @72704 | 0 @926720 | 202752 |

These byte counts are exactly what the DDR-backed CSR handshake and the vendor's own memory
allocator need (both already partially cited in `coredla_csr_handshake.py`, re-confirmed against two
more vendor source files this task read fresh):

- **"config immediately followed by filter (then bias+scale)"** is already true inside the ONE
  `configFilterBuffer` region above — matches `device_memory_allocator.cpp`'s comment ("filter must
  come immediately after config") and `compiled_result_t::config_filter_bias_scale_array`'s single
  concatenated bank for the non-DDR-free case (`compiled_result.h`).
- **"output immediately after input"** is already true inside the ONE `inputOutputBuffer` region.
- **`Config` size (bytes)** is exactly `InferenceJob.total_config_bytes` in
  `coredla_csr_handshake.py` — i.e. what `CONFIG_RANGE_MINUS_TWO = (Config bytes / 8) − 2` wants.
  **Not** the whole `configFilterBuffer` size (that would double the range and walk into the filter
  bytes as if they were config words).
- The vendor's own minimal "run one inference from an `.aot`, no OpenVINO, no hardware plugin"
  reference host,
  `$COREDLA_ROOT/runtime/dla_aot_splitter/dla_aot_splitter_example/src/main.cpp`, confirms the exact
  same allocation shape in code (`configFilterBufferSize = padded_config_mem_size +
  padded_filter_mem_size`, one `WriteToDDR` for config then one immediately after for filter;
  `inputOutputBufferSize = padded_input_mem_size + padded_output_mem_size` per pipeline slot), and
  its `CONFIG_RANGE_MINUS_TWO` line is `(config_mem_size / CONFIG_READER_DATA_BYTES) − 2` —
  config-only, confirming the point above independently.
- `DeviceMemoryAllocator::AllocatePrivateBuffer` (`device_memory_allocator.cpp`) allocates
  high-to-low with caller-specified alignment; `DeviceMemoryAllocator::AllocateSharedBuffer` places
  the intermediate buffer separately and only pokes its CSR base address (host never writes data
  into it — it's DLA-internal scratch).
- The msgDMA burst-alignment granule the vendor uses everywhere for padding is
  `kMsgDMAMaxBurstBytes = 512` (`util/inc/dla_aligned_allocator.h`) — reused here as both the default
  alignment and the default guard-band size (see §1.3).

### 1.3 Guard-banding (the write-wound law)

`docs/coredla_hyperram_onboard_findings.md` §7 (measured live on the AXC3000 this project's own
silicon proof-of-life): an **abutting** HyperRAM write wounds the 4 words (16 bytes) *below* its
base. `aot_layout.resolve_hyperram_layout()` places the intermediate scratch, then the config+filter
blob, then the input+output region, low → high, each rounded up to `align_bytes` (default 512), with
`guard_bytes` (default 512 — 32x the observed 16-byte wound zone, and equal to the vendor's own
`kMsgDMAMaxBurstBytes`) of guaranteed-dead space below **every host-issued write base**
(`config_base_addr`, `input_addr`). The hardware's own output write (immediately after input, zero
gap) is deliberately **not** guard-banded — that abutment is the vendor allocator's own contract, not
a host write, and by the time it happens the host has already consumed the input tensor, so a
wounded input tail is harmless. This is stated explicitly in `aot_layout.py`'s docstring, not glossed
over.

`aot_layout.build_inference_job()` turns a resolved `HyperRamLayout` straight into a
`coredla_csr_handshake.InferenceJob` (`config_base_addr`, `total_config_bytes`, `input_addr`,
`intermediate_addr`) — the object `CoreDlaCsrHandshake.start()`/`run_inference_timed()` already
consumed and unit-tested before this task.

---

## 2. Real System Console transport (was all `NotImplementedError`)

### 2.1 What the vendor's own MMD does

`$COREDLA_ROOT/runtime/coredla_device/mmd/system_console/mmd_wrapper.cpp` (the real
`libcoreDlaRuntimePlugin`'s JTAG backend) spawns `system-console -cli` via `boost::process` and talks
to it exactly like a REPL: write a Tcl line, read back everything up to the next `%` prompt
character. Every command form used below is transcribed from that file (line numbers as of the
2026.1.1 image):

| Operation | Tcl form (verbatim) |
|---|---|
| CSR write | `master_write_32 $::g_dla_csr_service 0x<addr+0x80000000> 0x<data>` |
| CSR read | `master_read_32 $::g_dla_csr_service 0x<addr+0x80000000> 1` → parse the last hex token |
| DDR/HyperRAM write | stage bytes to a temp file, `master_write_from_file $::g_emif_ddr_service <tmpfile> 0x<addr>` (no base added — `g_emif_ddr_service` claims offset 0x0) |
| DDR/HyperRAM read | `master_read_to_file $::g_emif_ddr_service <tmpfile> 0x<addr> 0x<nbytes>`, then read the temp file |
| Startup | `set ::cl(sof) <path>` → (optional `set ::cl(enable_pmon) 1`) → (optional `set ::cl(jtag_path) "..."`) → `source system_console_script.tcl` (defines + calls `initialization`: `load_sof` (`design_load`) → `claim_dla_csr_service` → `claim_emif_ddr_service`) → `csr_write32(IP_RESET, 1)` |
| Shutdown | `close_services` (one proc the sourced script defines: closes both master services + pmon if claimed) → `exit` |
| hw_timer | write `1` (start) / `2` (stop) to offset `0x800` (absolute `0x8000_0800`), then read the same offset for the elapsed clk_dla cycle count — used by the vendor MMD ctor itself to calibrate `clk_dla`'s real frequency (500 ms window), and reused here per-inference for latency |

### 2.2 What this repo now implements

- **`sw/host/system_console_process.py`** (new): the mechanical half — spawn, `%`-delimited prompt
  framing, `send()`. The framing state machine (`wait_for_prompt`) is unit-tested with a fake byte
  queue (timeout, EOF, multi-prompt framing); the actual `subprocess.Popen` call is the one
  hardware-dependent seam, `pragma: no cover`.
- **`coredla_csr_handshake.SystemConsoleTransport`** (was `NotImplementedError` everywhere): `open()`,
  `close()`, `_send()`, `write_ddr()`, `read_ddr()` now do exactly the table above, driving a
  `SystemConsoleProcess`. `tcl_script_path` defaults to
  `$COREDLA_ROOT/runtime/coredla_device/mmd/system_console/system_console_script.tcl` **inside the
  AI Suite image itself** (not a copy this repo maintains) so it can never drift from whatever
  `claim_dla_csr_service`/`claim_emif_ddr_service` the installed AI Suite version defines, and so
  there is no vendor-file duplication/licensing concern.
- **`CoreDlaCsrHandshake.run_inference_timed()`** (new): the same start/done sequence as
  `run_inference()`, bracketed by `start_hw_timer()`/`stop_hw_timer()`/`read_hw_timer()` around just
  the trigger write + completion poll (setup writes for `INTERMEDIATE_BASE_ADDR`/`CONFIG_BASE_ADDR`/
  `CONFIG_RANGE_MINUS_TWO` happen before the timer starts). Returns `(completion_count, cycles)`,
  where `cycles` is a real clk_dla hardware cycle count — PLAN §8 method E, not host wall-clock. Honest
  caveat documented inline: the stop write can only happen after the host's own poll notices
  completion, so a small amount of real (but still on-fabric) JTAG round-trip slop is included,
  bounded by `poll_interval_s`.

### 2.3 Exact orchestrator invocation

**Two separate Docker images are involved, for two separate reasons — do not conflate them:**

1. **AI Suite image** (`alterafpga/fpgaaisuite:2026.1.1-quartus`, via `scripts/env.sh`'s `dla_compiler`
   wrapper) — used ONLY to regenerate `ddr_buffer_info_*.txt` per model (§1.2). Board-free, no JTAG,
   no privilege needed:

   ```bash
   source scripts/env.sh
   python sw/host/aot_layout.py \
     --model-xml quartus/coredla_hyperram_ed/ip/models/<model>/<model>.xml \
     --arch models/arch/AGX3_Performance.arch \
     --dumpdir scratch/aot_layout/<model> \
     > results/layouts/<model>_hyperram_layout.json
   ```

2. **Quartus/system-console image, privileged, JTAG-attached** (the orchestrator's existing
   root+privileged+`/dev/bus/usb`+no-libudev docker invocation, per the memory note "AXC3000 JTAG
   programming path" — **NOT** `scripts/env.sh`'s `quartus_pgm` wrapper, which is CPU-container-only
   with no USB passthrough) — `system-console` ships with **Quartus**, not the AI Suite runtime
   image, so this MUST run in a Quartus-family container, not `fpgaaisuite`. Inside that container,
   with the programmed `.sof` and `models/arch/AGX3_Performance.arch` visible, and
   `COREDLA_ROOT=/opt/altera/fpga_ai_suite/ubuntu/dla` set (needed only so
   `SystemConsoleTransport`'s default `tcl_script_path` resolves — pass `tcl_script_path=` explicitly
   if the AI Suite tree isn't mounted in that container):

   ```bash
   scripts/devkit_lock.sh with "coredla-tiny-agent" "MLPerf Tiny HyperRAM run" -- \
     python3 -c "
   import sys; sys.path.insert(0, 'sw/host')
   from coredla_csr_handshake import SystemConsoleTransport
   from aot_layout import parse_ddr_buffer_info, resolve_hyperram_layout, build_inference_job
   from hyperram_loader import load_and_run

   layout = resolve_hyperram_layout(parse_ddr_buffer_info(open('results/layouts/resnet8-cifar10_ddr_buffer_info.txt').read()))
   t = SystemConsoleTransport('quartus/coredla_hyperram_ed/platform/build/hw/output_files/top.sof',
                               job=build_inference_job(layout))
   t.open()
   result = load_and_run(t, layout,
                          config_filter_bytes=open('models_bin/resnet8-cifar10/config_filter.bin','rb').read(),
                          input_bytes=open('records/rec_0000.bin','rb').read(),
                          reference_output=open('records/rec_0000.ref_out.bin','rb').read())
   print('cycles:', result.cycles, 'completion:', result.completion_count)
   t.close()
   "
   ```

   `run_tiny_benchmark.py` wraps the same pieces for a full model sweep:

   ```bash
   scripts/devkit_lock.sh with "coredla-tiny-agent" "MLPerf Tiny on-board run" -- \
     python sw/host/run_tiny_benchmark.py \
       --bundle results/tiny_bundles/resnet8-cifar10 \
       --sof quartus/coredla_hyperram_ed/platform/build/hw/output_files/top.sof \
       --arch-file models/arch/AGX3_Performance.arch \
       --fclk-mhz 280.0 --mode both --path hyperram \
       --ddr-buffer-info results/layouts/resnet8-cifar10_ddr_buffer_info.txt \
       --out-dir results/
   ```

   `--fclk-mhz 280.0` matches the retuned `clk_dla` in
   `docs/coredla_hyperram_onboard_findings.md` §3c/§7 (280 MHz, parity-gated, 44 endpoints at
   −0.156 ns) — **confirm parity before trusting a latency number from this specific `.sof`**, per
   that document's caveat.

`config_filter.bin`/per-record input+reference bytes are **not** produced by this task (that's the
packer/reference-bundle pipeline, `sw/packer/` + `docs/tiny_hardware_benchmark_runbook.md` §0.3) —
only the memory-layout resolution and the driver that consumes them are.

---

## 3. Guard-banded load + parity gate

`sw/host/hyperram_loader.py`: `load_config_filter()` / `load_input()` write the two blobs at the
resolved, guard-banded addresses; `run_one_inference()` runs the hw_timer-bracketed handshake and
reads the output back; `run_one_inference_with_parity()` / `load_and_run(..., reference_output=...)`
raise `ParityError` — never return — on any output mismatch, per the task brief ("refuse to report on
mismatch") and `docs/onboard_benchmark_plan.md` §6's clk_dla-marginal caveat (every run must be
parity-gated until the PLL retune is independently re-verified).

---

## 4. Latency

`CoreDlaCsrHandshake.run_inference_timed()` / `SystemConsoleTransport.run_inference_timed()` return
the on-chip hw_timer's elapsed clk_dla cycle count per inference (§2.2) — never wall-clock over JTAG
(PLAN §8 method E). `run_tiny_benchmark.py`'s existing `cycles_to_us()`/`summarize_latency()` (already
implemented and tested pre-task) turn a list of these into p50/p99/fps using the bitstream's real
`--fclk-mhz`.

---

## 5. DDR-free / streaming path — what's resolved, what isn't

`sw/host/streaming_driver.py` implements the protocol shape from the FPGA AI Suite's own worked
example, `$COREDLA_ROOT/runtime/streaming/ed0_streaming_example/` (README + `system_console_script.tcl`
+ `include/system_console_lib.tcl`): `assert_reset` → `initialize_coredla` → `stage_input`
(`master_write_from_file` into ingress on-chip memory) → `queue_ingress_descriptor` /
`queue_egress_descriptor` (write the transfer size to each mSGDMA's own descriptor CSR) → trigger
`READY_STREAMING_IFACE=1` (the DDR-free equivalent of `INPUT_OUTPUT_BASE_ADDR`, confirmed from
`coredla_batch_job.cpp`'s `disableExternalMemory_ && enableIstream_` branch) → poll the same
`COMPLETION_COUNT` register the DDR-backed path uses (it's part of the common `dla_dma_csr.sv` block,
increments regardless of which datapath produced the token) → `read_output` (`master_read_to_file`
from egress on-chip memory). The hw_timer bracket is identical to §2.2/§4.

**What is NOT resolved:** `ed0_streaming_example` targets the **Agilex 7 I-series dev kit's own Qsys
system** — its `INGRESS_SGDMA_CSR_ADDR`/`EGRESS_SGDMA_CSR_ADDR` and ingress/egress on-chip-memory base
addresses are specific to that platform, not this repo's AXC3000/AGX3 DDR-free build
(`quartus/coredla_agx3_ddrfree/`). That platform has not yet been carried through `qsys-generate` to a
build tree with a resolved address map in this repo (only `arch/` + `scripts/build.sh` exist as of
this writing). `streaming_driver.StreamingRegisters` therefore takes these four addresses as required
constructor arguments and raises `NotImplementedError` (`require_resolved()`) if any are missing —
flagging this honestly rather than inventing plausible-looking numbers. Resolving them is reading
THIS platform's own generated Qsys address map once it exists (same class of exercise as
`docs/coredla_hyperram_onboard_findings.md`'s CSR base-address discovery for the HyperRAM path), not a
new unknown protocol — everything else in `streaming_driver.py` (ordering, hw_timer bracket,
descriptor-full guard) is real vendor-sourced logic and is unit-tested.

`run_tiny_benchmark.py --path ddrfree` is wired to `CoreDlaCsrTransport(path="ddrfree",
streaming_regs=..., streaming_output_bytes=...)` but the CLI itself refuses to run
(`--path ddrfree` prints the same "not yet available" message and exits 3) until those four addresses
are supplied by a caller that has them, since there is nothing honest to default them to.

---

## 6. Test results (all off-board, no Docker, no system-console, no board)

```
$ .venv/bin/python -m pytest sw/host/ -q
........................................................................ [ 37%]
........................................................................ [ 75%]
..............................................                          [100%]
```

New test files added by this task, all passing:

| File | Count | Covers |
|---|---|---|
| `test_aot_layout.py` | 13 | Parsing all 4 real `ddr_buffer_info` samples, contiguity invariants, guard-band placement, non-overlap, `InferenceJob` wiring |
| `test_system_console_process.py` | 6 | `%`-prompt framing: capture, multi-prompt, EOF, timeout, no-process guard |
| `test_hyperram_loader.py` | 11 | Load size checks, full handshake + hw_timer read-back, parity pass/raise, end-to-end `load_and_run` |
| `test_streaming_driver.py` | 11 | Register-resolution guard, SGDMA reset ordering, descriptor-full guard, full sequence + hw_timer bracket, timeout propagation |
| `test_coredla_csr_handshake.py` (extended) | +4 | `run_inference_timed()`: start/stop ordering brackets the trigger, setup writes still happen, timeout still propagates |
| `test_run_tiny_benchmark.py` (extended) | +3 | `CoreDlaCsrTransport` now needs `layout=`/`streaming_regs=` and still refuses to construct off-board |

A dry CLI run (`--path hyperram --ddr-buffer-info <real resnet8 dump> --no-lock-check`, no board, no
`system-console` installed) exits 3 with a clear message:

```
run_tiny_benchmark needs a board: CoreDlaCsrTransport needs a real board bring-up environment: a
`system-console` install, a programmed .sof (), and JTAG access to the AXC3000. Off-board this always
fails; use MockTinyTransport instead. Underlying error: [Errno 2] No such file or directory:
'system-console'
```

confirming the "never fabricate a number off-board" contract survives the new wiring end-to-end.

---

## 7. Summary of remaining unknowns (honest, per AGENTS.md)

1. **DDR-free/streaming addresses** (§5) — needs this repo's own `coredla_agx3_ddrfree` Qsys build.
2. **`system-console`/JTAG plumbing has never been run against real hardware** by this task (by
   design — the orchestrator holds the devkit lock and does all programming/measurement). The Tcl
   command forms are transcribed verbatim from vendor source, and the framing state machine is
   unit-tested, but the actual subprocess interaction with a live `system-console` process talking to
   a programmed AXC3000 is unverified until the orchestrator runs it.
2b. Relatedly: `clk_dla` on the current HyperRAM `.sof` is marginal (`docs/coredla_hyperram_onboard_findings.md`
   §3c/§7, retuned to 280 MHz, 44 endpoints at −0.156 ns) — every latency number from it must be
   parity-gated (§3), and a further PLL retune is recommended before treating any single number as final.
3. **`config_filter.bin`/per-record reference bytes production** is out of this task's scope (the
   packer/reference-bundle pipeline owns that); `hyperram_loader`/`aot_layout` only consume them.
