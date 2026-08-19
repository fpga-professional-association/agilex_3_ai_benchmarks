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

Official binary prerequisites installed for FPGA AI Suite accelerator work:

| Input | Official source | Verified digest and destination |
|---|---|---|
| FPGA AI Suite 2026.1.1 | Altera Quartus Pro 26.1.1 Windows downloads, `fpga_ai_suite-26.1.1.130-windows.exe` | SHA-1 `69A3035A953349C1D81D348B866FB206697A0D66`; SHA-256 `FAAED83A886299243314DFAC5704F183234AB325481819FBEEA2BC4E10704C56`; Authenticode valid, Intel Corporation; installed at `C:\altera_pro\2026.1.1` |
| OpenVINO 2025.4 runtime | `https://storage.openvinotoolkit.org/repositories/openvino/packages/2025.4/windows/openvino_toolkit_windows_2025.4.0.20398.8fdad55727d_x86_64.zip` | Official SHA-256 `995A88DC1E34DF841CC4DB5AB118A87147608C3E4B67F7D9D86BEF1B311A273E`; installed at `C:\altera_pro\openvino_2025.4.0` |

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
- AXC3000 volatile FPGA programming: exact 5.5929446 s, successful with 0
  errors and 0 warnings.
- Successful Nios ELF load/resume: exact 4.6261979 s. Two non-running wrapper
  attempts are documented in `reports/hardware_run.md`.
- AXC3000 target-timed scalar benchmark: 70,811,326,870 ticks at 100 MHz =
  708.11326870 s for 20 timed inferences after 5 warmups.
- FPGA AI Suite-only download: exact 2.3903406 s; unattended installation:
  exact 101.5837781 s.
- OpenVINO archive download: exact 5.3334559 s; extraction and placement:
  exact 1.2207271 s.
- Agilex-3 `dla_compiler --fanalyze-area` sanity check: exact 1.5573449 s.
- Final GPT-5.6 SOL read-only installation/repository audit: exact
  456.3888297 s, verdict GO.

Per-agent token counts are unavailable for every agent; they are never
estimated. No exact Stopwatch/token metric was exposed for the unavailable
entries.
