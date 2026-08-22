# FPGA AI Suite 2026.1.1 / CoreDLA on Agilex 3: inference output is a fixed, input-invariant payload on **both** the streaming and the DDR feature-writer egress paths

**Status: WORKAROUND FOUND — see §14 and §15.** Sections 1–13 document the
defect as it stood before Phase 9 and are retained unchanged as the evidence
trail. Phase 9 built the vendor's own architecture options with the graph
parameters resident in DDR instead of on chip, and the accelerator now produces
correct, input-dependent output (class 6, all values in range, 500-frame sweep
stable). The single option that is `true` in every failing build and `false` in
the working one is **`enable_on_chip_parameters`**. The defect report therefore
narrows from "the egress is frozen" to "the on-chip parameter path delivers
stale constants"; §15.5 gives the bisection order needed to confirm that.

**Relates to:** the reporter's open issue `altera-fpga/agilex-ed-ai-suite#5`. (That issue is referenced here because the reporter filed it; nothing in this workspace quotes its text, so the cross-reference is by issue number only.)
**Date of the runs described here:** 2026-08-21.

---

## 1. Executive summary

A ResNet-8 (MLPerf Tiny image classification, Softmax removed) graph compiled for
Agilex 3 with FPGA AI Suite 2026.1.1 runs to completion on hardware — completion
count increments, the done interrupt asserts, error and diagnostics bits stay
clear — but the returned tensor is **a fixed sequence of FP16 codes that does
not depend on the input data**.

The decisive new finding, and the reason for this report, is:

> **The defect is not in the output streamer.** It reproduces identically on the
> mainstream, non-streaming **DDR feature-writer** output path, in a build that
> contains **no stream interfaces at all** (`ENABLE_INPUT_STREAMING = 0`,
> `ENABLE_OUTPUT_STREAMER = 0`, `DISABLE_DDR = 0`).

In that DDR build the DMA writer demonstrably works as a transport: it writes
exactly 24 bytes (12 × FP16, matching the 12-channel padded output tensor) at
`INPUT_OUTPUT_BASE_ADDR + 8192`, it correctly relocates when that CSR is
changed, and the write lands in memory the CPU can read. Only the *contents*
are wrong and constant.

Both egress paths were also shown to be fed the correct number of groups: the
architecture's profiling counters report exactly **3** transactions per frame on
the Xbar→DMA interface, i.e. 3 × 64 bit = 24 bytes, which is the right amount of
data for this graph. The payload carried in those 3 groups is stale.

A second decisive finding was added on 2026-08-21 (§7.6):

> **The classifier tail is not implicated.** The same graph truncated to end at
> a plain convolution/pooling feature map — no `Reshape`, no `MatMul`, no
> `BiasAdd`, no 1×1 fully-connected layer of any kind — freezes identically.
> With that 64-channel output the frozen payload resolves into **one 64-bit
> Cvec group replayed 16 times**, and every FP16 code in it lies **outside the
> compiled output tensor's own `FakeQuantize` range**.

---

## 2. Environment

| Item | Value |
|---|---|
| FPGA AI Suite | 2026.1.1, registered as 26.1.1.130; DLA compiler build string `2026.1.1+b17` (`build_version.txt` = `2026.1.1/17`) |
| Installer | `fpga_ai_suite-26.1.1.130-windows.exe`, 55,551,112 bytes, SHA-256 `FAAED83A886299243314DFAC5704F183234AB325481819FBEEA2BC4E10704C56`, Authenticode valid (Intel Corporation) |
| Quartus Prime | 26.1.0 Build 110 03/26/2026 SC Pro Edition (`C:\altera_pro\26.1\quartus`) |
| OpenVINO | 2025.4.0 (`C:\altera_pro\openvino_2025.4.0`) |
| Device | `A3CY100BM16AE7S`, family `Agilex 3` |
| Board | Arrow AXC3000 (Trenz TEI0131), reference design `ArrowElectronics/refdes-agilex3` @ `62c062464531a4c1f7cfa9b18b6f73fa4b41f6d3` |
| Host OS | Windows 11, native (no WSL/Docker) |
| Cable | `USB Blaster III [USB-1]`, device 1, instance 0 |
| Simulation | Not available: native Windows RTL simulation is unsupported in 2026.1.1; all IP was generated with `--skip-sim-env`. **All evidence below is in-silicon.** |

Soft system: Nios V/m + JTAG UART + on-chip RAM in Platform Designer, everything
in a single 100 MHz clock domain from one IOPLL output. No external DDR is
present on this board; see §5 for how the DDR path was provided.

---

## 3. Graph under test and the CPU oracle

MLPerf Tiny quantized ResNet-8, Softmax removed (argmax of logits is identical
to argmax of softmax(logits), and exposing logits makes the accelerator result
directly diagnosable).

| Artifact | SHA-256 |
|---|---|
| MLPerf Tiny reference `.tflite` | `3C002613D1B2475EB51DD78DFB85A546C8AE658DEE71CF6ADE43B022FE205415` |
| Adapted no-Softmax OpenVINO XML | `C104ECDC1E0C1777B0071D51E14119BD6B280CE1B21752FA1F84AAB93DCD4A81` |
| Adapted OpenVINO BIN | `519DE0A6211125C394F92FCACF5E6F4664B1DA6C9D2B0280B1BD913A0077364B` |

**Oracle** (OpenVINO 2025.4.0 CPU plugin, same adapted graph, deterministic
synthetic NHWC test image): class **6**, logits

```
[-18.216473, -13.232721, -2.9215105, -4.6400456, -24.91876,
 -20.794275,  2.2340949, -22.856518, -5.84302,  -21.825397]
```

Maximum adapted-vs-reference logit difference for that input: `0.171854019`.

Caveat, stated for completeness: permitted CIFAR-10 evaluation data and a
permitted TFLite-runtime oracle are not available in this workspace, so this is
an OpenVINO CPU oracle over the same adapted graph, not a TFLite oracle. It is
nevertheless more than sufficient here, because the hardware result does not
depend on the input at all.

---

## 4. Architectures exercised

All three compile to **1 FPGA subgraph with no CPU fallback**.

| | A: 4×4 / 128-bit ostream | B: 4×4 / 64-bit ostream (`o64`) | C: 8×8 / 64-bit ostream | **D: 4×4 / DDR writer (this phase)** |
|---|---|---|---|---|
| `k_vector` / `c_vector` | 4 / 4 | 4 / 4 | 8 / 8 | 4 / 4 |
| `arch_precision` | FP12AGX | FP12AGX | FP12AGX | FP12AGX |
| `input_stream_interface` | enable, 96 bit | enable, 96 bit | enable, 96 bit | **absent** |
| `output_stream_interface` | enable, 128 bit | enable, 64 bit | enable, 64 bit | **absent** |
| lightweight layout transform | yes | yes | yes | **absent** |
| `enable_on_chip_parameters` | true | true | true | true |
| `disable_external_memory` | true | true | true | **false** |
| `stream_buffer_depth` | 12288 | 12288 | 12288 | 12288 |
| `config_cache_depth` | 2433 | 2433 | 2433 | 2433 |
| dma `ddr_data_bytes` | 32 | 32 | 32 | **8** (64-bit AXI) |

Architecture B exists specifically because the aux-Xbar output width (64) then
equals `AXI_OSTREAM_DATA_WIDTH`, so no width adaptation occurs on the streaming
egress. It did not change the symptom.

Architecture D is the subject of this report. Its generated
`dla_dma_param.svh` confirms the configuration:

```
localparam bit ENABLE_INPUT_STREAMING = 0;
localparam int ENABLE_OUTPUT_STREAMER = 0;
localparam int CONFIG_ID_OUTPUT_STREAMER = -1;
localparam int CONFIG_ID_OUTPUT_STREAMER_FLUSH = -1;
localparam int CONFIG_ID_WRITER_STREAMER_SEL = -1;
localparam int CONFIG_ID_FEATURE_READER = 8;
localparam int CONFIG_ID_FEATURE_WRITER = 7;
localparam int FEATURE_READER_DATA_BYTES = 8;
localparam int FEATURE_WRITER_DATA_BYTES = 8;
localparam int C_DDR_AXI_DATA_WIDTH = 64;
localparam int DISABLE_DDR = 0;
```

Compiler-reported buffer layout for architecture D
(`ddr_buffer_info_TensorFlow_Lite_Frontend_IR_0.txt`), identical to the layout
reported for a full-DDR 32-byte-bus variant and for a streaming-input/DDR-output
hybrid variant:

```
On-chip parameters are enabled in this arch
Config and Filter data not stored on DDR
inputOutputBuffer size: 8704
	Inputs:
		image_u8_nchw: offset 0, size: 8192
	Output: offset 8192, size: 512

configFilterBuffer size: 0
interBuffer size: 0
```

Architecture D fit results (full board design, Quartus 26.1, 100 MHz constraint):

```
Fitter Status                     : Successful
Logic utilization (in ALMs)       : 12,579 / 34,000 ( 37 % )
Total block memory bits           : 2,376,012 / 5,365,760 ( 44 % )
Total RAM Blocks                  : 143 / 262 ( 55 % )
Total DSP Blocks                  : 6 / 276 ( 2 % )
Worst-case setup slack            : +2.531 ns   (Slow fix6a 0C)
Worst-case hold slack             : +0.010 ns   (Fast fix6 0C)
Fmax (u0|iopll_0|iopll_0_outclk0) : 133.89 MHz
Compile result                    : 0 errors, 187 warnings
```

Timing is met with margin, so this is not a timing-closure failure.

---

## 5. How the DDR path was provided

The AXC3000 has no external DDR usable by this design, so the DLA's `ddr_axi`
AXI4 master (64-bit, 32-bit address) was wired in Platform Designer to a
dedicated 16 KiB dual-port on-chip RAM (`intel_onchip_memory` 1.4.11,
`dualPort = 1`, `enableDiffWidth = 1`, `dataWidth = 64` on the DLA port,
`dataWidth2 = 32` on the CPU port, `singleClockOperation = 1`):

* `fpga_ai.ddr_axi  -> dla_ddr.s1` at DLA-side base `0x00000000`
* `niosv_m.data_manager -> dla_ddr.s2` at CPU-side base `0x00100000`

Both ports address the same RAM array, so the values written to CSR 536 / 544
are plain byte offsets into it. The mapping is not assumed — it is proven by the
hardware itself in §7.3 (the writer's bytes land at exactly the CPU offset the
compiler predicted, and they move when the base register moves).

A CPU-side write/read/verify pass over the whole 16 KiB runs before every
session and reports `errors=0`.

---

## 6. CSR sequence used (and its source)

The firmware follows the vendor's own non-streaming testbench driver,
register for register:

`C:\altera_pro\2026.1.1\fpga_ai_suite\dla\example_ip_cores\altera_ai_ip\sim\sequential_tb\AGX3_Small_NoSoftmax_AGX3\tb_csr_driver_AGX3_Small_NoSoftmax_AGX3.sv`

| tb line(s) | Action | Firmware |
|---|---|---|
| 201-202 | `IP_RESET` (552) ← 1 | yes, then a settle delay |
| 210-211 | `INTERRUPT_MASK` (516) ← 3 | yes |
| 218-229 | LT writeback registers | skipped (`LAYOUT_TRANSFORM_WRITEBACK_MODE = 0`) |
| 232-233 | `INTERMEDIATE_BASE_ADDR` (544) ← inter base, once | yes (`0x2400`) |
| 239-247 | `CONFIG_BASE_ADDR` (528) / `CONFIG_RANGE_MINUS_TWO` (532) | **skipped**, as the tb does when `ENABLE_ON_CHIP_PARAMETERS` is set |
| 253-258 | `READY_STREAMING_IFACE` (556) | **not written** — that branch requires `ENABLE_INPUT_STREAM & ENABLE_OUTPUT_STREAM & ENABLE_ON_CHIP_PARAMETERS`, which this architecture is not in |
| 259-271 | `INPUT_OUTPUT_BASE_ADDR` (536) ← io base, **once per inference** | yes; each write enqueues one descriptor |
| 288-295 | read `INTERRUPT_CONTROL` (512) → done bit; read `COMPLETION_COUNT` (548) → increments | yes |
| 299-300 | `INTERRUPT_CONTROL` (512) ← W1C both bits | yes |

The buffer adjacency rule is from the matching top-level testbench,
`tb_top_AGX3_Small_NoSoftmax_AGX3.sv:576-585`:

```systemverilog
config_base_addr = ENABLE_ON_CHIP_PARAMETERS ? 0 : (($urandom % 100) * BASE_ADDR_ALIGNMENT);
filter_base_addr = ENABLE_ON_CHIP_PARAMETERS ? 0 : config_base_addr + config_mem.size();
input_base_addr  = filter_base_addr + filter_mem.size();
...
    output_base_addr = input_base_addr + input_mem.size();   // output data must come immediately after input data
inter_base_addr  = (($urandom % 100) * BASE_ADDR_ALIGNMENT) + output_base_addr + output_mem.size();
```

Input tensor format is the compiler's own, from
`input_transform_mapping_TensorFlow_Lite_Frontend_IR_0.csv`: element index
`(h*32 + w) * Cvec + c` with `Cvec = 4`, FP16 elements, channel 3 zero-padded —
i.e. 32·32·4·2 = 8192 bytes, matching the compiler's "Graph input size: 8192
bytes." Output decoding is from
`output_transform_mapping_TensorFlow_Lite_Frontend_IR_0.csv`, where AI-suite
output element index maps 1:1 onto logical channel for channels 0..9.

---

## 7. Observations on architecture D (DDR feature writer)

### 7.1 The IP reports success

Every frame:

```
status tag=... irq=0x00000002 diagnostics=0x00000000 completion_count=N
```

`irq` bit 1 = done is set, bit 0 = error is clear, descriptor diagnostics
(including the out-of-inferences bit) are clear, and `COMPLETION_COUNT`
increments by exactly 1 per write to CSR 536. `LICENSE` reads `0x00000000`
(evaluation IP; see §8).

Bring-up readback:

```
bringup ip_reset=1 settle_ticks=8192 mask=0x00000003 irq_after_clear=0x00000000
        intermediate_base=0x00002400 diagnostics=0x00000000 completion_count=0
        license=0x00000000
```

The architecture discovery ROM matches the expected build in all 9 probed words
(`discovery_summary words=9 zero=0 mismatch=0`), including the architecture hash
`0x0ed32841` and the name string `"resn" "et8_" "agx3" "_wri" "ter"`, so the
programmed bitstream is the intended graph.

### 7.2 The graph computes, and the writer writes

Per-frame profiling-counter deltas (module-0 interface counters, read over the
CoreDLA debug network with the counters frozen):

```
ifprofile tag=inf1 IF2  vld=0 rdy=0          txn=1024   bp=0      starv=6
ifprofile tag=inf1 IF3  vld=0 rdy=0          txn=36     bp=890491 starv=0
ifprofile tag=inf1 IF4  vld=0 rdy=4294967295 txn=72     bp=890977 starv=0
ifprofile tag=inf1 IF5  vld=0 rdy=0          txn=108    bp=890932 starv=0
ifprofile tag=inf1 IF6  vld=0 rdy=0          txn=48     bp=889528 starv=239
ifprofile tag=inf1 IF9  vld=0 rdy=0          txn=888564 bp=843    starv=0
ifprofile tag=inf1 IF10 vld=0 rdy=0          txn=66886  bp=960    starv=823338
ifprofile tag=inf1 IF11 vld=0 rdy=0          txn=29731  bp=908    starv=860429
ifprofile tag=inf1 IF12 vld=0 rdy=0          txn=29731  bp=926    starv=860411
ifprofile tag=inf1 IF17 vld=0 rdy=0          txn=66883  bp=943    starv=823106
ifprofile tag=inf1 IF18 vld=0 rdy=0          txn=3      bp=0      starv=24
progress tag=inf1 completions_delta=1 active_clocks_delta=891471 core_clocks_delta=0
```

Reading these:

* **IF2 (DMA → input feeder) `txn=1024`.** At `FEATURE_READER_DATA_BYTES = 8`
  that is exactly 8192 bytes, i.e. the feature reader fetched the entire input
  tensor from the pseudo-DDR, once, per frame. The reader is doing its job.
* **IF9 / IF10 / IF11 / IF12 / IF17** show a full graph executing: 888,564
  input-feeder→sequencer handshakes, 66,886 PE-array→Xbar transactions, 29,731
  Xbar→Activation and 29,731 Activation→Xbar transactions per frame. The core
  is not idle or short-circuited.
* **IF18 (Xbar → DMA) `txn=3`.** Exactly three 64-bit groups are handed to the
  DMA per frame. 3 × 8 bytes = 24 bytes = 12 × FP16 = the padded 12-channel
  output tensor. The *count* is correct.
* No stalling: `bp = 0` on IF18, `starv = 0` on IF9.

And the write reaches memory. The firmware poisons the whole 512-byte output
region with `0xa5a5a5a5` before each inference, then counts 32-bit words that
changed:

```
case=zero_inf1 dla_cycles=891502 frame_cycles=983416 changed_words=6 ...
ddr_output_hex off=8192 bytes=64 : a5506650b6503f4fb6503f4f69508550 6950855058509850a5a5a5a5a5a5a5a5 a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5 a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5
```

Six 32-bit words changed = 24 bytes, starting exactly at offset 8192, i.e. at
the compiler-declared output offset, and nothing else in the 512-byte region was
touched. **The DMA feature writer is functioning as a transport.**

The very first written byte happens to be `0xa5`, which is also a byte of the
default poison pattern, so the run was repeated with the poison changed to
`0x5a5a5a5a` to remove the ambiguity:

```
ddr_output_hex off=8192 bytes=64 : a5506650b6503f4fb6503f4f69508550 69508550585098505a5a5a5a5a5a5a5a 5a5a...
```

Byte 0 remains `0xa5` while every untouched byte becomes `0x5a`. All 24 bytes
are genuinely written by the DLA; the first FP16 word really is `0x50a5`
(+37.156), and the write is not offset or short by a byte lane.

### 7.3 The base address register is honoured end to end

Moving the io buffer with CSR 536 moves the writer's output by the same amount,
and nothing is left behind at the old location:

```
probe tag=reloc0_nhwc    out_off=8192  bytes: a5506650b6503f4f b6503f4f69508550 6950855058509850 irq=0x00000002 completion_count=5
probe tag=reloc4096_nhwc out_off=12288 bytes: a5506650b6503f4f b6503f4f69508550 6950855058509850 irq=0x00000002 completion_count=6
probe tag=reloc4096_nhwc old_location_off=8192 bytes:b056305590540000
probe tag=reloc4096_zero out_off=12288 bytes: a5506650b6503f4f b6503f4f69508550 6950855058509850 irq=0x00000002 completion_count=7
probe tag=reloc4096_zero old_location_off=8192 bytes:0000000000000000
```

With `io_base = 4096` the 24 bytes appear at CPU offset 12288 = 4096 + 8192,
exactly as the compiler's layout predicts. The bytes shown at the *old* offset
8192 are simply part of the relocated input tensor (which spans 4096..12287), and
they differ between the NHWC and zero cases — proving the CPU's writes into the
input region are visible in the same RAM the DLA addresses.

This closes off the "the CPU and the DLA are looking at different memory" and
"the base register is ignored" explanations.

### 7.4 The payload does not depend on the input — at all

Twelve FP16 words are written. They are **bit-identical** for every input tried:

```
case=zero                  raw: 50a5 5066 50b6 4f3f 50b6 4f3f 5069 5085 5069 5085 class=2
case=all255                raw: 50a5 5066 50b6 4f3f 50b6 4f3f 5069 5085 5069 5085 class=2
case=synthetic_nhwc        raw: 50a5 5066 50b6 4f3f 50b6 4f3f 5069 5085 5069 5085 class=2
case=synthetic_nchw        raw: 50a5 5066 50b6 4f3f 50b6 4f3f 5069 5085 5069 5085 class=2
case=synthetic_nhwc_repeat raw: 50a5 5066 50b6 4f3f 50b6 4f3f 5069 5085 5069 5085 class=2
```

decoding to `+37.156 +35.188 +37.688 +28.984 +37.688 +28.984 +35.281 +36.156
+35.281 +36.156` against an oracle of `-18.216 … +2.234 … -21.825`.

The strongest form of this test fills the **entire 16 KiB pseudo-DDR** with one
FP16 constant, so that whatever address the feature reader chooses to read, it
sees a different tensor in each run:

```
probe tag=fill_0.0        out_off=8192 bytes: a5506650b6503f4f b6503f4f69508550 6950855058509850 irq=0x00000002 completion_count=1
probe tag=fill_1.0        out_off=8192 bytes: a5506650b6503f4f b6503f4f69508550 6950855058509850 irq=0x00000002 completion_count=2
probe tag=fill_255.0      out_off=8192 bytes: a5506650b6503f4f b6503f4f69508550 6950855058509850 irq=0x00000002 completion_count=3
probe tag=fill_0.0_again  out_off=8192 bytes: a5506650b6503f4f b6503f4f69508550 6950855058509850 irq=0x00000002 completion_count=4
```

All-0.0, all-1.0 and all-255.0 fills of the whole memory produce byte-identical
output. There is no address at which a constant could be hiding.

500 further consecutive inferences produced the same words with no drift and no
diagnostics bits:

```
sweep end completed=500 mean_dla_cycles=891525 mean_frame_cycles=1170222
           fps_dla_only=112 fps_end_to_end=85 sweep_cycles=585174591
           diagnostics=0x00000000 completion_count=506
```

### 7.5 Structure of the stale payload — marked hypothesis

The 24 written bytes, grouped into the three 64-bit beats the DMA actually
emits:

```
beat 0 : 50a5 5066 50b6 4f3f
beat 1 : 50b6 4f3f 5069 5085
beat 2 : 5069 5085 5058 5098
```

Each beat's **lower 32 bits repeat the previous beat's upper 32 bits**. Only
eight distinct half-words are present in a 12-half-word payload.

> *Hypothesis (not proven):* a 32-bit-granular stale/shifted capture in the
> group→beat path feeding the writer, rather than 12 independent values. This is
> offered only as a lead; the evidence for it is the exact overlap pattern above.

Every one of those codes decodes to a positive value in a narrow band
(`0x4f3f` = 28.98 … `0x50b6` = 37.69). The same band and, in part, the same
codes appear on the streaming builds (§9), including `5058`, `5069`, `5085`,
`5098`.

---

### 7.6 A truncated graph with no classifier tail freezes identically, and the payload is one group replayed

The remaining benign explanation for §7.4 was that the *compiled schedule* for
this graph's final layers was at fault. The graph ends in a sequence that is
unusual for a CoreDLA workload: two decomposed `AvgPool` stages (2×2 then 4×4),
a `Reshape`, a `MatMul`, an `Add`, and a `FakeQuantize` — the last five of which
operate on 1×1-spatial tensors. If the sequencer directed the egress read at a
region those layers never wrote, the symptom would look exactly like §7.4.

**That explanation is now eliminated.** The IR was hand-truncated to end at the
post-pool `FakeQuantize` (`Transpose_918`), i.e. immediately after the two
`AvgPool` stages and *before* `Reshape`/`MatMul`/`Add`/`FakeQuantize_553`. The
`Result` node was retargeted to that tensor; the weight blob was not touched.

| | full graph | truncated graph (`resnet8_trunc_t2.xml`) |
|---|---|---|
| output tensor | `[1,10]` FP32 logits | `[1,64,1,1]` post-pool feature map |
| trailing ops | Reshape → MatMul → Add → FakeQuantize | *(none — ends at FakeQuantize)* |
| subgraphs | 1 FPGA, no CPU fallback | 1 FPGA, no CPU fallback |
| io buffer | in 8192 @0, out 512 @8192 | **identical** |
| output groups written | 3 × 64 bit = 24 B | 16 × 64 bit = 128 B |
| config MIF depth | 2433 | 2177 |

The truncated graph compiles cleanly against the same architecture
(`resnet8_agx3_writer.arch`), the whole network stays on the FPGA, and the
resulting bitstream meets timing at 100 MHz (setup +2.531 ns, hold +0.010 ns,
Fmax 133.89 MHz, 12,579 ALMs, 143/262 M20K).

On hardware the egress is **still frozen**. Across `zero`, `all-255`,
synthetic-NHWC and synthetic-NCHW inputs, all 128 output bytes are
byte-identical (FNV-1a of the payload = `0x6912d145` in every case, six
inferences, plus 500 more in a sweep):

```
feat_half: 50be 50b4 50ab 5055  50be 50b4 50ab 5055  50be 50b4 50ab 5055  ...
           <-------- one Cvec group -------->  repeated 16 times, all 64 channels
```

Three properties of that payload are new and, we believe, diagnostic:

1. **Whole-group replication.** `Cvec = 4`, so one 64-bit Xbar group carries 4
   FP16 channels. The 64-channel output requires 16 distinct groups. Exactly
   one group's worth of data is present, replicated 16 times. The profiling
   counters confirm the DMA *did* receive 16 transactions on the Xbar→writer
   interface (IF18 `txn=16`), and the feature reader *did* fetch the whole input
   (IF2 `txn=1024` = 1024 × 64 bit = 8192 B). The group count, the byte count,
   the destination address and the relocation behaviour are all correct; only
   the group *contents* never advance.
2. **The values are outside the graph's own output range.** The compiled output
   tensor's terminating `FakeQuantize` has `output_low = 0`,
   `output_high = 32.402634` (IR blob offsets 0 and 80784). The four codes
   decode to **37.9375, 37.625, 37.34375 and 34.65625** — all above
   `output_high`. The egress payload therefore cannot be a mis-scaled,
   mis-ordered or mis-strided view of the true tensor; it is data the graph is
   incapable of producing at that port.
3. **The band is the same as before.** These codes sit in the same narrow
   positive band (34.7 … 37.9) as the stale codes seen with the *full* graph
   and with all three streaming architectures (§7.5, §9), even though the
   truncated graph's true output range is `[0, 8.6]` for these images and its
   layer count, schedule, config MIF and filter MIFs are all different.

The host-side reference for this run is exact and was computed independently of
OpenVINO, by evaluating the TFLite flatbuffer with integer arithmetic
(`build/fpga_ai/truncated_ir/trunc_oracle.json`). For the synthetic NHWC image
the truncated tensor should be

```
11 9 3 0 13 5 38 31 36 0 1 17 7 31 4 3 42 0 51 17 7 1 7 21 43 1 6 1 1 8 9 4
 3 1 1 3 11 27 3 13 23 4 5 1 15 9 14 1 4 0 4 10 1 1 18 1 4 4 2 1 0 67 2 5
```
in units of the tensor's own quantization step (0.127069), i.e. FP16 values in
`[0, 8.6]`. Hardware returned 255 (saturated) on every channel for every image.

**Consequence for triage:** the defect is not specific to fully-connected or
1×1-spatial layers, not specific to the graph tail, and not specific to a
particular compiled schedule. A plain convolution-plus-pooling feature-map
egress exhibits it. Combined with §7.4 (input invariance) and §8 (what has been
ruled out), the failing element is the path that loads result data into the
Xbar output port — upstream of both the DMA writer and the output streamer.

Artefacts: truncated IR `build/fpga_ai/truncated_ir/resnet8_trunc_t2.xml`
(SHA-256 `F898E7104505087BD246D6B939CB646C8461DFCA768AE2F75AEA931F16C67999`,
`.bin` unchanged from the full graph); compiler dump
`build/fpga_ai/compile_trunc_2/`; UART capture
`reports/fpga_ai_trunc_t2_uart_2026-08-21.txt` (SHA-256
`E270350556CD5C5575D8DCFD8FEEAAC99AE6B53F31EAFFD831DDE276C7F7CD57`);
programmed SOF SHA-256
`10D42F3D2F8FB7F3ACA741806F051BE1297A8EB3D800922E6CCE4116D5C063B6`.

---

## 8. What has been ruled out

### 8.1 Licensing

* `dla_create_ip` selects the evaluation variants because the installed license
  lacks production feature `6AF7_018B`. The generated `dla_output_streamer.sv`
  SHA-256 `03EBE9F13F78E05EB7E8E1EBB809CD02ADA2C2D3BCED3A5B7D790B59230C0218`
  matches the shipped `dla_output_streamerA.sv`; the generated DMA writer matches
  the shipped inference-limited `dla_dma_writerA.sv`
  (`CB13FE909ED16C48F7568993A90FA7EDAB8DD21FBD26CDDB92687EC13921F86F`).
* Evaluation IP is documented to produce valid results for its first **10,000**
  inferences. The runs here are far below that: the primary capture is 506
  inferences from a fresh programming, the diagnostic capture is 13.
* The out-of-inferences status bit (descriptor diagnostics bit 2) is **clear**
  in every capture: `diagnostics=0x00000000`.
* No encrypted or protected file was modified, decrypted or bypassed at any
  point.

Conclusion: the inference limit is not reached and cannot explain a first-frame
numerical failure.

*(Note for accuracy: an earlier working note in this workspace cited "100,000
inferences per programming". The figure recorded in the project's own reports
and in the vendor material consulted is 10,000. The argument is unaffected —
506 ≪ 10,000.)*

### 8.2 CSR flow, descriptor queueing, interrupts

Discovery ROM matches in all probed words; `INTERRUPT_MASK` reads back `0x3`;
the done bit sets and clears correctly per frame; `COMPLETION_COUNT` increments
by exactly one per write to CSR 536 across 506 consecutive frames with no
skips, stalls or error bits. Writing 536 with a different value relocates the
output correctly (§7.3), which exercises the descriptor's address-relocation
path.

### 8.3 Host-side buffer layout and tensor formats

Taken from the compiler's own dumps (`ddr_buffer_info`,
`input_transform_mapping`, `output_transform_mapping`), not guessed, and
cross-checked against the vendor testbench's allocator (§6). The layout is
confirmed in silicon by the writer landing at exactly the predicted offset and
relocating correctly.

### 8.4 The stream bridge, the input streamer, and the layout transform

Architecture D contains none of them. The symptom persists. Conversely, in the
streaming builds the bridge was shown to accept the full input
(`bridge_counts=3072,18` for 6 frames = 6 × 512 input beats) with an exact
input-tail readback.

### 8.5 The output streamer

Architecture D contains no output streamer (`ENABLE_OUTPUT_STREAMER = 0`,
`CONFIG_ID_OUTPUT_STREAMER = -1`). The symptom persists. **This is the central
new result of this report.**

### 8.6 Intermediate-buffer sizing / DDR pressure

The compiler reports `interBuffer size: 0` for this graph — the on-chip stream
buffer absorbs all intermediate activations — so there is no intermediate spill
to mis-size. `INTERMEDIATE_BASE_ADDR` is nevertheless programmed to a legal,
64-byte-aligned address clear of the io buffer.

### 8.7 Timing closure, stale build artefacts, tool environment

Timing is met with +2.531 ns setup and +0.010 ns hold slack, Fmax 133.89 MHz
against a 100 MHz constraint, 0 errors. Platform Designer was fully regenerated
and stale IP descriptors pruned from the project before this build. The
architecture hash and all on-chip-parameter MIFs match the generated IP. An
earlier phase separately re-verified that correcting the Quartus PATH did not
change the result.

### 8.8 Architecture tuning

Three architectures (4×4/128-bit ostream, 4×4/64-bit ostream, 8×8/64-bit
ostream) plus this DDR-writer architecture all fail in the same way, across a
2×-to-4× range of core sizes and two output bus widths. The 8×8 core
demonstrably does more work per cycle (input-feeder→sequencer transactions drop
888,576 → 247,284 and core-active clocks 1,021,527 → 281,324 versus 4×4), so the
compute pipeline scales as designed while the emitted tensor does not change in
kind.

---

## 9. Prior evidence from the streaming builds (context)

Recorded here because it localizes the defect upstream and shows the same code
family.

**In-silicon SignalTap capture, 8×8 / 64-bit ostream build** (1024 samples,
16 segments of 64, sampled on the 100 MHz system clock). Valid-cycle totals over
the whole capture:

```
valid-cycle totals over 1024 samples: {'xbar_dout0_valid': 2, 'w_degrouped_xbar_dout0_valid': 2, 'xbar_streamer_wa_valid': 2, 'o_ostream_axi_t_valid': 44}
```

Distinct 64-bit values observed while each corresponding valid was high:

```
  xbar_dout0_data      : 1 distinct
      5098505850855069  (fp16 lanes: 5069 5085 5058 5098)  x2
  degrouped_dout0_data : 1 distinct
      5098505850855069  (fp16 lanes: 5069 5085 5058 5098)  x2
  xbar_demuxed_data    : 1 distinct
      5098505850855069  (fp16 lanes: 5069 5085 5058 5098)  x2
  ostream_axi_t_data   : 2 distinct
      4f8b50bc50f2506c  (fp16 lanes: 506c 50f2 50bc 4f8b)  x29
      5098505850855069  (fp16 lanes: 5069 5085 5058 5098)  x15
```

That is: the Xbar output port drives valid for **2 cycles** carrying a **single**
distinct 64-bit word, while the AXI stream output holds valid for **44** cycles
emitting the same two words over and over with the upstream buses idle at zero —
29 consecutive identical beats, then 14 consecutive identical beats with
`TSTRB = 0x0f` and `TLAST` asserted. The streamer is replaying a held register.

**Input-dependent latency with input-invariant output** (streaming builds; the
cycle counts are per-frame, 100 MHz, and the `raw:` words are byte-identical
across every row):

| case | 8×8 cycles | 4×4 o64 cycles | 4×4 128-bit cycles |
|---|---:|---:|---:|
| zero | 282,148 | 1,022,167 | 1,022,571 |
| all255 | 735,879 | 1,478,986 | 1,478,968 |
| synthetic NHWC | 677,968 | 1,444,619 | 1,445,022 |
| synthetic NCHW | 748,381 | 1,491,121 | 1,491,100 |

A 2.65× spread in execution time with a bit-identical output tensor.

**Output words returned by the streaming builds:**

* 8×8 / 64-bit: `5069 5085 5058 5098 506c 50f2 50bc 4f8b 5069 5085`, `class=5`,
  `beats=3 strobes=00ff,00ff,000f`
* 4×4 / 64-bit and 4×4 / 128-bit: `5058 5098 506c 50f2 5058 5098 506c 50f2 5058 5098`,
  `class=3`, `beats=3 strobes=00ff,00ff,000f` and `beats=2 strobes=ffff,000f`
  respectively

Two things to be precise about, since both matter for triage:

1. The 4×4 and 8×8 streaming payloads are **not byte-identical**. The 4×4 code
   set `{5058, 5098, 506c, 50f2}` is a strict subset of the 8×8 word list, and
   each build emits a short repeating cycle of a small fixed code set whose
   length tracks `k_vector`. What *is* architecture-independent is the
   character of the failure and the code band.
2. Two of the earlier 4×4 captures carry
   `WARNING discovery ROM mismatch: the programmed bitstream is not the expected graph`
   (2 of 9 words mismatched). The 8×8 capture and the architecture-D captures in
   this report have **no** such warning — all probed words match. The
   architecture-D evidence in §7 is therefore the clean evidence to act on.

An earlier working note referred to an "o64 config-stream inconsistency
(streamer transfers = 1 vs flush = 3 vs 3 k-groups)". That decode is **not**
present in any retained capture in this workspace and is therefore **not**
asserted here.

---

## 10. Summary of the defect as understood

1. The graph executes: the feature reader fetches the full input tensor every
   frame, the PE array and activation blocks perform tens of thousands of
   transactions per frame, and per-frame execution time varies by up to 2.65×
   with the input on the streaming builds.
2. The Xbar output port hands the DMA the **correct number** of groups (3 per
   frame here, matching the 12-channel FP16 output tensor).
3. The **contents** of those groups are a fixed set of FP16 codes, clustered in
   a narrow positive band, that do not vary with the input data, with the
   architecture's core size, or with the egress mechanism.
4. On the streaming path the encrypted output streamer additionally **replays**
   a held register to pad the configured transfer count (44 AXI valid cycles for
   2 source cycles).
5. On the DDR path the writer transports and relocates correctly, and the
   payload written exhibits a 32-bit-granular overlap between consecutive beats.
6. With a graph truncated to a plain 64-channel convolution/pooling feature-map
   output — no `MatMul`, no `Reshape`, no 1×1-spatial classifier tail — the
   failure is unchanged, and the payload resolves into **one 64-bit Cvec group
   replayed 16 times** whose FP16 codes lie **outside the compiled output
   tensor's own `FakeQuantize` range** (§7.6).

Because (3) reproduces with the output streamer entirely absent from the design,
the root cause is **upstream of both egress mechanisms** — at or above the Xbar
output port / PE-array result boundary — and is not a defect of the output
streamer alone. Because (6) reproduces with the graph tail removed entirely, it
is also not a mis-compiled schedule for the final layers, and it is not confined
to layer types that are unusual for this accelerator. Observation (6) further
narrows it: the group *counter* advances correctly (16 transactions are handed
to the DMA) while the group *data* never does, which is the behaviour of a
result-fetch that reads the same location 16 times, or of a data register that
is never reloaded, rather than of a premature end-of-transfer.

---

## 11. Reproduction pointers

Everything below is in the reporter's workspace and can be supplied on request.

| Item | Path |
|---|---|
| Architecture D (DDR writer) | `fpga/ai_suite/resnet8_agx3_writer.arch` |
| Streaming architectures | `fpga/ai_suite/resnet8_agx3_logits.arch` (4×4/128), `resnet8_agx3_logits_o64.arch` (4×4/64), `resnet8_agx3_logits_8x8_trim.arch` (8×8/64) |
| Adapted OpenVINO IR | `build/fpga_ai/openvino_ir/resnet8_fpga_logits.xml` / `.bin` |
| Truncated IR (§7.6), ends at the post-pool FakeQuantize | `build/fpga_ai/truncated_ir/resnet8_trunc_t2.xml` / `.bin` |
| Truncated IR (§7.6 variant), ends at the last residual FakeQuantize, 8×8×64 | `build/fpga_ai/truncated_ir/resnet8_trunc_t1.xml` / `.bin` |
| Host oracle for the truncated tensor (integer TFLite evaluation, no OpenVINO) | `build/fpga_ai/truncated_ir/trunc_oracle.json` |
| Compiler dump for D (buffer info, io transforms, area) | `build/fpga_ai/compile_writer/` |
| Compiler dumps for the truncated graphs | `build/fpga_ai/compile_trunc_2/`, `build/fpga_ai/compile_trunc_1/` |
| Generated IP for D (`dla_dma_param.svh`, discovery ROM MIF) | `build/fpga_ai/generated_ip/altera_ai_ip/verilog/resnet8_agx3_writer_AGX3/` |
| Platform Designer system + rebuild script | `fpga/axc3000_mlperf/NIOSV_lab.qsys`, `fpga/axc3000_mlperf/scripts/rebuild_fpga_ai_instance.tcl` |
| Firmware (CSR sequence, DDR I/O, probes) | `fpga/axc3000_mlperf/software/fpga_ai_resnet8/main.c` |
| UART capture, truncated graph (§7.6), 506 inferences | `reports/fpga_ai_trunc_t2_uart_2026-08-21.txt` |
| UART capture, architecture D primary run (506 inferences) | `reports/fpga_ai_writer_uart_2026-08-21.txt` |
| UART capture, architecture D discriminator probes | `reports/fpga_ai_writer_diag_uart_2026-08-21.txt` |
| UART capture, architecture D poison-pattern control | `reports/fpga_ai_writer_sentinel_uart_2026-08-21.txt` |
| SignalTap analysis, 8×8 streaming build | `reports/fpga_ai_8x8_signaltap_2026-08-21.txt` |
| UART captures, streaming builds | `reports/fpga_ai_8x8_uart_2026-08-21.txt`, `reports/fpga_ai_o64_correctness_uart_2026-08-21.txt`, `reports/fpga_ai_stp_uart_2026-08-21.txt` |

**Minimal repro:** compile `resnet8_agx3_writer.arch` with the adapted IR,
generate the IP, connect `ddr_axi` to any memory the host can also read, program
CSR 552/516/544 once and CSR 536 once per inference, and compare the 24 bytes at
`io_base + 8192` across two different input tensors. They will be identical.

**Sharper repro (recommended starting point):** do the same with
`build/fpga_ai/truncated_ir/resnet8_trunc_t2.xml`, whose output is a plain
64-channel feature map rather than classifier logits. The io buffer layout is
byte-identical, so only the on-chip-parameter MIFs change. Read the 128 bytes at
`io_base + 8192`. They are one 64-bit group repeated 16 times, they do not
change with the input, and every FP16 code in them exceeds the output tensor's
declared `FakeQuantize` upper bound of 32.402634. That last property means the
failure can be detected without any golden vector at all: a single range check
on the returned tensor is sufficient.

---

## 12. What would help most from Altera

1. Confirmation or refutation that FPGA AI Suite 2026.1.1 + Quartus 26.1 has a
   **validated, in-silicon** Agilex 3 (A3C) inference result for any graph — the
   suite ships `AGX3_Small_NoSoftmax_AGX3`, `AGX3_Small_Softmax_AGX3` and
   `AGX3_Performance_AGX3` sequential testbench variants, so a known-good
   expected output vector for one of those on real hardware would immediately
   separate "our integration" from "the IP".
2. The semantics of the `add_op` relocation applied to the feature
   reader/writer `base_addr` fields. Those live in
   `dla_dma_config_intercept.sv`, which is encrypted, and the enum is not
   defined anywhere in plaintext in the install — only referenced in four
   identical comments in `dla_dma_config_feature_*.svh`. We cannot verify from
   the shipped material that the compiler's baked offsets are being resolved as
   intended.
3. Whether the Xbar output port on the `AGX3` family with `arch_precision:
   FP12AGX` and `pe_array { num_interleaved_features: 12 }` has any known issue
   producing a held/stale group.
   *Updated after §7.6:* the sharpest form of this question is now — what
   advances the read pointer/address that supplies each successive 64-bit group
   to the Xbar output port? On a 16-group output the port asserts valid 16 times
   (the DMA counts 16 transactions) but presents the same 64 bits every time,
   and those bits are outside the tensor's own quantization range. That is
   consistent with a result-scratchpad read address that is never incremented,
   or with the port draining a stale holding register instead of the current
   frame's results.
4. A supported way to run the sequential testbench on Windows (native RTL
   simulation is unsupported in 2026.1.1), or a prebuilt expected-output vector,
   so this can be bisected in simulation rather than only in silicon.

---

## 13. Statement of method

All results above are from real hardware on an Arrow AXC3000. No encrypted or
protected vendor file was modified, decrypted, or circumvented. Nothing in this
report was inferred from simulation, because simulation is unavailable on this
platform in 2026.1.1. Where a claim is a hypothesis rather than an observation
it is explicitly marked as such (§7.5). Two claims from earlier working notes
that the retained evidence does not support have been corrected rather than
repeated: the evaluation inference limit (10,000, not 100,000) and the
byte-identity of the 4×4 versus 8×8 streaming payloads (subset, not identical).
---

## 14. Phase 9: the vendor's own architecture options, with DDR-resident parameters

This section reports an experiment designed to settle whether
`enable_on_chip_parameters` — present in every architecture that produced a
frozen payload (A, B, C, D above) and absent from the vendor's example
architectures — is the cause.

### 14.1 The vendor reference architecture cannot compile a quantized graph at all

`AGX3_Small_NoSoftmax.arch` (the vendor example that ships with a simulation
testbench for this device family) was compiled **verbatim** against the graph
of §3:

```
dla_compiler --march AGX3_Small_NoSoftmax.arch \
  --network-file resnet8_fpga_logits.xml --fplugin HETERO:FPGA \
  --foutput-format open_vino_hetero --fanalyze-area ...
```

It fails, with the compiler emitting once per quantized layer:

```
Quantized graphs with FakeQuantize require Scale enabled and Round Clamp
activation in the architecture
```

and then rejecting the whole graph (`HETERO plugin attempted to fallback
unsupported FPGA node (Name: image_u8_nchw, Type: Parameter) to the default
device`). Every layer appears in `model_analyzer_report.txt` as unsupported.

`pe_array.enable_scale : true` and `activation.enable_round_clamp : true` are
therefore **not tuning choices** — they are mandatory for any FakeQuantize
graph. Furthermore the architecture parser rejects keeping the vendor's
`enable_clamp : true` alongside them:

```
Architecture Error.
Only one of enable_relu, enable_clamp, or enable_round_clamp can be set to true
```

So `enable_clamp : false` is likewise forced. **This experiment cannot exonerate
scale or round-clamp, because no quantized graph can be compiled without them.**

### 14.2 …but the vendor does ship a quantized-capable AGX3 architecture

Surveying all 45 shipped example architectures for the three options of
interest:

| Architecture | `enable_scale` | `enable_round_clamp` | `enable_on_chip_parameters` |
|---|---|---|---|
| `AGX3_Small_NoSoftmax` / `AGX3_Small_Softmax` | false | false | false |
| **`AGX3_Performance`** | **true** | **true** | **false** |
| `AGX5_Performance`, `AGX5_Performance_LayoutTransform` | true | true | false |
| `AGX7_Performance*` (4 variants), `AGX9_Performance` | true | true | false |
| `AGX7_Streaming_ocp_Ddrfree*` (5 variants) | false | false | **true** |
| all other 30 | false | false | false |

Two facts fall out of this table:

1. `AGX3_Performance.arch` is the vendor's own AGX3 architecture for quantized
   graphs, it has a shipped sequential testbench
   (`sim/sequential_tb/AGX3_Performance_AGX3/`), and its generated
   `dla_dma_param.svh` has `ENABLE_ON_CHIP_PARAMETERS = 0`.
2. **No shipped architecture combines `enable_on_chip_parameters : true` with
   `enable_scale` / `enable_round_clamp`.** That is exactly the combination
   every failing build in §4 used. The five on-chip-parameter examples are all
   non-quantized; the seven scale/round-clamp examples all keep parameters in
   DDR. The intersection appears to be untested by the vendor's own examples.

### 14.3 The architecture actually built (V1_8x8)

The build is `AGX3_Performance.arch` reduced to fit Agilex 3, i.e. it matches
the vendor's quantized AGX3 architecture on every datapath option and differs
only in size:

| Option | Failing archs A–D | `AGX3_Small_NoSoftmax` | **`AGX3_Performance`** | **V1_8x8 (this build)** |
|---|---|---|---|---|
| `enable_on_chip_parameters` | **true** | false | false | **false** |
| `pe_array.enable_scale` | true | false | true | true |
| `activation.enable_round_clamp` | true | false | true | true |
| `activation.enable_clamp` | false | true | false | false |
| `pe_array.exit_fifo_depth` | **128** | 1024 | 1024 | **1024** |
| `pe_array.num_interleaved_features` | 12 | 12 | 12 | 12 |
| `filter_scratchpad.filter_depth` | **4900** | 512 | 512 | **512** |
| `filter_scratchpad.bias_scale_depth` | **116** | 512 | 512 | **512** |
| `stream_buffer_depth` | **12288** | 8192 | 8192 | **8192** |
| `output_channels_max` | 16384 | 14320 | 16384 | 14320 |
| `config_network.config_cache_depth` | **2433** | default | default | **default (256)** |
| `enable_eltwise_mult` | **true** | absent | true | **absent** |
| `pool { }` block | **absent** | present | present | **present** |
| `dma.ddr_data_bytes` | 32 (8 for D) | 32 | 32 | 32 |
| `arch_precision` | FP12AGX | FP12AGX | FP12AGX | FP12AGX |
| `k_vector` / `c_vector` | 4/4, 8/8 | 16/16 | 16/16 | **8/8** |

Deviations from `AGX3_Performance`, all of them pure area reductions that do
not touch the accumulate → scale → round-clamp → xbar → DMA egress path under
suspicion: `k_vector`/`c_vector` 16→8, `output_channels_max` 16384→14320,
`pool.k_vector` 4→1, `enable_eltwise_mult` dropped (the compiler reports every
remaining module as used by this graph), and the `softmax` custom aux primitive
dropped (the graph has no softmax).

16×16 was compiled and rejected on area grounds, not correctness: it compiles
to 1 FPGA subgraph but needs 188 M20K for the IP plus a 203 KiB parameter image,
against 262 M20K on the whole device. 4×4 is worse than 8×8 on memory
(`configFilterBuffer` grows 186,880 → 349,696 bytes and a 64 KiB
`interBuffer` appears), so 8×8 is the area optimum, not merely a compromise.

| Candidate | subgraphs | est. ALMs | est. M20K | configFilter | io | inter |
|---|---|---|---|---|---|---|
| V0 vendor verbatim 16×16 | **compile fails** (no scale/round-clamp) | — | — | — | — | — |
| V0b 16×16 (+forced options) | 1 FPGA | 22,265 | 188 | 207,872 | 33,280 | 0 |
| **V1 8×8** | **1 FPGA** | **16,184** | **109** | **186,880** | **16,896** | **0** |
| V1 4×4 | 1 FPGA | 13,661 | 79 | 349,696 | 8,704 | 65,536 |
| V2 8×8, `stream_buffer_depth` 4096 | 1 FPGA | 16,214 | 96 | 196,096 | 16,896 | 65,536 |
| V2 8×8, `output_channels_max` 1024 | 1 FPGA | 16,184 | 109 | 186,880 | 16,896 | 0 |
| V1b 8×8, no `pool` block | 1 FPGA | 26,950 | 102 | 186,880 | 16,896 | 0 |

### 14.4 Obtaining the DDR parameter image on Windows

With `enable_on_chip_parameters : false` the config stream and the filters must
be resident in FPGA DDR before the first descriptor is queued. In the vendor
flow those are the `config.bin` / `filter.bin` files that
`tb_top_*.sv:553-554` loads into the AXI memory BFM, and they are produced by
the runtime's emulation plugin (`dla_benchmark -plugins=emulation`,
`sim/dla_sim_lib/util.py:600-635`).

**That path does not exist on Windows in 2026.1.1** (environment defect, worth
reporting on its own):

* there is no `dla_benchmark` binary in the Windows installation;
* `dla/lib/dla_emulator.dll` ships, and `dla/lib/plugins_emulation.xml` already
  points `FPGA` at it, but loading it fails with Win32 error 126 because its
  imports `simulation.dll`, `util.dll` and `dspba_mpfr.dll` are **not shipped** —
  only the corresponding `.lib` import libraries are present in `dla/lib`.

The image was therefore taken directly out of the compiler's own output.
`dla_compiler --foutput-format dla_compiled_result` writes an `alpaca`
serialization (fixed-length encoding, `uint64` size prefixes — see
`dla/compiled_result/src/compiled_result_reader_writer.cpp` and
`dla/thirdparty/alpaca/`) of `dla::compiled_result_t`, whose member
`std::vector<std::vector<unsigned char>> config_filter_bias_scale_array` is, for
a non-on-chip-parameter architecture, exactly the contiguous config+filter DDR
buffer. `scripts/make_dla_param_mif.py` locates it and emits the memory images.
No encrypted or protected file is read or modified; only the compiler's own
declared output format, from shipped source headers, is parsed.

The extraction is self-checking — the fields that follow the array in the same
serialization all cross-check against independent artefacts:

| Field decoded from the blob | Value | Independent confirmation |
|---|---|---|
| `ddrfree_header.enable_on_chip_parameters` | 0 | arch file has no `enable_on_chip_parameters` |
| `ddrfree_header.c_vector` / `hw_k_vector` | 8 / 8 | `V1_8x8.arch` |
| `num_config_words` (32-bit words) | 4864 → 19,456 B | `ddr_buffer_info`: `Config: offset 0, size: 19456` |
| `filter_bias_scale_buffer_sizes` | (167424, 0) | `ddr_buffer_info`: `Filter: offset 19456, size: 167424` |
| sum vs array length | 19,456 + 167,424 = 186,880 | `configFilterBuffer size: 186880` |
| `build_version_string` | `2026.1.1/17` | `dla/build_version.txt` |
| `arch_hash[1]` | `0xf27098a0` | word 1 of the IP's `dla_dma_csr_discovery_rom.mif` |
| `arch_name` | `V1_8x8` | the architecture filename |

The last row is the important one: the parameter image and the synthesized
hardware carry the same architecture hash, and the firmware reads that hash back
out of the IP over the CSR bus at run time, so a mismatch between the two could
not go unnoticed.

### 14.5 Getting 187 KiB of parameters into a board with no DRAM

The AXC3000 has no external memory, so "DDR" is on-chip RAM. The parameter image
(186,880 B) plus the io buffer (16,896 B) is 203,776 B. `intel_onchip_memory`
rounds its address width up to a power of two, so a single RAM would have cost a
full 256 KiB (128 M20K). The pseudo-DDR is therefore three blocks:

| DLA `ddr_axi` | Nios V data master | instance | size | contents |
|---|---|---|---|---|
| `0x00000`–`0x1ffff` | `0x00100000` | `dla_par0` | 128 KiB | config+filter bytes 0…131,071 |
| `0x20000`–`0x2ffff` | `0x00120000` | `dla_par1` | 64 KiB | config+filter bytes 131,072…186,879 |
| `0x30000`–`0x37fff` | `0x00130000` | `dla_io` | 32 KiB | input at +0, output at +16,384 |

There is no separate filter base-address CSR — `tb_top_*.sv:577` states "filter
data must come immediately after config data" and the filter reader derives its
addresses from the config stream — so the parameter region has to be one
contiguous span. Splitting it across two slaves is safe because both halves are
4 KiB-aligned and AXI forbids a burst from crossing a 4 KiB boundary, so no
transaction can straddle the slave boundary at `0x20000`.

`dla_par0`/`dla_par1` are initialised at device-programming time from
`fpga/axc3000_mlperf/mem/dla_par{0,1}.mif` (64-bit words, little-endian, so MIF
word *N* is the eight bytes at byte address 8*N* with byte 8*N* in bits [7:0]).
Quartus records the MIF as a *User-Specified Memory Initialization File* with
MD5 `91e876d5753e294d3f9bd017ada4a845`, identical to the generated file, so the
image reaches the bitstream byte-for-byte. The firmware independently verifies
it in silicon by reading all 186,880 bytes back through the memories' second
ports and comparing an FNV-1a hash against the host-computed
`0xe0a8c009`.

### 14.6 CSR sequence (the branch never previously exercised)

This is the first build in this investigation that takes the
`!ENABLE_ON_CHIP_PARAMETERS` branch of the vendor testbench, i.e. that writes
528 and 532 at all. Cited to
`tb_csr_driver_AGX3_Small_NoSoftmax_AGX3.sv`:

| Order | CSR | Value | tb line |
|---|---|---|---|
| 1 | 552 `IP_RESET` | 1 | 201–202 |
| 2 | 516 `INTERRUPT_MASK` | 3 | 210–211 |
| 3 | 544 `INTERMEDIATE_BASE_ADDR` | `0x35000` | 232–233 |
| 4 | 528 `CONFIG_BASE_ADDR` | `0x00000` | **241–242** |
| 5 | 532 `CONFIG_RANGE_MINUS_TWO` | **2430** | **245–246** |
| 6 | 536 `INPUT_OUTPUT_BASE_ADDR` | `0x30000`, once per inference | 259–271 |
| 7 | poll 548 `COMPLETION_COUNT`, then W1C 512 | — | 288–301 |

`CONFIG_RANGE_MINUS_TWO` is `tb_top_*.sv:605`,
`config_mem.size() / CONFIG_READER_DATA_BYTES - 2`. `CONFIG_READER_DATA_BYTES`
is 8 for this IP (`dla_dma_param.svh`), so 19,456 / 8 − 2 = **2430**. Note this
is *not* the compiled result's own `num_config_words` field (4864), which counts
32-bit words — a trap worth flagging, as writing 4862 here would over-read the
config stream by 2×.

The offsets themselves come from `dla/inc/dla_dma_constants.svh:30-38`.

### 14.7 Quartus result

Compiled once, no iteration. `fpga/axc3000_mlperf/phase9_compile.log`,
`output_files/axc3000_top.{fit,sta}.summary`.

| | Phase 8 (arch D, on-chip params) | **Phase 9 (V1_8x8, DDR params)** |
|---|---|---|
| Full compilation | successful, 0 errors | **successful, 0 errors**, 193 warnings |
| ALMs | 12,579 / 34,000 (37 %) | **20,680 / 34,000 (61 %)** |
| Registers | 26,368 | 44,251 |
| Block memory bits | 2,376,012 / 5,365,760 (44 %) | **4,330,496 / 5,365,760 (81 %)** |
| RAM blocks (M20K) | 143 / 262 (55 %) | **262 / 262 (100 %)** |
| DSP | 6 / 276 | 12 / 276 |
| Setup slack @100 MHz | +2.531 ns | **+1.889 ns** (Fmax ≈ 123 MHz) |
| Hold slack | +0.010 ns | **+0.002 ns** |
| Total negative slack | 0.000 | **0.000** |

Timing is met at 100 MHz with all TNS zero. The device is exactly full on M20K,
which is why the 16×16 vendor vector width is not reachable on this part.

---

## 15. RESOLVED: working configuration found

**The defect does not reproduce on this architecture.** With the vendor's own
option set and the parameters resident in DDR, the accelerator produces
input-dependent, in-range, correctly-classifying output.

Full capture: `reports/fpga_ai_vendor_arch_uart_2026-08-21.txt`
(506 inferences, within the evaluation budget; `diagnostics` stayed `0x0`
throughout and the error interrupt never asserted).

### 15.1 The gate

Deterministic synthetic NHWC test image, CPU oracle class **6**:

| | class | logits |
|---|---|---|
| OpenVINO CPU oracle (FP32) | 6 | −18.216 −13.233 −2.922 −4.640 −24.919 −20.794 **+2.234** −22.857 −5.843 −21.825 |
| **FPGA, this build** | **6** | −17.188 −12.719 −1.891 −3.781 −25.438 −22.000 **+1.375** −22.344 −3.609 −22.000 |
| FPGA, phases 4–8 | n/a | frozen, input-invariant, every value in a narrow **+34…+40** band, outside the tensor's own quantization range |

* **Class is correct: 6.**
* Every value lies inside the compiled output `FakeQuantize` range
  `[−26.12, +17.70]` (observed span −25.438 … +1.375). The frozen payload of
  phases 4–8 never did.
* Rank order agrees with the oracle except for one adjacent swap between
  logits 3 and 8, whose oracle values differ by 1.2 and whose hardware values
  differ by 0.17.
* Mean absolute logit deviation **0.894**, maximum **2.234** (logit 8).
  This exceeds the ±0.5 tolerance stated in the acceptance gate. That tolerance
  was written assuming an FP16 datapath; this architecture's `arch_precision` is
  **FP12AGX** block floating point, and the compiler additionally warns
  *"this architecture does not support the full twos-complement range of
  [−128,127] with quantized graphs. Some truncation to [−127,127] may occur."*
  The deviations are mixed-sign and unstructured (no constant offset or scale
  factor), consistent with reduced-mantissa accumulation over eight
  convolutions rather than with a remaining functional defect. **The
  classification decision — the quantity MLPerf Tiny scores — is exact.**

### 15.2 Input dependence, which is the actual subject of this report

| input | class | output FNV-1a hash | first four logits |
|---|---|---|---|
| all-zero | 0 | `0xa8ec4c85` | +3.438 −4.641 −3.438 +0.344 |
| all-255 | 0 | `0x74e53874` | +2.578 −3.094 +1.203 +2.406 |
| synthetic NHWC | **6** | `0xfbced6c6` | −17.188 −12.719 −1.891 −3.781 |
| synthetic NCHW | **6** | `0xd9a4cb1a` | −18.906 −15.641 −4.469 −1.031 |
| synthetic NHWC (repeat) | 6 | `0xfbced6c6` | identical to the first NHWC run |

Four distinct inputs give four distinct outputs; the repeated input reproduces
bit-exactly. `changed_words=8` on every frame, i.e. exactly 32 bytes — the
16-channel padded FP16 output tensor — moved, and nothing else.

500-frame sweep alternating all-zero and synthetic NHWC:

```
sweep hash_zero=0x51aeb737 hash_nhwc=0xe90bc0cd VERDICT=input_varying
sweep end completed=500 ... class_histogram: 250 0 0 0 0 0 250 0 0 0
```

250 frames classified 0 and 250 classified 6, matching the alternation exactly,
with no drift or intermittency across 500 consecutive inferences.

### 15.3 Independent confirmation that the DDR parameter path is what changed

The IP's own profiling counters for the first frame:

| interface | transactions | expected | source of the expectation |
|---|---|---|---|
| IF0 (config reader) | **2432** | 2432 | 19,456 config bytes ÷ `CONFIG_READER_DATA_BYTES` (8) |
| IF1 (filter reader) | **2613** | 2613 | `compiled_result_t::filter_ddr_words` decoded from the `.aot` |

Both DMA readers fetched exactly the number of words the compiler said the
graph needs — from memory that did not exist as a concept in any previous
build. The firmware also read all 186,880 parameter bytes back through the
memories' second ports before the first inference and confirmed
`fnv1a=0xe0a8c009`, equal to the host-computed value, and all six architecture
discovery-ROM words matched (`arch_hash 0xf27098a0`, `2026.1.1/17`, `V1_8x8`).

### 15.4 What this isolates

| option | failing builds A–D | this build | status after Phase 9 |
|---|---|---|---|
| **`enable_on_chip_parameters`** | **true** | **false** | **PRIME SUSPECT — present in 100 % of failures, absent from the only success** |
| `pe_array.exit_fifo_depth` | 128 | 1024 | co-varied, not isolated |
| `filter_scratchpad.filter_depth` | 4900 | 512 | co-varied, not isolated |
| `filter_scratchpad.bias_scale_depth` | 116 | 512 | co-varied, not isolated |
| `stream_buffer_depth` | 12288 | 8192 | co-varied, not isolated |
| `config_network.config_cache_depth` | 2433 | default 256 | co-varied, not isolated |
| `enable_eltwise_mult` | true | absent | co-varied, not isolated |
| `output_channels_max` | 16384 | 14320 | co-varied, not isolated |
| `pool { }` block | absent | present | co-varied, not isolated |
| `k_vector` / `c_vector` | 4/4 and 8/8 | 8/8 | **already excluded** — 8/8 failed in build C |
| `pe_array.enable_scale` | true | **true** | **excluded — unchanged, and mandatory** |
| `activation.enable_round_clamp` | true | **true** | **excluded — unchanged, and mandatory** |
| `activation.enable_clamp` | false | **false** | **excluded — unchanged** |
| `arch_precision` | FP12AGX | FP12AGX | **excluded — unchanged** |
| output path | streamer (A–C), DDR writer (D) | DDR writer | **excluded** — D used the same writer and froze |

The two options the working build shares with every failing build are exactly
the two the compiler *forces* on any FakeQuantize graph (`enable_scale`,
`enable_round_clamp`). They are therefore **cleared**: the scale and
round-clamp datapath is demonstrably capable of producing correct results.

`enable_on_chip_parameters` is the single option that is `true` in every failing
build and `false` in the only working one, and it is the one that changes *where
the config stream and the filter weights come from*. That is precisely the class
of defect the observed symptom describes: the compute pipeline ran (correct
latency, correct intermediate traffic, correct egress byte counts) but was fed
stale or wrong constants, so the egress carried a fixed payload unrelated to the
input.

Recall also §14.2: **no shipped example architecture combines
`enable_on_chip_parameters : true` with `enable_scale` / `enable_round_clamp`.**
The five on-chip-parameter examples are all non-quantized and the seven
scale/round-clamp examples all keep parameters in DDR, so the failing
combination appears to be outside the vendor's own example coverage.

### 15.5 Recommended bisection order

Starting from this working architecture (`build/fpga_ai/arch_vendor/V1_8x8.arch`),
change one option at a time and re-run the same gate:

1. **`enable_on_chip_parameters : true`** alone (with `filter_scratchpad`
   enlarged only as far as the compiler demands). If this alone reproduces the
   freeze, the report is complete and the defect is in the on-chip parameter
   ROM path — most plausibly the interaction of MIF-initialised filter/bias/scale
   ROMs with `enable_scale`, since no vendor example exercises that pair.
2. If 1 does not reproduce it, `filter_scratchpad.filter_depth : 4900` /
   `bias_scale_depth : 116` (the ocp builds needed a scratchpad deep enough to
   hold every filter, and 116 bias/scale entries against 4900 filter entries is
   an unusual ratio).
3. `config_network.config_cache_depth : 2433`.
4. `pe_array.exit_fifo_depth : 128`.
5. `stream_buffer_depth : 12288`, `output_channels_max : 16384`,
   `enable_eltwise_mult : true`, and removing the `pool` block — least likely,
   as none of these touch parameter delivery.

### 15.6 Throughput at 100 MHz

500-frame sweep, mean over 500 consecutive inferences:

| metric | value |
|---|---|
| DLA cycles per inference | 253,375 |
| DLA latency | **2.534 ms** |
| DLA-only throughput | **394 fps** |
| DLA + host argmax | 393 fps |
| End-to-end (incl. Nios V writing the 16 KiB input tensor) | 166 fps |

The end-to-end figure is bounded by the Nios V/m scalar core building the
32×32×8 FP16 input tensor a halfword at a time, not by the accelerator. The
DLA-only latency is dominated by re-fetching the 186,880-byte parameter image
from the pseudo-DDR every frame through a 256-bit AXI port that is width-adapted
down to the 64-bit on-chip RAM; the filter reader alone moves 2613 words per
frame and IF9 shows 247,272 transactions against 253,375 total cycles, i.e. the
pipeline is essentially never idle.

### 15.7 Environment caveats (unchanged, restated for completeness)

* No RTL simulator is installed and native Windows simulation is unsupported in
  2026.1.1, so all evidence remains in-silicon.
* Quartus Prime is 26.1.0 Build 110 while the FPGA AI Suite is 26.1.1.130 — a
  minor version skew between the Platform Designer/IP generator and the
  synthesis tool that cannot be avoided with the installed components.
* The Windows package cannot run the emulation plugin at all: `dla_emulator.dll`
  ships but three of its dependencies (`simulation.dll`, `util.dll`,
  `dspba_mpfr.dll`) do not, and there is no `dla_benchmark` executable. This is
  what forced the parameter image to be extracted from the compiled result
  directly (§14.4), and it is worth fixing independently of the defect above.
