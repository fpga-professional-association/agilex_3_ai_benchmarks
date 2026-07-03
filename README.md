# Agilex 3 AI Benchmarks — AXC3000

Independent AI-inference characterization of the Altera **Agilex 3** FPGA (A3CY100BM16AE7S, no HPS)
on the $129 Arrow **AXC3000** board: what fits in 559 KB of M20K and 138 tensor-mode DSPs, and how
fast it classifies when fed from a 16 MB HyperRAM over an 8-bit HyperBus.

Agilex 3 silicon shipped in 2025 and the third-party performance literature is effectively empty —
no tensor-capture audits, no fmax-vs-array-size curves, no HyperBus shmoos, no MLPerf-Tiny-class
numbers. This repo produces that dataset on vendor-public silicon.

## Start here

| Doc | What it is |
|---|---|
| [`docs/PLAN.md`](docs/PLAN.md) | Canonical plan: silicon budget, rooflines, characterization ladder, tool flow. **Every number cites this.** |
| [`docs/axc3000_ai_capacity.html`](docs/axc3000_ai_capacity.html) | Original interactive plan document (sliders recompute the capacity tables) |
| [`docs/register_map.md`](docs/register_map.md) | Benchmark scoreboard CSR map |
| [`docs/record_format.md`](docs/record_format.md) | HyperRAM record-store binary format |
| [`AGENTS.md`](AGENTS.md) | Conventions for (AI) implementers — read before touching code |

## Roadmap = the issue tracker

Work is organized as GitHub issues grouped into milestones. Each issue is a self-contained spec:
context, exact deliverables, step-by-step instructions, acceptance criteria.

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

- Arrow AXC3000 (Agilex 3 A3CY100BM16AE7S), Winbond W957D8NB 16 MB HyperRAM, 32 MB QSPI, USB-C FTDI JTAG
- Host tools: Quartus Prime Pro ≥25.3 + FPGA AI Suite (free Agilex 3 license), Python ≥3.10
- Optional: inline USB-C power meter for µJ/inference numbers
