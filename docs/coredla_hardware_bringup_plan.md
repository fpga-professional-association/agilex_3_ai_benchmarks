# CoreDLA-on-AXC3000 hardware bring-up — plan & supervision

Goal: get all four MLPerf Tiny v1.4 models **running on the physical AXC3000** and benchmarked
(latency / throughput / accuracy / energy). The models already **compile** 100% to the Agilex-3
CoreDLA IP ([`tinyml_all_models_plan.md`](tinyml_all_models_plan.md), `sw/model_prep/graph_ops/`);
this plan is the remaining hardware integration. Fable-authored; the build/investigate work is run by
supervised opus agents (see §4). No invented numbers (AGENTS.md).

## 0. Coordination constraints (this is a shared machine)

- **One board, many agents.** Another Claude agent is actively running the devkit (HyperRAM-speed
  work). All board access (program / jtag / system-console / usbipd / `dla_benchmark`-on-board) MUST
  hold [`scripts/devkit_lock.sh`](../scripts/devkit_lock.sh) — a machine-wide atomic advisory lock,
  safe by default (never auto-steals). Wrap board commands as
  `scripts/devkit_lock.sh with "coredla-agent" "<reason>" -- <cmd>`.
- **Off the HyperRAM datapath.** `rtl/hyperbus/**` and `third_party/hyperram/**` belong to the
  HyperRAM-speed agent — read-only for us. This is a strong reason to prefer the DDR-free path (§1B),
  which needs no HyperRAM at all.
- **This session: no board.** The board is busy; all work here is build / investigate / prepare so
  that the moment the board frees up, running all four models is one locked command.

## 1. Two paths to on-board inference

### Path A — HyperRAM-backed (the PH3 effort, #18)
CoreDLA streams weights from the 16 MB HyperRAM over the AXI4 bridge. This is the general path (works
for models > on-chip budget) but depends on: the HyperRAM datapath (another agent), the PH3 clk2x /
IOPLL / SDC / board-pinout integration, and the CSR handshake (§2). Higher effort, more shared
dependencies. Not our focus this session.

### Path B — DDR-free / on-chip weights (the chosen fast path) ✅
The AI Suite ships AGX3 CoreDLA support (`/opt/altera/fpga_ai_suite/…/fpga/top/quartus/AGX3`) and a
**DDR-free config** (`dla_ddrfree_config_data_read.sv`). The Tiny models are tiny — INT8 weights
**fit entirely in the 559 KB M20K** (ad-toycar 267 KB · resnet8 78 KB · ds-cnn 23 KB · vww ~200 KB),
so a DDR-free CoreDLA holds weights on-chip and needs **no external memory at all**. That **sidesteps
the HyperRAM datapath and the entire PH3 memory-subsystem blocker** — potentially the shortest route
to a real on-board Tiny number. The open question (opus Track A resolves it): does the AI Suite
actually support a DDR-free build for **AGX3** in 2026.1.1, or only AGX5/7? Answer decides A vs B.

## 2. The crux blocker — the CoreDLA CSR start/done handshake

Independent of A/B, running an inference needs the CoreDLA CSR sequence that loads a descriptor,
kicks off inference, and polls done (CoreDLA CSR at `0x8000_0000`, range `0x900`). `sw/host/
smoke_infer.py`'s `run_inference()` is `NotImplementedError` because this was undocumented. **But the
vendor runtime implements it**, so it's discoverable from the shipped AI Suite (runtime lib / OpenVINO
FPGA plugin / `dla_benchmark` / the generated host program) — not a true unknown. Opus Track B extracts
it (SW investigation, no board) and delivers a drop-in `coredla_csr_handshake.py`.

## 3. Success criteria

1. **[Track A]** A DDR-free (or, if unsupported, structurally-closed) AGX3 CoreDLA example design that
   Quartus-compiles for `A3CY100BM16AE7S` (synth→fit→timing, no board) with one Tiny `.aot`, resource
   report captured. Bitstream produced (not programmed).
2. **[Track B]** The CSR start/done/config sequence documented + implemented behind the Transport
   abstraction, unit-tested with a mock.
3. **[Track C]** `run_tiny_benchmark.py` + runbook: acquire lock → program bitstream → run all four
   models → record schema-valid `results/l5_*.json` → release lock. Mock-tested without a board.
4. **[Board, when free]** Execute the runbook under the devkit lock → the project's first **measured**
   MLPerf-Tiny latency/throughput/accuracy on the AXC3000.

## 4. Supervision — opus workstreams (this push)

Workflow `coredla-agx3-hardware-prep` (opus, background), 3 parallel tracks matching §3.1-3.3, all
build/investigate/prepare with **no board** and **no HyperRAM edits**. Deliverables land under
`quartus/coredla_agx3_ddrfree/`, `sw/host/`, and `docs/coredla_*_findings.md`. Fable reviews each
track's honest verdict (does DDR-free AGX3 build? is the handshake fully recovered? does the harness
mock-test pass?) and folds the results into this plan + the issue tracker (#8 DDR-free, #18 integration).

## Sources / ties
- [`tinyml_all_models_plan.md`](tinyml_all_models_plan.md) (compile path) · [`ph3_coredla_nextsteps.md`](ph3_coredla_nextsteps.md)
  (HyperRAM path A) · [`mlperf_tiny_v14_plan.md`](mlperf_tiny_v14_plan.md) (benchmark rules) · issues #8, #18, #22.
