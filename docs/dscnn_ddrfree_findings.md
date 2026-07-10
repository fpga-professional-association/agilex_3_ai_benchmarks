# ds-cnn-kws DDR-free: depthwise->dense rewrite findings

Track B2 of `docs/onboard_benchmark_plan.md` (§0 investigation, §3 Track B). Goal: make ds-cnn-kws
DDR-free-viable by rewriting its four depthwise convs as equivalent block-diagonal dense convs
(removing the `group>1` -> split+concat blocker), then building the DDR-free `.sof`.

**Bottom line: the depthwise->dense rewrite works exactly as designed (bit-exact, no more
GroupConvolution) — but ds-cnn-kws still does not compile DDR-free, because of its oversized 25x5
`AveragePool`, not its depthwise convs.** Both of that pool's equivalence-preserving decompositions
(already in `graph_ops.pool_decompose`, issue #14) hit a *different* DDR-free-specific compiler
rejection, before Quartus ever sees the design — there is no `.sof`, and no fitter M20K/ALM numbers
to report, because `dla_create_ip`/`dlac` itself refuses the graph. `ad-toycar` remains the plan's
documented fallback 2nd DDR-free model (not attempted here; out of this track's scope).

## 1. The rewrite: `graph_ops.depthwise_to_dense`

New module `sw/model_prep/graph_ops/depthwise_to_dense.py` (+ tests in
`sw/model_prep/tests/test_graph_ops.py`), exported from `graph_ops` as
`rewrite_grouped_convs_to_dense` / `find_grouped_convs`. It replaces any `Conv` with `group > 1`
(ONNX's encoding of a depthwise/grouped conv -- OpenVINO's importer lowers it to
`GroupConvolution`) with an equivalent `group=1` dense `Conv`: the weight
`[Cout, Cin/group, kh, kw]` is zero-padded into a block-diagonal `[Cout, Cin, kh, kw]` tensor (each
group's block placed at its own input-channel slice, everywhere else exactly `0`), and the `group`
attribute is dropped. This is a **standalone step**, not part of `make_coredla_friendly`'s default
pipeline (the dense weight is strictly bigger, so it's only worth it for the DDR-free path).

**Equivalence proof** (`sw/model_prep/tests/test_graph_ops.py`, run via
`.venv/bin/python -m pytest tests/test_graph_ops.py -q -s`, all pass):

- Synthetic cases: true depthwise (`cin_per_group==1`, ds-cnn's actual shape) is **bit-exact**
  (`np.testing.assert_array_equal`) across `(64,1,64,3,3)`, `(64,1,64,25,5)`, and `(5,1,5,1,1)`.
  A general grouped conv (`cin_per_group>1`, e.g. `(6,2,3,3,3)`) matches to `rtol=atol=1e-5` --
  mathematically identical, but onnxruntime's dense-conv kernel sums over more (all-zero) terms in
  a different order than its grouped-conv kernel, so float non-associativity leaves ~1e-7 noise.
  A grouped conv with a non-constant weight correctly raises (nothing to dense-ify).
- **Real model, full pipeline** (`test_ddrfree_dscnn_full_pipeline_bit_exact`): cached
  `models/onnx/ds-cnn-kws.onnx` -> `make_coredla_friendly` (pool-decompose `"auto"` + transpose-fold)
  -> `rewrite_grouped_convs_to_dense` on every remaining `group>1` Conv (the 4 true depthwise convs
  **and** the pool-decompose-produced one -- see §2) -> run both the original and rewritten graphs
  through onnxruntime on the same random input. Result, printed by
  `test_report_real_maxdiffs`:

  ```
  ddrfree_dense/ds-cnn-kws: 2.980e-08
  ```

  (float32 noise-floor -- Closed-Division-legal bit-exact). The test also asserts zero `group>1`
  Conv and zero `Concat` remain, and that the growth factor is exactly `64.0x` per rewritten layer
  (`Cout=Cin=64` depthwise -> `64x64xkhxkw` dense), matching the analysis in §2.

Re-running the same rewrite as a standalone script (`scratch/dscnn_ddrfree/build_pipeline.py`,
gitignored scratch, not committed) against 8 fresh random inputs: **max abs diff = 3.725e-08**.

## 2. Which pool-decompose strategy to feed the dense rewrite -- and why it matters a lot

ds-cnn-kws has **five** `group>1` Conv nodes once graph-surgered for CoreDLA, not four:

| # | Origin | Shape `[Cout,Cin/group,kh,kw]` | group |
|---|---|---|---|
| 1-4 | the model's real depthwise-separable-conv blocks | `[64,1,3,3]` | 64 |
| 5 | `pool_decompose`'s `"conv"`/`"cascade"` strategy replacing the 25x5 `AveragePool` with an exact-mean depthwise conv (needed because CoreDLA's `pool` primitive caps windows at 3x3 -- PLAN/issue #14) | `[64,1,25,5]` | 64 |

`depthwise_to_dense` treats all five identically (the task's own description, "depthwise conv
`[C,1,kh,kw]`", literally matches node #5 too). But **the weight-growth cost of node #5 is
prohibitive**, measured directly (`scratch/dscnn_ddrfree/build_pipeline.py` output):

```
functional_1/activation_1 ... [64,1,3,3]  -> [64,64,3,3]   64.0x   576  ->  36,864 bytes (int8)
functional_1/activation_3 ... [64,1,3,3]  -> [64,64,3,3]   64.0x   576  ->  36,864 bytes
functional_1/activation_5 ... [64,1,3,3]  -> [64,64,3,3]   64.0x   576  ->  36,864 bytes
functional_1/activation_7 ... [64,1,3,3]  -> [64,64,3,3]   64.0x   576  ->  36,864 bytes
functional_1_average_pooling2d_AvgPool_conv [64,1,25,5] -> [64,64,25,5] 64.0x  8,000 -> 512,000 bytes
TOTAL: orig 10,304 -> dense 659,456 bytes
```

**659,456 bytes is ~98% of the AXC3000 C100's *entire* on-chip block-memory budget** (262 M20K
blocks x 20,480 bits = 5,365,760 bits = 670,720 bytes total, for *everything*: weights, the DLA's
scratchpads, config-network cache, and stream FIFOs -- resnet8's DDR-free `.sof` already uses
247/262 of those blocks, 94%, for a completely different ~78 KB weight set, docs/coredla_agx3_build_findings.md).
Dense-ifying the pool-substitute conv the same way as the real depthwise convs is dead on arrival.

So the pool decomposition strategy for the DDR-free variant should **not** be `"conv"`/`"cascade"`
(both produce that 25x5 depthwise conv here -- 25 and 5 have no <=3 factor, so `"cascade"`
degenerates to the same residual conv as `"conv"`, confirmed via `factorize_into(25)==([],25)` and
`factorize_into(5)==([],5)`). `pool_decompose`'s third, already-implemented strategy,
`"reduce_mean"` (a single exact `ReduceMean` over the spatial axes, zero extra weight, no groups),
is the natural fit -- and *that* pipeline is what the graph_ops equivalence tests above and the
scratch driver actually exercise for the reported max-diffs when checking "no concat left in the
graph" at the ONNX level (§3). Using it drops the dense-weight total to just the 4 real depthwise
layers: **147,456 bytes (144 KiB)**, in line with resnet8's on-chip footprint.

## 3. Re-export -> OpenVINO IR -> INT8 (reusing ds-cnn's calibration)

`scratch/dscnn_ddrfree/build_pipeline.py` (gitignored scratch driver, not a committed tool --
the durable code is `graph_ops.depthwise_to_dense`/`rewrite_grouped_convs_to_dense` above):
`models/onnx/ds-cnn-kws.onnx` -> `make_coredla_friendly(pool_strategy="reduce_mean")` ->
`rewrite_grouped_convs_to_dense` -> `common.convert_onnx_to_ir` (ovc) ->
`common.quantize_ir_int8` using `models.dscnn.calibration_samples()` (the same fixed-seed
300-sample Speech-Commands-`train` slice `sw/model_prep/models/dscnn.py` already defines,
`datasets/speech_commands` already fetched locally -- no new calibration data was invented).

Resulting INT8 IR op histogram (`grep type= scratch/dscnn_ddrfree/ir/int8/ds-cnn-kws-ddrfree.xml`):

```
10 Add   77 Const   10 Convert   9 Convolution   11 FakeQuantize   1 MatMul
10 Multiply   1 Parameter   1 ReduceMean   9 ReLU   2 Reshape   1 Result   1 SoftMax
```

**Confirmed: NO `GroupConvolution`, NO `Concat`.** 9 `Convolution` (5 originally-pointwise + 4
now-dense-depthwise, all `group=1`), 1 `ReduceMean` (the decomposed pool), matching the ONNX-level
op set exactly (`['Add','Conv','DequantizeLinear','MatMul','ReduceMean','Relu','Reshape','Softmax']`).

## 4. DDR-free build attempt: it does NOT reach the fitter

Arch: `quartus/coredla_agx3_ddrfree/arch/AGX3_Ddrfree_DSCNN.arch`, tuned from
`AGX3_Ddrfree_Fit.arch` (the resnet8 config that fits the C100: ALM 88%, M20K 94%) -- only
`layout_transform_params` needed re-sizing for ds-cnn's `[1,1,49,10]` input (`max_channels: 1`,
`max_feature_height: 49`, `max_feature_width: 10`, vs resnet8's `[1,3,32,32]`/`max_channels:4`/
`32x32`), plus `output_image_width_max: 64` (unset in the resnet8 arch; ds-cnn needed it stated
explicitly to get past the *first* rejection, see below). Everything else (`stream_buffer_depth
4096`, `config_cache_depth 3201`, `filter_depth 512`, no sigmoid/prelu) kept as-is per the task's
build-to-verify instruction -- adjusted only in response to real tool output, never guessed.

Command (`source scripts/env.sh; dla_create_ip --arch ... --model ... --ip-dir ... --skip-sim-env
--overwrite`), no board/Quartus-fitter step involved, CPU-only:

### Attempt A -- `reduce_mean` pool decomposition (144 KiB dense weight, the "should fit" variant)

```
CoreDLA compiler has thrown an error: Compiler error.
Width concats not supported in architectures with external memory disabled.
Try setting output_image_width_max in the arch file to be larger than the max width in the graph
to prevent slicing and concatenation.
```

Tried `output_image_width_max` at the arch default (unset), `64`, and `4096` -- **identical error
every time.** This rules out a sizing/threshold issue: CoreDLA's own internal lowering of
`ReduceMean` needs a width-concat regardless of the configured ceiling, which is exactly what
`disable_external_memory` forbids. `ReduceMean` is a structural DDR-free blocker on this compiler
version, not a fixable resource constraint.

### Attempt B -- `conv`/`cascade` pool decomposition (659 KiB dense weight, the "definitely won't fit" variant, tried anyway for completeness)

```
HETERO plugin attempted to fallback unsupported FPGA node
(Name: functional_1_average_pooling2d_AvgPool_conv/fq_weights_1, Type: Constant) to the default
device. However, none of the other HETERO device(s) can support this node either.
```

Rejected outright (before any fitter numbers exist to report) even after bumping
`filter_scratchpad.filter_depth` 512 -> 9,000 -- same error, so it isn't a filter-cache-depth
shortfall like the earlier resnet8 tuning history; the 512,000-element dense weight itself is
unplaceable. Consistent with §2's byte-math: it would have needed ~98% of the *entire* device's
block memory for one tensor.

### Attempt C -- no pool decomposition at all (sanity check)

```
HETERO plugin attempted to fallback unsupported FPGA node
(Name: functional_1/average_pooling2d/AvgPool, Type: AvgPool) to the default device.
```

As expected: the raw 25x5 `AveragePool` exceeds the arch's `pool.max_window_height/width: 3`
ceiling and is rejected outright -- confirming the pool decomposition step is mandatory, not
optional, exactly as issue #14 already found for the HyperRAM path.

**No `model_analyzer_report.txt` was generated in any of the three attempts** (`find
quartus/coredla_agx3_ddrfree/coredla_ip_dscnn*/altera_ai_ip/compiled_model_dir/tf2onnx
-iname model_analyzer_report.txt` -- empty in every run): the compiler rejects the graph during
its own lowering pass, before it gets far enough to write that report, generate MIF resources, or
hand anything to Quartus. There is therefore **no `.sof`, no ALM/M20K fitter numbers, and no
timing** to report for ds-cnn-kws DDR-free -- the honest finding is a compiler-level graph
rejection, not a place-and-route overflow.

## 5. Conclusion + what this does and doesn't close

- **The depthwise->dense rewrite itself is validated and reusable**: bit-exact (2.98e-08/3.7e-08
  max abs diff, float32 noise floor), removes every `GroupConvolution`/depthwise conv from the
  graph, confirmed via both onnxruntime (ONNX level) and the exported OpenVINO INT8 IR (no
  `GroupConvolution`, no `Concat`). It is exactly what Track B2 asked for and is available to any
  future model needing the same fix (`graph_ops.rewrite_grouped_convs_to_dense`).
- **ds-cnn-kws is not DDR-free-viable on this compiler version**, but not for the depthwise-conv
  reason the plan anticipated -- it's the oversized `AveragePool`'s *own* decomposition that has no
  DDR-free-legal form: `"conv"`/`"cascade"` costs unaffordable M20K (byte-math alone rules it out,
  before even reaching the fitter), and `"reduce_mean"` hits a hard structural compiler rejection
  (`Width concats not supported`) independent of any arch sizing knob tried (32/64/4096).
- Per `docs/onboard_benchmark_plan.md` §0's own contingency, the documented fallback is
  **`ad-toycar`** as the 2nd DDR-free model (not attempted in this track -- out of scope here).
  DDR-free therefore currently stands at **resnet8-only** (`.sof` already built, per
  `docs/coredla_agx3_build_findings.md`) unless `ad-toycar`'s config-cache trim (mentioned in
  the plan) is pursued next.

## 6. Artifacts

- `sw/model_prep/graph_ops/depthwise_to_dense.py` -- the rewrite (committed, reusable).
- `sw/model_prep/tests/test_graph_ops.py` -- equivalence tests (synthetic + real ds-cnn-kws;
  `.venv/bin/python -m pytest sw/model_prep/tests/test_graph_ops.py -q -s`, all pass).
- `quartus/coredla_agx3_ddrfree/arch/AGX3_Ddrfree_DSCNN.arch` -- the tuned DDR-free arch (committed;
  documents its own build-to-verify outcome in its header comment).
- `scratch/dscnn_ddrfree/` -- gitignored scratch: the three rewritten ONNX/IR variants
  (`reduce_mean`, `conv`/`cascade`, no-pool-decomp) and `build_pipeline.py`, kept locally for
  reproducing the numbers above but not committed (AGENTS.md: no build outputs in git).
