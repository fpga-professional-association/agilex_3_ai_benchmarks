# MLPerf Tiny on-board benchmark runbook (Track C, PLAN §9 PH3)

The one locked command sequence to benchmark all four MLPerf Tiny models on the AXC3000 CoreDLA
**DDR-free** bitstream (Track A) the moment the board is free. Nothing here fabricates a number: the
runner (`sw/host/run_tiny_benchmark.py`) only emits `results/` JSONs when it actually talks to the
board; off-board it exits 3.

The four models (registry ids ↔ MLPerf Tiny Closed benchmarks):

| registry id           | MLPerf Tiny benchmark        | metric | output |
|-----------------------|------------------------------|--------|--------|
| `ds-cnn-kws`          | Keyword Spotting (KWS)       | top-1  | 12 classes |
| `resnet8-cifar10`     | Image Classification (IC)    | top-1  | 10 classes |
| `mobilenetv1-025-vww` | Visual Wake Words (VWW)      | top-1  | 2 classes  |
| `ad-toycar`           | Anomaly Detection (AD)       | AUC    | recon. error |

---

## 0. Prerequisites (all OFF-board — do these before you touch the devkit)

These have no board dependency; do them while another agent may still hold the board.

1. **Bitstream (Track A).** A programmed-able DDR-free `top.sof` exists at
   `quartus/coredla_agx3_ddrfree/output_files/top.sof`, compiled against
   `models/arch/AGX3_Performance.arch`, with a known fabric clock `F_CLK_MHZ` (from the fit's
   timing report — do **not** guess it; it goes into every result as `config.fclk_mhz`).

2. **Track B handshake.** `coredla_csr_handshake` (the CSR start/done + hw-timer sequence) is
   available on `sw/host`'s import path. `CoreDlaCsrTransport` imports it if present; the exact
   CoreDLA CSR start/done/timer bit offsets are confirmed on-board (SignalTap or vendor CSR map),
   never guessed (same discipline as `sw/host/smoke_infer.py`).

3. **Reference bundles.** One per model under `results/tiny_bundles/<model_id>/`, each containing
   `reference.json` + `records/rec_*.bin` (and optional `records/ref_*.bin` raw INT8 outputs). A
   bundle is built from existing tooling, no new measurement:
   - inputs: quantize + serialize the model's MLPerf Tiny test set with the packer
     (`sw/packer/pack_records.py` / `packlib.quantize_int8`) using the IR's scale/zero-point.
   - `cpu_pred` + `cpu_int8` metric: run `sw/model_prep/eval_int8_cpu.py` for that model to produce
     the CPU-INT8 reference JSON (`results/ph2_<id>-int8_*.json`); record its per-record argmax as
     `cpu_pred` and its aggregate `top1`/`auc` as `cpu_int8.value`. This is the exact reference the
     hardware is cross-checked against.
   `reference.json` shape (see `load_bundle` in `run_tiny_benchmark.py`):
   ```json
   {
     "model_id": "ds-cnn-kws", "metric": "top1", "output_bytes": 12,
     "input_addr": 0, "output_addr": 1048576,
     "cpu_int8": {"metric_name": "top1", "value": 0.87,
                  "results_path": "results/ph2_ds-cnn-kws-int8_20260704.json"},
     "records": [
       {"file": "records/rec_00000.bin", "label": 3, "cpu_pred": 3,
        "ref_output_file": "records/ref_00000.bin"}
     ]
   }
   ```

4. **Unit tests green** (proves the latency/throughput/accuracy math with no board):
   ```bash
   /home/tcovert/projects/agilex_3_ai_benchmarks/.venv/bin/python \
       -m pytest sw/host/tests/test_run_tiny_benchmark.py
   # 15 passed
   ```

---

## 1. Acquire the devkit lock (REQUIRED — board is shared across agents)

Every board-touching step below runs **inside** `scripts/devkit_lock.sh`. Use the `with` form so the
lock is always released, even on failure. Do all four models under a single held lock so you don't
thrash the board:

```bash
cd /home/tcovert/projects/agilex_3_ai_benchmarks   # or this worktree's checkout
scripts/devkit_lock.sh with "coredla-tiny-agent" "MLPerf Tiny on-board benchmark" -- \
    bash -lc '
      source scripts/env.sh
      set -euo pipefail
      SOF=quartus/coredla_agx3_ddrfree/output_files/top.sof
      ARCH=models/arch/AGX3_Performance.arch
      FCLK=<F_CLK_MHZ>          # from the Track A timing report — do not guess

      # 2. Program the DDR-free bitstream (control plane; see AXC3000 JTAG note below)
      quartus_pgm -c 1 -m jtag -o "p;${SOF}"

      # 3. Run all four models (single-stream latency + accuracy each)
      for M in ds-cnn-kws resnet8-cifar10 mobilenetv1-025-vww ad-toycar; do
        python sw/host/run_tiny_benchmark.py \
          --bundle   results/tiny_bundles/${M} \
          --sof      ${SOF} \
          --arch-file ${ARCH} \
          --fclk-mhz ${FCLK} \
          --mode both \
          --out-dir  results/
      done
    '
```

Notes:
- The runner itself has a guard: if `scripts/devkit_lock.sh status` reports **FREE** (i.e. you forgot
  the wrapper), it refuses to touch the board and prints the exact `devkit_lock.sh with …` line. Pass
  `--no-lock-check` only for dry documentation runs, never on the real board.
- AXC3000 programming path caveat: the USB-Blaster III programming route on this host is not the
  plain `quartus_pgm` line above in every setup — follow whatever the board-owning runbook / memory
  note prescribes for *this* devkit (the important part for Track C is that programming happens
  **once**, under the held lock, before the four runs).

---

## 2. What each run emits

Per model, `--mode both` writes two schema-valid `results/` JSONs (`kind:"measured"`, `level:"L5"`):

- `results/ph3_<model_id>-tiny-latency_<YYYYMMDD>.json` — MLPerf Tiny **performance** mode:
  `latency_us_p50`, `latency_us_p99`, `latency_us_min/max`, `fps`, `n_records`.
  Latency is per-inference **CoreDLA on-fabric hw-timer cycles** → µs via `config.fclk_mhz`
  (PLAN §8 method E: JTAG control-plane only; the input is resident before timing, so cycles bracket
  compute, not JTAG transfer). Single-stream throughput `fps = N / Σ latency`.

- `results/ph3_<model_id>-tiny-accuracy_<YYYYMMDD>.json` — MLPerf Tiny **accuracy** mode:
  `n_records`, and `accuracy_top1` (argmax vs ground-truth label for the three classifiers).
  Notes carry the cross-checks vs the CPU-INT8 reference: `cpu_int8_argmax_agreement` (always) and
  `raw_int8_output_match` (when the bundle ships raw INT8 reference outputs). For `ad-toycar`
  (metric = AUC) the on-fabric gate is the cross-check agreement and the reported `accuracy_top1`
  field carries the CPU-INT8 AUC the device is shown to reproduce (device AUC scoring is **not**
  re-implemented — flagged in the result's notes).

Unlicensed CoreDLA IP is capped at 10 000 inferences (PLAN §9 PH1); the runner enforces this and
prints a message. Pass `--licensed-ip` to lift it (and to run the full validation set for a
Closed-division-grade accuracy number). Use `--max-records N` to cap deliberately.

---

## 3. Record + release

1. Validate the emitted JSONs against the schema (off the board is fine — you can keep the lock or
   release first):
   ```bash
   python scripts/validate_results.py results/ph3_*-tiny-*.json
   ```
2. The `devkit_lock.sh with …` wrapper releases the lock automatically when the inner command exits.
   Confirm with `scripts/devkit_lock.sh status` → `FREE`. If you ran steps by hand instead of `with`,
   release explicitly: `scripts/devkit_lock.sh release "coredla-tiny-agent"`.

---

## 4. Mapping to MLPerf Tiny Closed-division reporting

| MLPerf Tiny Closed field        | Where it comes from here |
|---------------------------------|--------------------------|
| Benchmark (KWS/IC/VWW/AD)       | `config.model` ↔ table at top |
| Latency (single-stream)         | `metrics.latency_us_p50` (median); p99 also reported |
| Throughput (inf/s)              | `metrics.fps` = `N / Σ latency` |
| Accuracy (top-1 / AUC)          | `metrics.accuracy_top1` (classifiers = device top-1; AD = CPU-INT8 AUC reproduced, see notes) |
| Quantization                    | `config.quantization = int8-nncf-ptq` |
| System / device                 | `config.device`, `config.board`, `config.fclk_mhz` |
| Architecture / IP provenance    | `config.arch_file` (AGX3 CoreDLA), `config.report_paths` = the `.sof` |

Closed-division rules honored:
- **Same quantized model** the reference measures (INT8 NNCF-PTQ IR), cross-checked record-for-record
  against the OpenVINO CPU-INT8 path — the hardware must reproduce the reference predictions
  (`cpu_int8_argmax_agreement` ≈ 100 %, `raw_int8_output_match` ≈ 100 % when available). A large
  disagreement means a datapath/packing bug, **not** a valid accuracy number — investigate before
  reporting.
- **Single-stream** latency measured on the DUT's own timer (not host wall-clock), median reported.
- **Accuracy mode** over the validation set (needs `--licensed-ip` to exceed the 10 000-inference
  cap for a full-set number).

---

## Hardware handoff — remaining board-gated steps (cannot be done off-board)

Everything above the board line is implemented and unit-tested; the following require the physical
AXC3000 and are **not** done in this session (no board access):

1. **Track A**: finish/compile the DDR-free `top.sof`; read `F_CLK_MHZ` from its timing report.
2. **Track B**: land `coredla_csr_handshake` and confirm the CoreDLA CSR start/done + hw-timer bit
   offsets on-board (SignalTap / vendor CSR map). Fill in `CoreDlaCsrTransport.run_inference`
   (write START → poll STATUS.DONE → read `DLA_TIMER_OFFSET` cycles) using it.
3. **Bundles**: generate `results/tiny_bundles/<model>/` for the four models (§0.3).
4. **Run**: execute §1 under the devkit lock; collect the eight JSONs; validate (§3); release.
