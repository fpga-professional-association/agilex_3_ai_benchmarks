# Provenance and reproducibility

All external source material is fetched by Git at immutable commits. The
tracked FPGA, firmware, parser, and model-manifest work is a derivative of the
pinned sources below; generated Quartus/Nios output is evidence only. The
working tree intentionally does not commit generated or fetched third-party
content from this task.

| Input | Repository and pin | Destination |
|---|---|---|
| Agilex-3 reference design | `ArrowElectronics/refdes-agilex3` @ `62c062464531a4c1f7cfa9b18b6f73fa4b41f6d3` | `third_party/arrow_refdes` |
| MLPerf Tiny sources | `mlcommons/tiny` @ `4addd0fa08d216e20637637874e084895f289da4` | `third_party/mlcommons_tiny` |
| Board schematic | `ArrowElectronics/Agilex-3` @ `0578b8c6ced7f0f006318e021ee7773608d83465`, sparse path `images/AXC3000/SCH-TEI0131-01-P001.PDF` | `third_party/arrow_refdes_schematic/SCH-TEI0131-01-P001.PDF` |

Verified artifacts:

- `third_party/mlcommons_tiny/benchmark/training/image_classification/trained_models/pretrainedResnet_quant.tflite` — SHA-256 `3C002613D1B2475EB51DD78DFB85A546C8AE658DEE71CF6ADE43B022FE205415`.
- `third_party/arrow_refdes_schematic/SCH-TEI0131-01-P001.PDF` — local SHA-256 `A3548E1B1E61498A71791C531D3D9B68844106842BC9E51FAAF5E2BA826873A9`.
- Schematic Git blob hash — `467cf7913bc3fcdafcbba9355421f67e224e48ac`.

Derivative basis:

- `fpga/axc3000_mlperf` derives from the pinned Arrow checkout's
  `axc3000/first_niosv_refdes`, retaining its Agilex-3 device and pin map while
  removing licensed SLL xSPI/HyperRAM IP and tying HyperRAM inactive at 0 MHz.
- `software/resnet8` and `model/model_manifest.json` derive from the pinned
  MLPerf Tiny ResNet quantized model. The generated model header is reproducible
  from `tools/generate_resnet8_model.py`; the integer path is a regression
  implementation, not a TFLite-runtime oracle.

Timing notes supplied by the task:

- Arrow clone command: approximately 1.8 s (command-runner approximate label).
- Arrow schematic search: exact 11.216268 s.
- ML clone and inspection: measured tool time 12.5 s.
- ML dependency follow-up: exact component timings 1.381872 s + 0.411473 s + 0.157523 s.
- Original Arrow baseline build: blocked by SLL license feature `AE7C_0062` at
  98.6 s.
- Parser initial: 0.130719700 s; parser options: 0.119814500 s; parser audit
  fixes: 0.128757800 s.
- Minimal final Quartus build: 245.051386 s (exit 0).
- Nios host command: 0.245072700 s (PowerShell Stopwatch, including process
  startup).
- Nios BSP/application link: 12.6219946 s.
- SOL phase-1 audit: 332.0539399 s.
- Environment discovery command-runner timings: 1.7 s + 1.4 s + 0.5 s +
  1.0 s; git init: 0.4 s.
- Repository scaffold and inference initial elapsed times: unavailable.
- Pre-program SOL verification: exact 385.4739068 s; controlled SOF + ELF
  bring-up authorized.

Per-agent token counts are unavailable for every agent; they are never
estimated. No exact Stopwatch/token metric was exposed for the unavailable
entries.
