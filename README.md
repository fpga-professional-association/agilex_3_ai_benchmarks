# CoreDLA on the Arrow AXC3000 — measured inference

Measured performance of the Altera FPGA AI Suite **CoreDLA** inference IP on the ~$129 Arrow
**AXC3000** board — an **Agilex 3** `A3CY100BM16AE7S` (no HPS, **no DDR**), fed from a 16 MB HyperRAM.

This page reports **only measured silicon results** for one workload: `resnet8-cifar10` INT8.

## Result — `resnet8-cifar10` INT8 (MLPerf Tiny image classification)

| Metric | Measured |
|---|---|
| **Top-1 accuracy** | **86.0 %** on 100 CIFAR-10 test images — vs 86.64 % CPU-INT8 software reference |
| Top-5 accuracy | 100 % |
| **CoreDLA IP throughput** | **409.3 fps** — the DLA engine's own on-chip-clock-measured rate (`clk_dla` = 200.0 MHz) |
| Compute efficiency | 2.05 fps/MHz |

All 12 inference stages complete on silicon with no timeout, and the on-board top-1 matches the
software reference — i.e. real, correct, input-dependent compute. Full record:
[`results/ph3_resnet8-cifar10-hyperram-onboard_20260711.json`](results/ph3_resnet8-cifar10-hyperram-onboard_20260711.json)
(bitstream SHA-256 `e0e363f2…`).

> **409 fps is the DLA *engine* rate** (measured from the DLA's on-chip clock-cycle count). End-to-end
> *system* throughput is presently bounded by the JTAG input path (~12 fps) — a bring-up limitation of
> the data path, not the engine (see [limitations](#hyperram-related-board-limitations)).

## System configuration

| | |
|---|---|
| Device | Altera **Agilex 3** `A3CY100BM16AE7S` (Arrow AXC3000, no HPS) |
| **CoreDLA compute clock** (`clk_dla`, fabric) | **200 MHz** (measured 200.011 MHz) — retuned down from the vendor 600 MHz default, which does not close timing on this fit (~3× engine-clock haircut vs the vendor devkit) |
| Memory / control clock | **175 MHz** (HyperRAM CK, CoreDLA `clk_ddr`, CSR, interconnect); HyperRAM DDIO 2× = 350 MHz |
| External memory | Winbond **W957D8NB HyperRAM** — 16 MB, **x8 HyperBus**, 175 MHz CK (DDR via DDIO) |
| CoreDLA architecture | `AGX3_Performance.arch`, INT8 (NNCF PTQ) |

## Toolchain

| Tool | Version |
|---|---|
| Quartus Prime Pro | 26.1.0 Build 110 (Agilex 3) |
| FPGA AI Suite | 2026.1.1 |
| OpenVINO runtime | 2025.4.0 |
| Container | `alterafpga/fpgaaisuite:2026.1.1-quartus` |

## FPGA resource usage — CoreDLA + HyperRAM example design (`A3CY100BM16AE7S`)

| Resource | Used | Available | % |
|---|---:|---:|---:|
| Logic (ALMs) | 32,889 | 34,000 | **97 %** |
| Logic registers | 78,042 | — | — |
| M20K blocks | 228 | 262 | **87 %** |
| Block memory bits | 4,133,008 | 5,365,760 | 77 % |
| DSP blocks | 75 | 276 | 27 % |
| PLLs | 2 | 11 | 18 % |

The design is **logic- and M20K-bound** (97 % ALM / 87 % M20K) — CoreDLA's on-chip weight/activation
buffering dominates the A3CY100; the tensor-MAC DSP array sits at only 27 %.

## HyperRAM-related board limitations

CoreDLA is architected for a wide, high-bandwidth DDR global memory. The AXC3000 has none — that one
gap drives every limitation here:

- **No DDR.** Only a 16 MB HyperRAM on an **8-bit** HyperBus (the vendor Agilex-3 devkit ships LPDDR4).
  Peak HyperRAM bandwidth is ~350 MB/s (175 MHz CK × DDR × x8) versus DDR-class GB/s, so the design is
  **memory-bandwidth-constrained** — resnet8 re-streams its weights from HyperRAM every inference.
- **16 MB capacity** caps the resident config + weights + activation working set.
- **`clk_dla` = 200 MHz, not 600.** The vendor default engine clock fails STA by a wide margin on this
  fit; 200 MHz is what closes.
- **DDIO write defect (worked around).** The HyperRAM DDIO controller corrupts any 32-byte beat written
  more than once (the device "write-wound" law). Fixed on our side by a **write-combiner** in the
  AXI→HyperBus bridge ([`rtl/hyperbus/axi4_hbmc_bridge.sv`](rtl/hyperbus/axi4_hbmc_bridge.sv)) that
  gathers the host's per-word partial writes into one full-strobe beat write — making the contiguous
  config/weight load bit-exact. The memory-IP track root-caused it to a missing CK eye-centring pin
  delay (`set_instance_assignment -name D5_DELAY 15 -to hb_ck`), tracked for a margin fix.
- **Per-fit launch calibration.** The DQ/CK pad-launch timing is trim-calibrated per fit and *not*
  SDC-constrained, so a fit can pass STA yet be silicon-marginal — every rebuild must be
  silicon-validated (shape suite: `wstrb_abc.tcl` + `wound_retest.tcl`).
- **JTAG data path (bring-up).** Config/weights/input are delivered over JTAG (~1–2 MB/s, control-plane
  rate), which bounds end-to-end system throughput to ~12 fps. The DLA engine rate (409 fps) is
  unaffected; a HyperRAM-resident input feed is the next step.
- **JTAG programming quirk.** This cable must configure at **6 MHz** JtagClock — 15 MHz silently fails
  to synchronize.

Remaining follow-ups (D5_DELAY margin fix, full-set accuracy, on-chip `hw_timer` timing, the other
MLPerf Tiny models) are tracked in **issue #71**.

## Reference

- **MLPerf Tiny v1.4** results (MLCommons): <https://mlcommons.org/benchmarks/inference-tiny/>
- resnet8-cifar10 reference model: <https://github.com/mlcommons/tiny> (`pretrainedResnet.tflite`)

---

Multiple agents share one board — any on-board work must hold the
[`scripts/devkit_lock.sh`](scripts/devkit_lock.sh) devkit lock.
