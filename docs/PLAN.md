# AXC3000 AI Capacity & Benchmark Plan (canonical numbers)

This is the machine-readable distillation of `docs/axc3000_ai_capacity.html` (DOC AXC3000-AICAP REV B,
2026-07-03). The HTML is the source document; **this file is the ground truth implementers should cite**.
If the two disagree, fix this file and say so in your PR.

Central question every task answers: does the workload hit the **compute wall**
(2,760 INT8 MACs/cycle in tensor mode) or the **memory wall** (an 8-bit HyperBus at a few hundred MB/s)?
The dividing line is **~350–400 KB of INT8 weights** — where a model stops fitting in on-chip SRAM.

## §1 Silicon and board budget

| Resource | Quantity | Notes |
|---|---|---|
| Board | Arrow AXC3000 ($129) | |
| Device | A3CY100BM16AE7S | "Y" = **no HPS** → Nios V soft-core only |
| Logic | 100K LE | |
| DSP blocks | 138 | Enhanced DSP with AI tensor block (Agilex 5-derived) |
| INT8 MACs / DSP / cycle | 20 | Tensor mode, two-column structure, INT32/FP32 accumulate |
| 18×19 multipliers | 276 | Classic DSP mode (2 per block) |
| On-chip M20K SRAM | 4.47 Mb ≈ **559 KB** | Weights + activations + FIFOs all compete here |
| HyperRAM | 128 Mb = **16 MB** | Winbond W957D8NB, ×8 HyperBus DDR, **1.2 V** (corrected from an earlier "1.8 V" — see note below) |
| QSPI flash | 256 Mb = **32 MB** | Bitstream + dataset cold storage |
| Fabric fmax | up to 345 MHz (family headline) | Assume less for a packed design; verify per speed grade in Quartus |
| Host link | USB-C (FTDI JTAG) | **Control plane only, never the data plane** |
| Expansion | CRUVI HS, MKR pads | LVDS to 1.25 Gbps, MIPI D-PHY — future live input |

No HPS means no Linux/OpenVINO runtime on-chip. Control paths: (a) JTAG System Console from a
workstation, (b) Nios V/g bare-metal or RTOS. Both are supported by FPGA AI Suite example designs.

> **Correction flagged loudly per AGENTS.md:** the HyperRAM row above previously read "1.8 V". Issue
> #7's `docs/board_bringup.md` (§"HyperRAM voltage discrepancy against PLAN §1") found Arrow's
> versioned/dated AXC3000 User Guide v1.2.1 (§2.3.3.2, §3.6, bank-voltage table) states the
> AXC3000's HyperRAM bank is **1.2 V**, and `quartus/constraints/axc3000_board.tcl` was already
> written against 1.2 V for every HyperRAM pin. This doc is corrected to match that
> already-cited-in-repo, versioned source; the underlying discrepancy report and its provenance are
> preserved in `docs/board_bringup.md` for anyone who wants to re-derive it from the schematic.

## §2 Compute ceiling

Marketing peak: `138 DSP × 20 INT8 MACs × 2 ops × 345 MHz = 1,904 GOPS ≈ 1.90 TOPS`
(= 952 GMAC/s). Derate in three stages:

1. **Fabric clock** — packed design won't close 345 MHz first pass. Plan **250–300 MHz**.
2. **MAC allocation** — inference IP won't get all 138 DSPs (DMA, address gen take a cut). Plan **70–85%**.
3. **Scheduler efficiency** — useful fraction of allocated MAC-cycles. Sequenced overlays land at
   **30–60%** on CNNs; small models sit at the low end (fixed per-inference overhead dominates).

**Default planning point used throughout:** 300 MHz × 75% alloc × 45% sched
= **~279 GMAC/s effective (~0.56 TOPS)**.

## §3 Performance levers (Agilex 3-specific)

| Lever | Discipline | Proof |
|---|---|---|
| LV1 Retiming-clean RTL | Reset-less datapath pipeline registers (2nd-gen Hyperflex); sync reset only where architecture needs state; no clock gating; duplicate high-fanout control; treat Fast Forward report as a work queue | L1 |
| LV2 Tensor-mode capture | Naive `a*b+c` lands in classic 18×19 mode = silent 10× loss. Use DSP UG templates, cascade accumulation through the DSP column, audit fitter report for tensor-mode block count **every build** | L0 |
| LV3 M20K banking geometry | ~330 GB/s aggregate exists only if banked one-port-per-PE-column, output registers on, widths matched to lanes | L2 |
| LV4 Clock-domain split | Hot domain: PE array + its M20Ks. Cool domain (100–150 MHz): Nios V, CSRs. HyperBus in its own. Async FIFOs at seams | L1 |
| LV5 Soft-logic MAC overflow | Past 138 DSPs, sub-8-bit MACs go into ALMs via fractal synthesis (confirm Agilex 3 support in current synthesis handbook) | L0b |
| LV6 Trained HyperBus capture | Constraints referenced to RWDS + init-time training pass centering the capture window per board/temperature | L3 |
| LV7 Quantization aggression | Each bit removed multiplies effective compute only where the datapath exploits it; accuracy priced by the §6 harness → accuracy-vs-bits Pareto per model | L5 |

> **Toolchain caveat on LV2 (added by #9, measured 2026-07-05):** on **Quartus Prime Pro 26.1.0**,
> the Agilex 3 DSP's tensor mode (DSP Prime block) is **not reachable from hand-written RTL or the
> IP Catalog** — the tool restricts DSP Prime to Stratix 10 NX and Agilex 5 (message ID 24863).
> This is a *tooling* gap, not a silicon limit (the fabric is genuinely Agilex-5-derived per §1).
> Consequence: the 20 INT8 MACs/DSP/cycle density in §2 is reachable **only** through FPGA AI
> Suite's `dla_compiler` (its own internal netlist path), **not** through the custom RTL that LV2,
> §7 L0/L0b/L1, and the §11 hand-rolled-systolic stretch assume. Custom-RTL MACs land in classic
> 18×19 mode (≈half the density). Re-check on future Quartus/AI-Suite releases. See #9 README.

## §4 Memory walls

| Tier | Capacity | Peak BW | Sustained (plan) | Role |
|---|---|---|---|---|
| M20K on-chip | 559 KB | ~330 GB/s aggregate @300 MHz | banked (L2 verifies) | Weights (small models), activations, ping-pong buffers |
| HyperRAM ×8 | 16 MB | 333 MB/s @166 MHz DDR; 400 MB/s @200 MHz | **~250 MB/s** (166 MHz × 75% eff) | Test-record store; weight store for models >~400 KB |
| QSPI flash | 32 MB | ~50–66 MB/s (×4, 100–133 MHz) | ~40–55 MB/s | Bitstream + full-dataset cold storage |

- HyperBus is 8 bits DDR: `bytes/s = 2 × f_HB`. Sustained efficiency on long linear bursts: 70–85%.
- **Verify the W957D8NB speed grade on your board** (166 vs 200 MHz = 20% swing on every memory number).
- The HyperBus clock you close in Agilex 3 GPIO is a timing-closure result, not a datasheet right;
  conservative first pass **100 MHz → 200 MB/s peak**.
- **Controller gap:** Quartus ships no HyperBus controller; stock AXC3000 Nios V example runs from
  internal RAM only. Options in order: (1) Arrow Agilex-3 GitHub reference designs, (2) port an
  open-source HyperBus master (bus is simple: CA phase, latency count, RWDS strobe), (3) licensed IP.
  Budget the port + timing closure as its own task.
- Escape hatch (future custom board): Agilex 3 EMIF supports LPDDR4-2133 ≈ 10–15× memory-wall lift.

## §5 What fits — capacity table

At the default planning point (279 GMAC/s, 249 MB/s HyperRAM sustained). Design line
`ONCHIP_W_LIMIT = 400 KB` INT8 weights.

| Model | Weights INT8 | MACs/inf | Input bytes | Weights home | Bound | Net FPS (plan) |
|---|---|---|---|---|---|---|
| AD autoencoder (MLPerf Tiny) | 267 KB | 264 K | 640 | M20K | MEMORY (input feed) | ~354 k/s |
| DS-CNN keyword spotting | 24.9 KB | 2.7 M | 490 | M20K | COMPUTE | ~103 k/s |
| ResNet-8 CIFAR-10 | 78.7 KB | 12.5 M | 3,072 | M20K | COMPUTE | ~22 k/s |
| MobileNetV1-0.25 VWW 96² | 325 KB | 7.5 M | 27,648 | M20K | MEMORY (input feed) | ~9.0 k/s |
| MobileNetV2-1.0 224² | 3.54 MB | 300 M | 150,528 | HyperRAM | MEMORY | ~40 /s |
| ResNet-18 224² | 11.69 MB | 1,814 M | 150,528 | HyperRAM | MEMORY | ~17 /s |
| Tiny-YOLOv3 416² | 8.86 MB | 2,780 M | 519,168 | HyperRAM | MEMORY | ~21 /s |

Traffic model for HyperRAM-resident weights: `bytes/frame ≈ weights + input + ~2.5 MB activation spill`
(rough constant; refine with the AI Suite performance estimator, which accepts memory bandwidth as input).
These FPS numbers are **roofline estimates** — the whole point of the project is to replace them with
measurements. Below the 400 KB line, FPGA AI Suite's **DDR-free mode** applies and HyperRAM is free to
hold the test-record set.

## §6 HyperRAM record-replay benchmark

Store the quantized test set in HyperRAM with golden labels inline; burst DMA replays records through a
ping-pong M20K buffer into the engine; a hardware scoreboard accumulates cycle and hit counts.
**FPS and accuracy fall out of two register reads — no host in the timed loop.**

Pipeline: `QSPI/JTAG loader → HyperRAM (records+labels) → HBMC+DMA (linear bursts) → M20K ping-pong
(2× record buf) → INT8 engine → scoreboard (argmax·cmp·count) → System Console JTAG readback`.

- Record format: see `docs/record_format.md` (INT8 tensor, engine-native layout + 1-byte golden label,
  padded to 64 B stride). Pack offline with the **exact** input quantization scale/zero-point from the
  compiled model so hardware sees bit-identical tensors to the software reference.
- Scoreboard register map: see `docs/register_map.md`.
- `FPS = DONE_COUNT × f_clk / CYCLES_64`; `accuracy = PASS_COUNT / DONE_COUNT`.
- Record-store capacity (16 MB − 64 KB result log, minus resident weights when >400 KB):
  AD 23,738 · KWS 32,640 (full test ×6) · ResNet-8 5,328 (53% of 10 k) · VWW 603 ·
  MobileNetV2 87 · ResNet-18 33 · Tiny-YOLOv3 15.
- Loop the resident set for sustained-rate runs; report cold-pass and looped numbers separately.
  For CIFAR-10 full-set accuracy, stage the remaining half from QSPI between passes.
- Throughput and latency decouple once the engine overlaps requests: quote FPS from CYCLES_64 and
  latency from LAT_MIN/MAX + histogram. Never one as a proxy for the other.

## §7 Characterization ladder

Run bottom-up. Every level produces one number that overwrites an assumption in §5.

| Lvl | Measures | Method | Replaces | Target |
|---|---|---|---|---|
| L0 | Achieved MACs/DSP/cycle; tensor-chain fmax | 8–32 DSP dot-product chain, cycle-counted; fitter-mode audit | allocation + scheduler floor | 20/20; tensor count = DSP count |
| L0b | Soft-MAC density (MACs/kALM) + fmax at INT4/2/1 | fractal-synth multiplier array | ceiling past 138 DSPs | characterized curve |
| L1 | fmax vs PE-array size; reset style; domain split | systolic tile sweep 8→138 DSPs; retiming-clean vs reset-heavy; merged vs isolated | fabric-clock assumption | locate the cliff; quantify LV1/LV4 deltas |
| L2 | Aggregate M20K GB/s vs banking | parallel wide readers into checksum sinks | on-chip BW claim | ≥0.8× theoretical |
| L3 | HyperBus shmoo + sustained MB/s | trained capture; LFSR/address-in-data memtest; linear-burst sweep | both memory assumptions | operating point with plotted margin |
| L4 | Overlay fixed cost per inference (µs) | graph-size sweep on AI Suite IP via method A; repeat under Spatial Compiler | tiny-model sub-roofline gap | µs constant per configuration |
| L5 | Model corpus: FPS, p50/p99, accuracy, µJ/inf | §6 harness, methods A and B, per quantization point | the entire §5 table | the publishable dataset |

> **L0 target caveat (added by #9, measured 2026-07-05):** the L0 "20/20; tensor count = DSP count"
> target is **unachievable via the custom-RTL microbench** on Quartus Pro 26.1 — Agilex 3 tensor
> mode is not exposed to hand-written RTL (see §3 LV2 caveat, msg 24863). L0/L0b/L1 therefore
> characterize the **classic-mode** ceiling (~10 MACs/block, one of the tensor block's two columns)
> and the fmax/retiming behaviour of that datapath, which is still a real, publishable number and
> the honest "what can custom RTL do on this silicon" answer. The tensor-mode density itself is a
> §7 L4/L5 measurement of the FPGA AI Suite IP, not of hand RTL.

Why this niche is ownable: Agilex 3 shipped in 2025; third-party performance literature is effectively
empty. Everything here is vendor-public silicon on a $129 board — every level is publishable.

## §8 Feed methods

| Method | Dataset size | Feed BW | Isolates |
|---|---|---|---|
| A · On-chip loop (16–128 records in spare M20K, looped) | tiny | fabric-rate | Pure engine ceiling |
| B · HyperRAM replay (§6, primary) | ≤16 MB resident | ~250 MB/s | Engine + realistic feed; **B−A = feed cost** |
| C · QSPI-resident, staged into HyperRAM | ≤32 MB | ~40–55 MB/s | Full-set accuracy passes only |
| D · Live CRUVI LVDS/MIPI | unbounded | ≫ HyperRAM | Future demo, out of scope v1 |
| E · JTAG streaming | — | ~1–2 MB/s | **Control/readback only. Never the data plane.** |

Report every result as a pair: method-A rate (engine ceiling) and method-B rate (fed from HyperRAM).

## §9 Tool-flow phases

- **PH0** Desk-check: Quartus Prime Pro ≥25.3 (free Agilex 3 license) + FPGA AI Suite from same
  installer. Run the **performance estimator** per model × architecture file; since 25.1 it accepts
  **external memory bandwidth** as an input — feed it ~250 MB/s.
- **PH1** Stock hostless example: AI Suite 25.3 ships Agilex 3 production support incl. a **hostless
  JTAG design for the Agilex 3 C-series dev kit** — build with `dla_build_example_design.py`, drive from
  the streaming System Console Python interface. Also the hostless **DDR-free** example ([HL-NO-DDR]):
  whole model in M20K, weights as MIF files — the "below the line" configuration verbatim.
  **License gate: unlicensed IP generation is hard-limited to 10,000 inference requests.** Fine for
  ≤10 K-record accuracy passes; for soak runs, generate licensed IP or reload bitstream per run.
- **PH2** Model prep: TF/PyTorch → ONNX → OpenVINO IR (AI Suite 25.x tracks OpenVINO 2024.6) → NNCF
  post-training INT8 with calibration slice → `dla_compiler` against the Agilex 3 architecture file.
  Record the compiled report's fold/streaming decisions (they set the real activation-spill term).
  Keep an OpenVINO CPU-INT8 run of the same IR as the accuracy reference.
- **PH3** HyperRAM integration: port controller, close I/O timing, verify with LFSR/address-in-data
  memtest + sustained-burst measurement **before** attaching inference. Platform Designer system:
  JTAG-Avalon master (or Nios V/g) + mSGDMA + HBMC + inference IP. Two configs:
  (a) DDR-free IP, HyperRAM = record store only; (b) HyperRAM = IP global memory for >400 KB models.
- **PH4** Benchmark harness: Python record packer, RTL scoreboard, host script pulling two registers →
  FPS/accuracy/p50/p99 per model.
- **PH5** Cross-checks: hardware accuracy must match OpenVINO CPU-INT8 on identical records (mismatch =
  layout/scale bug, not "FPGA error"). Power: inline USB-C meter + Quartus Power & Thermal Analyzer.
- **PH6** Stretch: AI Suite **Spatial Compiler** (beta) for MLP-class dataflow; beyond that, custom
  dataflow RTL (hls4ml Quartus/oneAPI path, or hand-rolled tensor-mode systolic).

## §10 Measurement checklist (what a defensible result includes)

- Batch = 1, end-to-end record-fetch → scoreboard-retire; p50/p99 and FPS, cold and looped.
- Accuracy on full test set post-quantization vs fp32 and CPU-INT8 baselines of the same graph.
- Achieved HyperRAM MB/s from memtest quoted next to every memory-bound number.
- f_clk, resource utilization (ALM/DSP/M20K), architecture file logged per result —
  **numbers without configs are noise**.
- Board watts via USB inline meter → µJ/inference; fabric estimate from Power & Thermal Analyzer.
- Fitter-report tensor-mode audit as a merge gate — DSPs left in classic mode are a silent 10× loss.
- Throughput and per-inference latency reported as separate numbers.

### Risk register

| Risk | Mitigation |
|---|---|
| HyperBus timing closure at 166+ MHz in GPIO | Start at 100 MHz, measure, then push |
| Refresh-collision jitter inflating p99 | Latch per-inference min/max; expect bimodal histogram |
| Depthwise layers mapping poorly to tensor mode | Scheduler-efficiency assumption at low end for MobileNets |
| 10 K-inference cap on unlicensed IP truncating soak runs | Check DONE_COUNT against intent |
| Quantization drift between packer and IR | PH5 parity gate is mandatory |

## §11 References

- Arrow AXC3000 product page + `github.com/ArrowElectronics/Agilex-3` (board spec, reference designs)
- AXC3000 teardown incl. no-HPS "Y" variant and Winbond HyperRAM: `jsykora.info/2025/11/axc3000-agilex-fpga-starter-kit`
- Altera Agilex 3 overview: `altera.com/products/fpga/agilex/3`
- Agilex 5/3 Enhanced DSP with AI Tensor Block: architecture brief 776602
- Quartus Prime Pro 25.1 + 25.3 release notes (Agilex 3 support, estimator memory-BW input, hostless JTAG, DDR-free mode, System Console Python)
- FPGA AI Suite Design Examples User Guide 848957 ([HL-JTAG], [HL-NO-DDR], `dla_build_example_design.py`, `dla_benchmark`)
- `altera-fpga/agilex-ed-ai-suite` GitHub (10,000-inference unlicensed limit)
- MLPerf Tiny: `mlcommons.org/tiny` + arXiv:2206.11791
- Agilex Hyperflex architecture handbook · Agilex 3 DSP user guide · Agilex 3 device datasheet · Winbond W957D8NB datasheet
