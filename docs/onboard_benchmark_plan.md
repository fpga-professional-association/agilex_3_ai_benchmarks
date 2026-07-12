# On-board MLPerf Tiny benchmark plan — 2 models DDR-free + 4 models HyperRAM

Fable-authored plan. Goal: measured MLPerf-Tiny latency/throughput/accuracy on the AXC3000, two ways —
**all four models memory-bound (HyperRAM)** and **two models compute-bound (DDR-free)** — with `resnet8`
measured *both* ways as the headline memory-wall comparison. Investigation-first; no code until approved.

## 0. Investigation result — why "2 of 4" DDR-free, and which 2

DDR-free's binding constraint is **not weight size** (all 4 weights are 23–268 KB, well under 559 KB M20K).
It is the compiler rule **"slicing is not supported when external memory is disabled"** → the whole
activation tensor + config/filter caches must live on-chip.

| Model | DDR-free verdict | Real reason (measured) |
|---|---|---|
| **resnet8** | ✅ fits (`.sof` built) | 32×32 activations → 4096 stream buffer; 224/262 M20K |
| **ds-cnn** | ⚠️ **fixable** | Not a resource miss. Its 4 **GroupConvolution** (depthwise) layers get lowered to split+**concat**, unsupported in DDR-free. Weights smallest (~36 KB), activations tiny (49×10). |
| ad-toycar | ❌ borderline overflow | 268 KB weights (biggest) + mandated `config_cache`(8449)/`filter` floors → 328/262 M20K (125%) |
| vww | ❌ fundamental | first activation 48×48×8 = **18,432** must sit on-chip (no slicing) → 589/262 M20K (225%). Needs a smaller model (Open). |

**The 2nd DDR-free model = `ds-cnn`, via an equivalence-preserving rewrite** (fallback: `ad-toycar` config-trim).
The concat comes from the depthwise `GroupConvolution` lowering, so:

> **Rewrite each depthwise conv as a block-diagonal *dense* conv** — a `groups=1` `Conv` whose weight
> `[C,C,kh,kw]` is zero except the per-channel diagonal blocks `[c,c,:,:] = depthwise_weight[c]`. This is
> **bit-exact** to the depthwise conv (Closed-Division-legal), and being ungrouped it lowers with **no
> split/concat** → DDR-free-legal. Cost: the 4 depthwise layers grow ~C× in weight+compute, but ds-cnn's
> C is small and its depthwise layers are cheap, so the on-chip footprint plausibly stays near resnet8's.

**Honest caveat:** that the rewritten ds-cnn *fits* M20K is a hypothesis to confirm by building it (step B1) —
the concat blocker is definitely removed, but the dense-weight growth vs the 262-M20K budget is build-to-verify.
If it doesn't fit, fall back to `ad-toycar` (trim `config_cache_depth`, or accept DDR-free = resnet8-only + report the finding).

## 1. The two tracks + the crux they share

Both tracks are BUILT to the bitstream level (HyperRAM `top.sof` proof-of-life done; resnet8 DDR-free `.sof`
done). The single remaining engineering piece — **the host inference driver** — is shared, in two variants:

- **DDR-backed driver (HyperRAM path):** write each `.aot`'s config-words + weights + input into HyperRAM over
  the JTAG-Avalon master, **guard-banded** so no write base abuts live data (the write-wound law, confirmed live
  §silicon proof-of-life); run the CSR handshake (`CONFIG_BASE`→`CONFIG_RANGE-2`→`INPUT_OUTPUT_BASE` **last =
  trigger**→poll `COMPLETION_COUNT`); read the output region back.
- **Streaming driver (DDR-free path):** weights are on-chip MIF ROMs (nothing to load); push the input tensor
  into `ingress_onchip_memory` via `ingress_msgdma`, run, pull the result from `egress_onchip_memory` via
  `egress_msgdma`. Uses the streaming CSR-ready/completion registers, not DDR base addresses.

Both build on `sw/host/coredla_csr_handshake.py` (register map resolved) + `sw/host/run_tiny_benchmark.py`
(mock-tested off-board), and both run **under the devkit lock**. The transport gap to close: a real
System-Console/JTAG `master_read_32`/`master_write_32` backend for the `Transport` abstraction (today a stub) —
the smoke test already proved the raw `master_*` calls reach both the CSR and the HyperRAM window.

## 2. Track A — all 4 models, HyperRAM (memory-bound)

Per model {ad-toycar, ds-cnn, resnet8, vww}, on the one model-agnostic HyperRAM `top.sof` (already programs;
`clk_dla` retuned to 280 MHz, parity-gated):
1. Guard-banded-load config/weights/input → 2. CSR handshake → 3. read output →
4. **parity-check vs the CPU-INT8 reference** (reject any run whose output ≠ reference) →
5. measure latency via the on-chip `hw_timer`/`clocks_active` counter (never wall-clock-over-JTAG, PLAN §8 method E),
   throughput, and accuracy on the full test set.

## 3. Track B — 2 models, DDR-free (compute-bound)

- **B1 `resnet8`** — `.sof` built; program + stream-drive + measure (steps as §1 streaming driver, + parity).
- **B2 `ds-cnn`** — do the depthwise→dense rewrite (§0), re-export → INT8 requant → DDR-free `dla_create_ip` →
  confirm it **fits** (fanalyze-area ≤ 262 M20K / 34k ALM) → build `.sof` → program + measure. If it doesn't fit,
  fall back to `ad-toycar` or report DDR-free = resnet8-only.

## 4. Metrics (MLPerf Tiny, per model per path)

Single-stream **latency** (p50/p99, on-chip timer), **throughput** (fps), **accuracy** (top-1 / AUC vs the CPU-INT8
reference, full test set). Recorded as schema-valid `results/l5_<model>_<path>.json` (`kind: measured`), one per
model per path. Energy (µJ/inf, #22) is a later add (needs the meter).

## 5. The headline: memory wall vs compute wall (measured)

`resnet8` both ways is the money shot: **HyperRAM (memory-bound, ~340 MB/s ceiling) vs DDR-free (compute-bound,
weights on-chip)** — the same model, same accuracy, two latency/throughput regimes on the same $129 board. Plus:
all four HyperRAM numbers (the full MLPerf Tiny set) and ds-cnn DDR-free.

## 6. Sequencing + honest risks

1. **Build the DDR-backed driver + run `resnet8` on HyperRAM first** (both `.sof` + the CSR/memory path are already
   proven on silicon) — lowest-risk first real inference number.
2. Extend to the other 3 HyperRAM models (same driver, per-model `.aot`).
3. **Build the streaming driver + run `resnet8` DDR-free** (`.sof` built).
4. **ds-cnn DDR-free rewrite + build + run** (the one with build-risk).
5. Update the README with both result sets.

Risks, stated plainly:
- **The inference driver is the real remaining work** and the genuine uncertainty — first CoreDLA inference on this
  board via a bespoke hostless flow. The control plane + memory are proven; the descriptor/weight *layout* and the
  streaming DMA path are not yet exercised end-to-end.
- **`clk_dla` is 44-endpoints-marginal** (−0.156 ns) on the HyperRAM `.sof` — every number is **parity-gated**; if a
  model's output ever mismatches the reference, retune to ~260 MHz and re-run (do not report a failed-parity number).
- **ds-cnn DDR-free fit is unproven** until built (§0 caveat).
- **HyperRAM throughput will be low** (memory-bound, ~16× width-starved) — that is the measured finding, not a defect.

## 7. Definition of done

Four `results/l5_<model>_hyperram.json` + two `results/l5_<model>_ddrfree.json` (`kind: measured`, parity-passed),
and a README section reporting both, with `resnet8` side-by-side memory-bound vs compute-bound.
