# Agilex 3 AI Benchmarks — AXC3000

Independent AI-inference characterization of the Altera **Agilex 3** FPGA (A3CY100BM16AE7S, no HPS)
on the $129 Arrow **AXC3000** board: what fits in 559 KB of M20K and 138 tensor-mode DSPs, and how
fast it classifies when fed from a 16 MB HyperRAM over an 8-bit HyperBus.

Agilex 3 silicon shipped in 2025 and the third-party performance literature is effectively empty —
no tensor-capture audits, no fmax-vs-array-size curves, no HyperBus shmoos, no MLPerf-Tiny-class
numbers. This repo produces that dataset on vendor-public silicon.

## 🎯 MLPerf Tiny performance benchmark — status

The north-star deliverable: **run the four MLPerf Tiny v1.4 workloads on the $129 AXC3000 and report
latency / throughput / accuracy / energy** per the benchmark's rules ([`docs/mlperf_tiny_v14_plan.md`](docs/mlperf_tiny_v14_plan.md)).

| Stage | Where it stands |
|---|---|
| **Models compile to the FPGA** | ✅ **all four core MLPerf Tiny models place 100% on the Agilex-3 CoreDLA IP** — `dla_compiler`-verified, via equivalence-preserving graph surgery (MLPerf **Closed-Division-legal**) |
| **Memory-subsystem benchmarks** | ✅ measured on the physical board (HyperRAM + on-chip M20K bandwidth) |
| **CoreDLA on the AXC3000** | ✅ **proof-of-life on silicon** — a CoreDLA+HyperRAM bitstream programs; the CoreDLA CSR responds (ID `0x81C43991`) and the HyperRAM AXI memory path works (guard-banded writes bit-exact; the device write-wound law confirmed + mitigated). First proof CoreDLA runs on this board. |
| **On-hardware inference numbers** | ✅ **FIRST measured inference on silicon** — `resnet8-cifar10` INT8 runs end-to-end on CoreDLA+HyperRAM: **86% top-1** (100 CIFAR images) vs 86.64% software reference → real, correct, input-dependent compute; DLA IP compute rate **409 fps @ 200 MHz**. Unblocked by the [HyperRAM write-combiner](rtl/hyperbus/axi4_hbmc_bridge.sv) (contiguous config/weight load bit-exact) + `JtagClock=6MHz` to configure. |

### Models → CoreDLA (compile-verified, accuracy-preserved)

| Task | Model | Compiles 100% FPGA | Throughput (est.) | INT8 accuracy | MLPerf target |
|---|---|:---:|---:|---:|---:|
| Anomaly Detection | `ad-toycar` | ✅ | 522 fps (memory-bound) | AUC 0.78 | — |
| Keyword Spotting | `ds-cnn-kws` | ✅ | 1,578 fps | 90.75% | 90% |
| Image Classification | `resnet8-cifar10` | ✅ | 4,557 fps | 86.64% | 85% |
| Visual Wake Words | `mobilenetv1-025-vww` | ✅ | 2,191 fps | 85.84% | 80% |

fps are `dla_compiler` performance **estimates** on `AGX3_Performance.arch`; the graph rewrites that
unblocked ds-cnn / resnet8 / vww are proven bit-equivalent to the reference
([`sw/model_prep/graph_ops/`](sw/model_prep/graph_ops/), 42 tests). Plan to get all four *running* on
the board: [`docs/tinyml_all_models_plan.md`](docs/tinyml_all_models_plan.md).

### Measured on the physical AXC3000 (the memory subsystem CoreDLA feeds from)

| Benchmark | Result | Status |
|---|---|---|
| **CoreDLA inference — `resnet8-cifar10` INT8** | **86% top-1** on 100 CIFAR images (vs 86.64% software ref); DLA IP throughput **409 fps @ 200 MHz** (on-chip-clock-measured, not the JTAG loop) — [`results/`](results/ph3_resnet8-cifar10-hyperram-onboard_20260711.json) | **measured** |
| HyperRAM sustained bandwidth | **342 MB/s** write / 337 read (175 MHz SDR PHY) | **measured** |
| L2 aggregate M20K bandwidth | **38.4 GB/s** banked (100% of theoretical); **32×** penalty for wrong banking geometry | **measured** |

> Multiple agents share one board — any on-board work must hold the [`scripts/devkit_lock.sh`](scripts/devkit_lock.sh) devkit lock.

## Results

**Status legend** — how much each row is worth, so no estimate is mistaken for a measurement:
`measured` = observed on the real AXC3000 · `synthesized` = Quartus place-and-route on
`A3CY100BM16AE7S`, no hardware · `sim-verified` = self-checking Verilator testbench ·
`estimate` = FPGA AI Suite performance estimator, no hardware · `blocked` = not attainable yet,
reason stated.

### Board bring-up

| Result | Detail | Status |
|---|---|---|
| LED blink on real hardware | `quartus/axc3000_blink` — real board pins (25 MHz `CLK_25M_C`@A7, LEDs @ AG21/AH22/AK21/AK20), flashed over JTAG, LEDs confirmed blinking | **measured** |
| JTAG data path | on-board USB-Blaster III (`09fb:6022`) → `usbipd` → Docker `quartus_pgm` → "Configuration succeeded" | **measured** |

### AI model through the FPGA AI Suite

| Result | Detail | Status |
|---|---|---|
| Model compile coverage | 3 of 7 compile to Agilex-3 IP: **ad-toycar, MobileNetV2, ResNet-18**. `resnet8-cifar10` / `ds-cnn-kws` / `mobilenetv1-vww` / `tiny-yolov3` do **not** (op-placement limits, not resource limits — see [`results/reports/ph0_estimator.md`](results/reports/ph0_estimator.md)). Per-model fixes tracked in issues #48–#51 (Closed-equivalent re-export vs Open-Division re-arch; tiny-yolov3 is impossible-as-is). MLPerf Tiny **v1.4** adoption plan: [`docs/mlperf_tiny_v14_plan.md`](docs/mlperf_tiny_v14_plan.md) | **measured** (tool fact) |
| ad-toycar INT8 → CoreDLA IP | compiles 100 % to FPGA, 350 MHz IP target, `out.aot` produced | **measured** compile |
| ad-toycar throughput | 521.6 fps @ 250 MB/s assumed DDR BW; **memory-bound** (needs 519.8 MB/s; weights re-streamed ~2×/inference) — `results/ph0_ad-toycar-*.json` | **estimate** |

### Memory-subsystem bandwidth — measured on the AXC3000

The board is in hand and the first on-silicon benchmarks landed. The full loop (`usbipd` attach → JTAG
→ program → System-Console readback) is proven; JTAG is control-plane only (counters time the on-chip
datapath, not the link — PLAN §8 method E).

| Result | Detail | Status |
|---|---|---|
| HyperRAM sustained bandwidth | **342.4 MB/s write / 337.3 MB/s read** peak (175 MHz CK, SDR PHY), burst sweep 16→768 words, every point integrity-clean (`ERR_COUNT=0`) — `results/ph3_hyperbus_bw_len*.json`, [`results/reports/hyperbus_bw.md`](results/reports/hyperbus_bw.md) | **measured** |
| L2 aggregate M20K bandwidth | banked, one port per reader = **38.4 GB/s = 100 % of theoretical** (32 banks × 4 B × 300 MHz). Shared round-robin = **1.2 GB/s (3.1 %) — a 32× penalty**, the cost of getting banking wrong. Output-register-off collapses M20K inference (33→1 blocks, 7× the ALMs). All checksum-verified vs golden — `results/l2_m20k_bw_*.json` | **measured** |

### PH3 — running a model on the AXC3000 (HyperRAM ↔ CoreDLA)

The AXC3000 has no LPDDR4 (only 16 MB HyperRAM), but CoreDLA requires a 256-bit AXI4 DDR memory.
PH3 bridges that gap (merged to `main`); design in
[`docs/ph3_interfaces.md`](docs/ph3_interfaces.md), [`docs/ph3_bridge_design.md`](docs/ph3_bridge_design.md),
[`docs/ph3_integration.md`](docs/ph3_integration.md); **ordered next-steps roadmap in
[`docs/ph3_coredla_nextsteps.md`](docs/ph3_coredla_nextsteps.md)**.

| Result | Detail | Status |
|---|---|---|
| AXI4↔HyperRAM bridge datapath | `rtl/hyperbus/axi4_hbmc_bridge.sv` — AXI4-256 write/read round-trips correct through the **real** `hbmc_core` + W957D8NB device model (bursts len 0/1/3/15, `arid→rid` echo, partial-WSTRB detected not corrupted) | **sim-verified** |
| Bridge fmax + P&R | **273.6 MHz** (slow corner; 250 MHz target met, +0.345 ns) · **1,014 ALM / 821 reg / 0 M20K / 0 DSP** on A3CY100 (`quartus/ph3_bridge_char`) | **synthesized** |
| LPDDR4 EMIF → HyperRAM swap | Platform Designer regenerates + `quartus_syn` **0 errors** on A3CY100, no unresolved black box; fitter enters (physical synthesis + clock promotion, 0 errors). Two integration bugs found + fixed. | **synthesized** (structural) |
| Model classifying on-board | — | **blocked** |

**Why on-board inference is still blocked** (honest handoff — ordered roadmap in
[`docs/ph3_coredla_nextsteps.md`](docs/ph3_coredla_nextsteps.md)):

1. ~~Real DDR-IO HyperBus PHY~~ — **CLOSED.** The `third_party/hyperram` submodule supplies a real,
   silicon-proven SDR PHY, measured on *this* board at 342 MB/s (see the bandwidth table above). The
   old tristate stub is gone; the `clk`/`clk2x`-from-one-IOPLL pattern it needs is solved and proven.
2. **PD *system* regen to source `clk2x` from the IOPLL** + **25 MHz IOPLL reparam + regenerated SDC +
   board pinout** — the component is already two-clock; what remains is wiring `clk2x` through the
   CoreDLA example system and a signed-off fit/STA (the stock example assumes a 100 MHz reference; the
   AXC3000 has only 25 MHz).
3. **CoreDLA CSR start/done handshake** — undocumented vendor-internal protocol
   (`sw/host/smoke_infer.py` leaves it `NotImplementedError`); needed to clock and count inferences.
4. **HyperRAM bandwidth ceiling** — the *measured* 342 MB/s feeding a 256-bit port is ~16× width-starved,
   so even once functional this system is HyperRAM-bandwidth-bound (the ~522 fps ad-toycar estimate above
   assumed 250 MB/s DDR; recompute at 342 MB/s — see the roadmap doc).

## Start here

| Doc | What it is |
|---|---|
| [`docs/PLAN.md`](docs/PLAN.md) | Canonical plan: silicon budget, rooflines, characterization ladder, tool flow. **Every number cites this.** |
| [`docs/axc3000_ai_capacity.html`](docs/axc3000_ai_capacity.html) | Original interactive plan document (sliders recompute the capacity tables) |
| [`docs/mlperf_tiny_v14_plan.md`](docs/mlperf_tiny_v14_plan.md) | Plan to adopt the latest **MLPerf Tiny v1.4** (Jul 2026): checkpoint provenance, streaming DS-CNN, Closed vs Open Division framing |
| [`docs/ph3_coredla_nextsteps.md`](docs/ph3_coredla_nextsteps.md) | Ordered roadmap to get a CoreDLA model classifying on the AXC3000 (PH3) |
| [`docs/board_bringup.md`](docs/board_bringup.md) | Board pinout / voltage / JTAG bring-up notes (Arrow User Guide provenance) |
| [`docs/toolchain.md`](docs/toolchain.md) | Quartus / FPGA AI Suite Docker flow, `dla_compiler` invocation, HETERO plugin constraint |
| [`docs/register_map.md`](docs/register_map.md) | Benchmark scoreboard CSR map |
| [`docs/record_format.md`](docs/record_format.md) | HyperRAM record-store binary format |
| [`AGENTS.md`](AGENTS.md) | Conventions for (AI) implementers — read before touching code |

## Roadmap = the issue tracker

Work is organized as GitHub issues grouped into milestones. Each issue is a self-contained spec:
context, exact deliverables, step-by-step instructions, acceptance criteria.

> **Status (2026-07):** M0 and most of M2 have landed — issues #1–#7, #9–#11, #13–#17, #21 are merged
> to `main`; the physical AXC3000 arrived and two benchmarks are **measured** (see Results above).
> Open follow-ups from a repo review + the MLPerf Tiny v1.4 pass: **#47** (v1.4 provenance),
> **#48–#51** (enable the four non-compiling models), **#52–#60** (RTL/Python/CI/reproducibility fixes).
> The original milestone table below is the initial v1 spec, preserved for reference.

| Milestone | Theme | Hardware needed? |
|---|---|---|
| M0 Toolchain & models | Quartus/AI Suite install, MLPerf Tiny model prep, quantization, record packer, results schema, estimator sweep | No (Quartus install only) |
| M1 Board bring-up | Stock hostless JTAG example, DDR-free example | Yes |
| M2 Characterization ladder | L0/L0b/L1/L2 microbenchmarks, HyperBus controller + L3 memtest | Mixed |
| M3 Benchmark harness | Scoreboard RTL, record-replay DMA, host runner, end-to-end integration (L5) | Mixed |
| M4 Measurement & analysis | L4 overlay cost, accuracy parity, power, quantization Pareto | Yes |
| M5 Stretch | Spatial Compiler, custom dataflow RTL | Yes |

Dependency spine: `M0 → M1 → (M2 ∥ M3-software) → M3-integration → M4 → M5`. Software-only issues
(label `software-only`) can be picked up in parallel at any time.

| # | Issue | Depends on |
|---|---|---|
| [#1](../../issues/1) | Quartus Pro ≥25.3 + FPGA AI Suite install | — |
| [#2](../../issues/2) | Model zoo → ONNX + fp32 baselines | — |
| [#3](../../issues/3) | OpenVINO IR + NNCF INT8 + CPU reference | #2 |
| [#4](../../issues/4) | Results tooling + CI | — |
| [#5](../../issues/5) | HyperRAM record packer | #3 |
| [#6](../../issues/6) | PH0 estimator sweep | #1 #3 |
| [#7](../../issues/7) | Hostless JTAG example on AXC3000 | #1 #3 |
| [#8](../../issues/8) | DDR-free example (MIF weights) | #7 |
| [#9](../../issues/9) | L0 tensor-mode chain + audit script | #1 |
| [#10](../../issues/10) | L0b soft-MAC density INT4/2/1 | #1 |
| [#11](../../issues/11) | L1 fmax vs PE-array sweep | #9 |
| [#12](../../issues/12) | L2 M20K bandwidth vs banking | #1 #7 |
| [#13](../../issues/13) | HyperBus controller port (W957D8NB) | #1 |
| [#14](../../issues/14) | L3 training + shmoo + sustained BW | #13 |
| [#15](../../issues/15) | Scoreboard RTL + testbench | — |
| [#16](../../issues/16) | Record-replay datapath | #13 #15 |
| [#17](../../issues/17) | Host benchmark runner | #15 #5 |
| [#18](../../issues/18) | Integration (a): L5 Tiny dataset | #8 #14 #15 #16 #17 |
| [#19](../../issues/19) | Integration (b): >400 KB models | #18 |
| [#20](../../issues/20) | L4 overlay fixed cost | #18 |
| [#21](../../issues/21) | Accuracy parity gate | #18 |
| [#22](../../issues/22) | Power → µJ/inference | #18 |
| [#23](../../issues/23) | LV7 quantization Pareto | #18 #10 |
| [#24](../../issues/24) | Spatial Compiler + dataflow memo | #18 #20 |

## Repository layout

```
docs/                 plan + canonical formats
rtl/
  common/             CDC wrappers, counters, shared package
  scoreboard/         §6 benchmark scoreboard (Avalon-MM CSRs)
  hyperbus/           HyperBus (HyperRAM) controller + PHY
  replay/             record-replay DMA + ping-pong buffering
  microbench/         L0 tensor chain · L0b soft MACs · L1 PE array · L2 M20K bandwidth
sim/                  self-checking testbenches (Verilator where possible)
quartus/              project revisions + constraints/ (.sdc)
platform_designer/    .qsys systems
sw/
  model_prep/         fetch → ONNX → OpenVINO IR → NNCF INT8 → CPU reference
  packer/             dataset → HyperRAM record image (docs/record_format.md)
  host/               System Console benchmark runner + register readback
models/arch/          FPGA AI Suite architecture files
scripts/              build automation, fitter-report audits
results/              one JSON per result, conforming to results/schema/result.schema.json
```

## Hardware

- Arrow AXC3000 (Agilex 3 A3CY100BM16AE7S), Winbond W957D8NB 16 MB HyperRAM, 32 MB QSPI, on-board USB-Blaster III JTAG (`09fb:6022`)
- Host tools: Quartus Prime Pro ≥25.3 + FPGA AI Suite (free Agilex 3 license), Python ≥3.10
- Optional: inline USB-C power meter for µJ/inference numbers
