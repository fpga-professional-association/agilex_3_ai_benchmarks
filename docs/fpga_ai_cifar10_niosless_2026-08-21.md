# CIFAR-10 image classification on the Arrow AXC3000 — Nios-less CoreDLA, JTAG host

Date: 2026-08-21
Design: FPGA AI Suite 2026.1.1 CoreDLA, arch `resnet8_agx3_int8_k16c8`, ResNet-8, on-chip pseudo-DDR parameters.
Device: Agilex 3 `A3CY100BM16AE7S` (speed grade 7), Quartus Prime Pro 26.1.0 Build 110.
Board: Arrow AXC3000, cable `USB Blaster III`.

## Headline

**86.33 % top-1 on the full 10,000-image CIFAR-10 test set** — above the 85 %
MLPerf Tiny ic01 quality floor, and 0.17 pp below the 86.5 % reference accuracy.
**84.00 % on the official 200-image ic01 accuracy/performance subset** (168/200;
95 % binomial CI 78.9–89.1 %, i.e. statistically indistinguishable from the
full-set result — see §4.2 for why the full set is the number that counts).

**Single-stream device latency 527.57 µs = 1,895.5 inferences/s**, measured on
the CoreDLA hardware cycle counter, image-independent to the clock cycle:
9,748 of the 10,000 jobs returned *exactly* 52,757 ticks.

10,000 images ran in 235.4 s of wall clock over 2 programming cycles with zero
diagnostics events, zero timeouts, zero stale reads and zero discarded images.

**This is not an official MLPerf Tiny submission.** See §7.

## 1. Design under test

The design is the Phase 12 Nios-less build. There is no soft CPU: the host is a
Python program on the workstation driving an `altera_jtag_avalon_master`
("`jtag_master`") through System Console over the USB Blaster III.

| Property | Value |
|---|---|
| CoreDLA arch | `resnet8_agx3_int8_k16c8` (k_vector 16, c_vector 8, FP12AGX, stream_buffer_depth 8192) |
| Arithmetic | DSP INT9 tensor mode `TENSOR1X2_MULT8`, **24 DSP = 128 INT8 MAC/cycle** |
| `dla_clk` (datapath) | **300.00 MHz** (achieved Fmax 312.50 MHz) |
| `ddr_clk` / `csr_clk` / `irq_clk` | 100.000 MHz (`iopll_0.outclk0`) |
| Peak dot-product rate | 38.4 GMAC/s |
| Parameters | 205,056 B config+filter image, MIF-initialised into on-chip RAM, loaded at FPGA configuration |
| Activations / IO | 16,384 B input tensor, 512 B output tensor, on-chip |
| Off-chip memory | none — the whole model lives in M20K |
| ALM | 23,991 / 34,000 (71 %) |
| M20K | 254 / 262 (97 %) |
| DSP | 24 / 276 (9 %) |

Host address map used by the runner (host = DLA + 0x0010_0000 for every
pseudo-DDR slave):

```
0x0004_0000 .. 0x0004_07FF   fpga_ai.csr_axi   2 KiB
0x0010_0000 .. 0x0013_3FFF   dla_par0/1/2      param image, 205,056 B
0x0014_0000 .. 0x0014_3FFF   dla_io0           input tensor
0x0014_4000 .. 0x0014_4FFF   dla_io1           output tensor (512 B at +0)
```

Model: MLPerf Tiny ic01 ResNet-8, 9 convolutions + 1 fully-connected layer,
**12,501,632 MACs per inference** (computed from the quantised conv descriptors
in `software/resnet8/generated_model.h`, not quoted from literature):

| layer | in | out | kernel | stride | MACs |
|---|---|---|---|---|---|
| conv0 | 32×32×3 | 32×32×16 | 3×3 | 1 | 442,368 |
| conv1 | 32×32×16 | 32×32×16 | 3×3 | 1 | 2,359,296 |
| conv2 | 32×32×16 | 32×32×16 | 3×3 | 1 | 2,359,296 |
| conv3 | 32×32×16 | 16×16×32 | 3×3 | 2 | 1,179,648 |
| conv4 | 16×16×32 | 16×16×32 | 3×3 | 1 | 2,359,296 |
| conv5 (shortcut) | 32×32×16 | 16×16×32 | 1×1 | 2 | 131,072 |
| conv6 | 16×16×32 | 8×8×64 | 3×3 | 2 | 1,179,648 |
| conv7 | 8×8×64 | 8×8×64 | 3×3 | 1 | 2,359,296 |
| conv8 (shortcut) | 16×16×32 | 8×8×64 | 1×1 | 2 | 131,072 |
| fc | 64 | 10 | — | — | 640 |
| **total** | | | | | **12,501,632** |

## 2. Methodology

### 2.1 What "one inference" is

Per image the host performs: a 16,384-byte block write of the pre-transformed
input tensor into `dla_io0`; a poison-sentinel fill of the 512-byte output
window; a read of CSR 548 (completion) and CSR 576 (CLOCKS_ACTIVE); a single
32-bit write of CSR 536 `IO_BASE`, **which is the write that queues the job**;
a poll of CSR 548 until it moves; a re-read of CSR 576; then a 32-byte read of
the output window. The FP16 logits are decoded on the host and `argmax` is the
prediction.

The poison fill is load-bearing: without it a job that silently failed to write
would be scored against the *previous* image's logits. Across both runs, 0 of
10,200 scored jobs returned the sentinel.

### 2.2 Latency definition — device counter, JTAG excluded by construction

Latency is the delta of CSR 576/580 `CLOCKS_ACTIVE` across the queue→complete
transition. That counter is **gated on `jobs_active`** and clocked by `ddr_clk`
at exactly 100.000 MHz, so one tick is 10 ns and the delta counts only cycles
during which a job was actually in flight inside the IP. Host round-trip time is
therefore excluded structurally, not by subtraction.

Host wall-clock timings (`us_wr`, `us_q`, `us_poll`, `us_rd`) are recorded in
separate record fields and are **never** added to or substituted for the counter
delta. In particular `us_poll` bounds the inference from above and badly — one
JTAG poll round trip is of the same order as the whole inference.

### 2.3 Input format

Each image is pre-transformed on the host into the exact layout the compiled
arch expects: element index `(h*32 + w)*8 + c`, `c` in 0..7 with channels 3..7
zero-padded, each element an IEEE binary16 holding the integer 0..255;
16,384 B per image. Logical tensor is CHW. The converter
(`tools/cifar10_to_dla.py`) was validated against this compile's
`input_transform_mapping_TensorFlow_Lite_Frontend_IR_0.csv`.

**Cross-check performed for this run:** all 200 images the runner packed for the
ic01 subset were compared byte-for-byte against the independently produced
`cifar10_test_perf_fp16_cvec8.bin` dataset buffer. 200/200 identical. The
runner's packing path and the dataset-conversion path are therefore the same
function of the same source data.

### 2.4 Subset definitions

- **Tier 1 — official ic01 subset.** The 200 indices in
  `third_party/mlcommons_tiny/benchmark/training/image_classification/perf_samples_idxs.npy`,
  which is class-balanced at exactly 20 images per class (verified). This is the
  subset the MLPerf Tiny image-classification benchmark uses.
- **Tier 2 — full test set.** All 10,000 images of the CIFAR-10 `test_batch`
  (source batch md5 `40351d587109b95175f43aff81a1287e`), indices 0..9999 in
  order.

### 2.5 Per-cycle integrity gates

Every programming cycle, before any scored image, the runner:

1. reads back the entire 205,056-byte parameter image over JTAG and checks its
   FNV-1a against the value baked into the build (`0x191007b5`). Hard abort on
   mismatch — a stale or half-loaded parameter image is otherwise completely
   silent and every subsequent number would be a confident lie;
2. issues `IP_RESET` (CSR 552), re-initialises `CONFIG_BASE`, `CONFIG_RANGE`,
   `INTERMEDIATE_BASE` and the IRQ mask, and dumps them back;
3. runs **one golden warm-up inference** of the deterministic synthetic
   stimulus and asserts `argmax == 6`, the class predicted by the independent
   integer evaluator `tools/resnet8_golden.py`.

Gate 3 was previously a stub; it was implemented for this campaign (§6). All
four cycles across the two tiers passed all three gates, and the warm-up returned
bit-identical logits every time:
`[-17.188, -12.719, -1.891, -3.781, -25.438, -22.0, 1.375, -22.344, -3.609, -22.0]`.

Every job additionally reports CSR 540 diagnostics and CSR 512 IRQ status, which
are recorded per image.

## 3. Tier 1 — official 200-image ic01 subset

Log: `build/fpga_ai/bench/tier1_perf200/`. One programming cycle, 18.3 s wall.

### 3.1 Accuracy

**168 / 200 = 84.00 % top-1.** 0 discarded, 0 stale.

| class | correct / n | recall |
|---|---|---|
| airplane | 15/20 | 75.0 % |
| automobile | 19/20 | 95.0 % |
| bird | 11/20 | 55.0 % |
| cat | 15/20 | 75.0 % |
| deer | 18/20 | 90.0 % |
| dog | 14/20 | 70.0 % |
| frog | 20/20 | 100.0 % |
| horse | 18/20 | 90.0 % |
| ship | 19/20 | 95.0 % |
| truck | 19/20 | 95.0 % |

Confusion matrix, rows = true, columns = predicted:

| true \ pred | airpl | autom | bird | cat | deer | dog | frog | horse | ship | truck |
|---|---|---|---|---|---|---|---|---|---|---|
| airplane | **15** | 1 | 1 | 1 | 0 | 0 | 0 | 1 | 1 | 0 |
| automobile | 0 | **19** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| bird | 1 | 0 | **11** | 0 | 2 | 2 | 2 | 1 | 0 | 1 |
| cat | 1 | 0 | 0 | **15** | 0 | 2 | 2 | 0 | 0 | 0 |
| deer | 1 | 0 | 0 | 1 | **18** | 0 | 0 | 0 | 0 | 0 |
| dog | 0 | 0 | 1 | 3 | 1 | **14** | 1 | 0 | 0 | 0 |
| frog | 0 | 0 | 0 | 0 | 0 | 0 | **20** | 0 | 0 | 0 |
| horse | 0 | 0 | 1 | 0 | 0 | 1 | 0 | **18** | 0 | 0 |
| ship | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **19** | 0 |
| truck | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **19** |

The error structure is the ordinary CIFAR-10 one — bird/deer/dog/cat confusions
dominate, vehicles are near-perfect. There is no transpose-shaped or
channel-shaped failure mode (which would show as a collapsed or uniform
confusion matrix).

### 3.2 Latency

| statistic | ticks (10 ns) | ms |
|---|---|---|
| mean | 52,566.6 | 0.5257 |
| median | 52,546.5 | 0.5255 |
| p95 | 52,697 | 0.5270 |
| p99 | 52,702 | 0.5270 |
| min | 52,086 | 0.5209 |
| max | 52,704 | 0.5270 |

FPGA-only throughput at the mean: **1,902.4 inferences/s**.

**Caveat, and it is the interesting result of this campaign:** all 200 of these
jobs are the *first* 200 jobs of a fresh programming cycle, and the counter has
a reproducible settling ramp there. The steady-state figure in §4.3 is the one to
quote.

## 4. Tier 2 — full 10,000-image CIFAR-10 test set

Log: `build/fpga_ai/bench/tier2_full10k/`. 235.4 s wall, 2 programming cycles
(9,499 images then 501, split by the runner's 9,500-job-per-programming budget
policy).

### 4.1 Accuracy

**8,633 / 10,000 = 86.33 % top-1.** 0 discarded, 0 stale, 0 abandoned.
95 % binomial CI: 85.66 – 87.00 %.

| class | correct / 1000 | recall |
|---|---|---|
| airplane | 873 | 87.3 % |
| automobile | 947 | 94.7 % |
| bird | 826 | 82.6 % |
| cat | 713 | 71.3 % |
| deer | 851 | 85.1 % |
| dog | 735 | 73.5 % |
| frog | 943 | 94.3 % |
| horse | 899 | 89.9 % |
| ship | 921 | 92.1 % |
| truck | 925 | 92.5 % |

Confusion matrix, rows = true, columns = predicted:

| true \ pred | airpl | autom | bird | cat | deer | dog | frog | horse | ship | truck |
|---|---|---|---|---|---|---|---|---|---|---|
| airplane | **873** | 13 | 29 | 6 | 7 | 0 | 8 | 15 | 33 | 16 |
| automobile | 5 | **947** | 0 | 1 | 0 | 1 | 1 | 2 | 7 | 36 |
| bird | 40 | 3 | **826** | 20 | 21 | 11 | 51 | 16 | 2 | 10 |
| cat | 12 | 14 | 40 | **713** | 41 | 82 | 55 | 17 | 9 | 17 |
| deer | 5 | 3 | 37 | 24 | **851** | 4 | 48 | 24 | 1 | 3 |
| dog | 6 | 7 | 31 | 116 | 28 | **735** | 32 | 33 | 3 | 9 |
| frog | 5 | 4 | 18 | 10 | 13 | 2 | **943** | 2 | 2 | 1 |
| horse | 9 | 5 | 17 | 18 | 28 | 11 | 8 | **899** | 1 | 4 |
| ship | 38 | 11 | 4 | 3 | 0 | 0 | 3 | 1 | **921** | 19 |
| truck | 14 | 44 | 3 | 2 | 1 | 0 | 0 | 4 | 7 | **925** |

Per-class precision: 86.7 / 90.1 / 82.2 / 78.1 / 86.0 / 86.9 / 82.1 / 88.7 /
93.4 / 88.9 %.

The dominant error is cat↔dog (82 + 116 = 198 of the 1,367 errors, 14 %), which
is the canonical CIFAR-10 confusion pair.

### 4.2 Comparison to the quality target

| metric | value | target |
|---|---|---|
| full test set, top-1 | **86.33 %** | ≥ 85.0 % floor — **PASS** |
| reference accuracy | 86.5 % | −0.17 pp |
| ic01 200-image subset, top-1 | 84.00 % | (see below) |

The 200-image subset lands 2.33 pp below the full-set number, which is 2 images.
At n = 200 the sampling standard error is 2.6 pp, so 84.00 % and 86.33 % are the
same number to within noise (the subset's 95 % CI, 78.9–89.1 %, contains both the
full-set result and the 86.5 % reference). MLPerf Tiny measures *accuracy* over
the whole test set and uses the 200-image class-balanced subset for the
*performance* run; the full-set figure is therefore the one compared against the
85 % floor, and it passes.

Both runs are reported here because the small subset is what a performance-mode
submission would time, and it is honest to show that a 200-image accuracy
estimate can fall below the floor by chance.

### 4.3 Latency — steady state, and a reproducible settling ramp

Full-set distribution:

| statistic | ticks (10 ns) | ms |
|---|---|---|
| mean | 52,749.1 | 0.5275 |
| median | 52,757 | 0.5276 |
| p95 | 52,757 | 0.5276 |
| p99 | 52,757 | 0.5276 |
| min | 52,086 | 0.5209 |
| max | 52,757 | 0.5276 |

**9,748 of 10,000 jobs reported exactly 52,757 ticks.** The 252 that did not are
*only* the first 252 jobs of each of the two programming cycles — positions
0..251 and 9,499..9,750, and nowhere else in the run. Within that window the
reported delta climbs from 52,086 to 52,757 and then pins there for the rest of
the cycle and never dips again.

The ramp reproduces between the two cycles — **251 of the 252 values match
exactly** (the sole exception is job 8: 52,443 in cycle 0 vs 52,379 in cycle 1)
— despite the two cycles running completely different images (cycle 0 ran test
images 0..251, cycle 1 ran 9,499..9,750). Both produced the sequence
`52189, 52086, 52087, 52334, 52335, 52154, 52243, 52356, …` and then a clean
+1 tick per job up to 52,757 at job 252. Two conclusions follow directly:

1. **The inference is image-independent to the clock cycle.** 9,748 jobs
   spanning all ten classes returned the same count, and the settling ramp
   itself reproduces across two cycles that ran disjoint image sets. Different
   data, identical cycle counts. That is expected for a CoreDLA convolution schedule —
   the dataflow has no data-dependent control — and it is a useful negative
   result: there is nothing to gain from "easy" images and there is no
   data-dependent tail to worry about in a latency distribution.
2. The sub-52,757 readings are a **counter/sampling artefact of the first ~250
   jobs after a programming**, not real variation. The steady-state value is the
   true per-job active-cycle count.

**Steady-state single-stream latency: 52,757 ticks = 527.57 µs = 1,895.5
inferences/s.** In `dla_clk` terms that is 158,271 cycles at 300 MHz.

Efficiency against the 128-MAC/cycle peak:

| quantity | value |
|---|---|
| MACs per inference | 12,501,632 |
| achieved | 23.69 GMAC/s |
| peak (128 MAC/cyc × 300 MHz) | 38.4 GMAC/s |
| utilisation | **61.7 %** (79.0 of 128 MACs per `dla_clk` cycle) |

Of the 527.57 µs, at least ~64 µs (12 %) is the parameter DMA: the 205,056-byte
config+filter image is re-fetched over the 256-bit `ddr_axi` at 100 MHz on every
single job, which is 6,408 `ddr_clk` cycles minimum. That re-fetch, and the
100 MHz `ddr_clk` it runs at, remain the largest identified lever — see §8.

### 4.4 Run integrity

| check | result |
|---|---|
| images scored | 10,000 / 10,000 |
| CSR 540 diagnostics | `0x00000000` on all 10,000 jobs — eval-limit bit 2 never asserted |
| CSR 512 IRQ | `0x00000002` on all 10,000 — consistent completion IRQ, W1C'd per job |
| stale / poison reads | 0 |
| `eval_limit` events | 0 |
| timeout events | 0 |
| abandoned images | 0 |
| retries | 0 |
| parameter readback | FNV-1a `0x191007b5` = expected, both cycles |
| golden warm-up | argmax 6 = oracle, both cycles, identical logits |
| distinct outputs | **10,000 distinct logit vectors out of 10,000 images** |
| polls per job | 1 (133), 2 (9,574), 3 (293) |

The distinct-output check is the strong form of the brief's "spot-check 5 random
images": not five, but every image in the run has a logit vector unique across
the whole test set. Nothing is being replayed from a cache or a stuck buffer.

**Cross-run reproducibility.** The 200 ic01 images were run twice — once as
Tier 1, once as part of Tier 2 — on different programming cycles, in different
positions within their batches, with different neighbouring images. All 200
returned **bit-identical logit vectors** both times, and therefore identical
predictions (168/200 in both). The pipeline is deterministic end to end.

### 4.5 Wall clock and host-side throughput

Two throughput numbers, kept strictly apart:

| | Tier 1 (200) | Tier 2 (10,000) |
|---|---|---|
| **FPGA-only** (1 / device latency) | 1,902.4 inf/s | **1,895.8 inf/s** (steady state 1,895.5) |
| JTAG-inclusive, per-image sum | 79.3 img/s | 57.2 img/s |
| end-to-end incl. programming | 10.9 img/s | 42.5 img/s |
| wall clock | 18.3 s | 235.4 s |

Per-image host breakdown (host wall clock around JTAG traffic — *not* inference
latency):

| stage | Tier 1 mean | Tier 2 mean |
|---|---|---|
| input write (16 KiB) | 10.384 ms | 15.437 ms |
| queue write (CSR 536) | 0.340 ms | 0.314 ms |
| completion poll wait | 0.694 ms | 0.638 ms |
| output read (32 B) | 1.185 ms | 1.104 ms |
| **total** | **12.603 ms** | **17.494 ms** |

The 16 KiB input write is 82–88 % of the host cost. Of the 17.494 ms of
accounted per-image time in Tier 2, the device inference is 0.528 ms — **3.0 %**;
the other 97 % is JTAG. (Total wall clock per image is higher still, 23.5 ms,
the remainder being per-batch host bookkeeping, the worker re-reading the
10,000-line manifest once per batch, and the two programming cycles.)
The Tier 2 input write is 5 ms slower per image than
Tier 1 purely because Tier 2 streams 10,000 distinct 16 KiB files off disk while
Tier 1 re-read 200 that were already in the OS file cache — it is a host storage
effect, not a JTAG or device effect. Measured JTAG bandwidth in this campaign was
1,036–1,541 KiB/s write and ~1,245–1,265 KiB/s read, consistent with the
bring-up characterisation, including the documented cold-start penalty on the
first transfer after a System Console launch (the `calibrate` event reports
707–753 KiB/s, roughly half the steady-state rate — do not use it for
projections).

Programming cycles cost 7.11–7.19 s each plus ~7 s of System Console restart.

## 5. Eval-licence budget

The CoreDLA IP is unlicensed (CSR 608 reads 0, expected on evaluation builds) and
permits a documented 10,000 valid inferences per FPGA programming, with CSR 540
bit 2 signalling that the wall has been hit.

| run | cycle | warm-up | scored | jobs charged |
|---|---|---|---|---|
| Tier 1 | 0 | 1 | 200 | 201 |
| Tier 2 | 0 | 1 | 9,499 | 9,500 |
| Tier 2 | 1 | 1 | 501 | 502 |

The runner charges each batch to the budget *before* issuing it and refunds the
difference once the worker returns cleanly — over-counting only costs an early
reprogram, whereas under-counting would silently run past the licence wall.
Diagnostics bit 2 never asserted in 10,203 inferences.

**Reprogramming, not `IP_RESET`, was used to reset the budget.** The brief
permitted relying on `IP_RESET` if bring-up had shown the licence counter resets
with it. It did not: the bring-up experiment established only that `IP_RESET`
clears the completion counter, `CLOCKS_ACTIVE` and the IRQ mask, and it ran
nowhere near the limit, so it could not distinguish "the licence counter resets"
from "the licence counter was never close". A full reprogram remains the only
proven reset, and the 14 s it cost across this campaign is not worth the risk of
a run that silently stops producing valid inferences.

## 6. Changes made to the tooling for this campaign

All changes are in `tools/run_cifar10_jtag.py`. `tools/jtag_worker.tcl` was not
modified. No Quartus compile was used; the SOF is byte-identical to the one
bring-up validated.

1. **`--index-file`** — run an arbitrary set of CIFAR-10 test indices (`.npy` or
   text) rather than a contiguous range. This is what selects the official ic01
   200-image subset. Indices are run in ascending order so adjacent ones still
   share a `BATCH`.
2. **`--pack-dir`** — share one packed-input directory between runs instead of
   writing another 164 MiB copy per run directory.
3. **Batch-abort handling (latent bug, would have killed a long run).**
   `jtag_worker.tcl` reports a job-level fault by emitting the offending job's
   `RES … err=eval_limit|timeout` and *then* `ERR batch_aborted`.
   `SysconBackend.command` raises on any `ERR`, so the exception escaped
   `Backend.batch` and would have aborted the whole campaign on the first
   timeout or licence stop — exactly the events the Runner has reprogram-and-
   retry logic for, which could therefore never fire on real hardware. The
   `SimBackend` returned the correct `(records, "batch_aborted")` shape, so the
   fault path had only ever been exercised against a backend that could not
   reproduce the bug. `WorkerError` now carries the `RES` lines collected before
   the failure and `Backend.batch` converts a `batch_aborted` error into the
   documented return shape; genuine transport deaths still propagate. Verified
   both ways.
4. **Real golden warm-up** (§2.5 gate 3), replacing a stub that charged the
   budget for a warm-up it never ran. It now writes the synthetic stimulus,
   runs one job, and aborts the cycle if `argmax != 6`. It fired correctly on
   all four hardware cycles.

## 7. What this is not

- **This is not an official MLPerf Tiny submission.** No submission checker was
  run, no closed-division rules compliance was audited, the results were not
  reviewed by MLCommons, and the run does not use the EEMBC EnergyRunner
  harness or its serial-port protocol. The numbers here are measurements of this
  design taken with the benchmark's dataset and subset definition, reported in
  the benchmark's units.
- **The latency figure is the device figure, not a system figure.** 527.57 µs is
  the time the CoreDLA IP spends with a job in flight. An MLPerf Tiny
  single-stream latency would include the host/interface path, which here is a
  JTAG debug cable costing ~17 ms per image — a bring-up transport, not a
  deployment one. The two are reported separately throughout and are never
  combined.
- **There is no independent CPU reference number for this exact model on this
  exact dataset in this report.** No TFLite runtime is installable in this
  environment (`tensorflow`, `tflite_runtime`, `ai_edge_litert`, `onnxruntime`
  are all absent), and the repo's pure-Python integer evaluator
  `tools/resnet8_golden.py` is far too slow for 10,000 images. It was used for
  what it is good for: a single-image oracle for the warm-up gate. The 86.5 %
  reference accuracy is the published MLPerf Tiny ic01 figure, not something
  re-measured here.
- The IP is unlicensed evaluation IP; every number above was taken inside the
  10,000-inference-per-programming evaluation limit.

## 8. Observations worth carrying forward

1. **Latency is exactly reproducible and image-independent.** 52,757 `ddr_clk`
   ticks, every job, once settled. Any future change to the design can be
   regression-tested against a single integer.
2. **The parameter re-fetch is the biggest identified lever.** 205,056 bytes are
   re-read from on-chip pseudo-DDR on every inference at 100 MHz over a 256-bit
   bus — ~64 µs, ~12 % of the job, and it buys nothing because the parameters
   never change. Raising `ddr_clk` shrinks it proportionally (and would require
   `Config.latency_counters[0]["hz"]` to move with it — the tick is not
   self-describing).
3. **Compute utilisation is 61.7 % of the 128-MAC/cycle peak.** The gap is not
   explained by the parameter DMA alone (12 %); ~26 % of the job is still
   unaccounted for and is the natural next investigation.
4. **The host transport, not the accelerator, defines wall-clock throughput** —
   1,896 inf/s of device capability delivered through a 57 img/s cable. The
   16 KiB input write is 88 % of that cost.
5. **The first ~250 jobs after every programming report a settling counter.**
   Any short benchmark run (like Tier 1) sits entirely inside that window and
   will report a latency ~0.35 % low. Quote steady-state numbers, or run more
   than ~250 jobs.

## 9. Artifacts

### Bitstream and model

| artifact | size | hash |
|---|---|---|
| `fpga/axc3000_mlperf/output_files/axc3000_top.sof` | 2,272,194 B | sha256 `d16828ff8f66d783ad5af1c75d9e1343cffc1d65d10d3d9727be6d737495c588` |
| `fpga/ai_suite/resnet8_agx3_int8_k16c8.arch` | — | sha256 `768c2061c469ae500c0e2c0ab4c19cf8c294ab3b8958d28986fbb93ea018105e` |
| parameter image (config+filter, 205,056 B) | — | FNV-1a `0x191007b5` (verified by JTAG readback on all 4 cycles) |
| IP discovery ROM arch hash (CSR 0x00040004) | — | `0xa5de3e03` |

### Datasets

| artifact | size | sha256 |
|---|---|---|
| `third_party/.../perf_samples_idxs.npy` (ic01 subset definition) | 200 idx | `3bd4a88eeb4c50fad652d0f24c8af13bc9219ba2878aea47c6536bfbeb43024d` |
| `build/fpga_ai/cifar10/dla_k16c8_perf200_fp16/cifar10_test_perf_fp16_cvec8.bin` | 3,276,800 B | `ef1ec3d14caa72b0c089c4dd455815b60bb06df1e2213c74190f3196c456a9f8` |
| `build/fpga_ai/cifar10/dla_k16c8_full10k_fp16/cifar10_test_all_fp16_cvec8.bin` | 163,840,000 B | `d229e8ae0a62c4249cfb18d975c25a7973691e48e0c48cc7abd60ca46aece002` |
| `build/fpga_ai/cifar10/dla_k16c8_synthetic/synthetic_fp16_cvec8.bin` (warm-up) | 16,384 B | `f63673df2cebfaa62874fd4908454c78e7c7557581ede73f203e424ec9893aae` |
| CIFAR-10 `test_batch` source | — | md5 `40351d587109b95175f43aff81a1287e` |

### Run logs

Tier 1 — `build/fpga_ai/bench/tier1_perf200/`:

| file | size | sha256 |
|---|---|---|
| `results.jsonl` | 79,701 B | `87108f8a116988a8e2b13e3c5e4589ddf3fb653f224adc4ca6f2ccc0109e2562` |
| `events.jsonl` | 977 B | `5f1838e27266bbda82de04205fa4b94102271ed306e02bb75c13c75efaf90112` |
| `report.txt` | 2,683 B | `8e13fca6a644826c24f8a70b7e5c10c7c111c9b8e0d890598828b39f2f24d3cc` |

Tier 2 — `build/fpga_ai/bench/tier2_full10k/`:

| file | size | sha256 |
|---|---|---|
| `results.jsonl` | 4,020,156 B | `e4fc118078d7f7bb4633379820877e229200f32a70db43d141e0b5eb6d115e79` |
| `events.jsonl` | 1,862 B | `c53affb1d053008a257870b663a7e5279b7f1e4a48b67eaaeb4c37da73a6d06b` |
| `report.txt` | 2,696 B | `ab4ed140db688f5d02352cfb24b2fe77cd572b06e53040d6fa1df3f0c5072dee` |

Console transcripts: `build/fpga_ai/bench/tier1_perf200_console.log`,
`build/fpga_ai/bench/tier2_full10k_console.log`.
Shared packed inputs: `build/fpga_ai/bench/pack/` (10,000 × 16,384 B +
`manifest.txt`).
Per-image records in `results.jsonl` carry the image index, true label,
prediction, correctness, all 16 decoded FP16 output halves, the
`clocks_active` before/after/delta, the four host-side timings, poll count,
CSR 512 IRQ and CSR 540 diagnostics, and the programming cycle.
