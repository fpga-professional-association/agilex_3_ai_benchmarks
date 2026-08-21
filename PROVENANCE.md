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

FPGA AI Suite's Windows `dla_create_ip` also required Python package
`protobuf==4.21.12`, installed into the task-specific ignored directory
`build/fpga_ai/python_deps` after the user explicitly lifted the installation
constraint. This dependency came from pip rather than an Altera installer, and
the downloaded wheel hash was not retained; the repository therefore does not
claim a fully offline/hash-bound reinstall. Installed-content evidence:
`google/protobuf/__init__.py` SHA-256
`0A0A45D2C69A4A01A3FA184DB4BFD9689CA6E0A21CD13C56954EC05A1950E869`,
METADATA SHA-256
`2F57E3950738F9420416E45178E701AA2EF08907CF323333FE695A56E372F459`,
and RECORD SHA-256
`E28667F3A7ACBAC32FD40B5A1F6B1BE732F2EAE9EA5866377241EE5F8F3A4A08`.

Verified artifacts:

- `third_party/mlcommons_tiny/benchmark/training/image_classification/trained_models/pretrainedResnet_quant.tflite` — SHA-256 `3C002613D1B2475EB51DD78DFB85A546C8AE658DEE71CF6ADE43B022FE205415`.
- `third_party/arrow_refdes_schematic/SCH-TEI0131-01-P001.PDF` — local SHA-256 `A3548E1B1E61498A71791C531D3D9B68844106842BC9E51FAAF5E2BA826873A9`.
- Schematic Git blob hash — `467cf7913bc3fcdafcbba9355421f67e224e48ac`.
- Adapted no-Softmax OpenVINO XML — SHA-256
  `C61E8404AB6A1B034C1D43C14838DE28BAF23D724D19203511ADD3398558CF3B`
  for the programmed run. The tracked clean-build flow removes path-bearing
  conversion metadata and reproducibly emits
  `C104ECDC1E0C1777B0071D51E14119BD6B280CE1B21752FA1F84AAB93DCD4A81`;
  the BIN and compiled on-chip parameter MIFs are identical.
- Adapted OpenVINO BIN — SHA-256
  `519DE0A6211125C394F92FCACF5E6F4664B1DA6C9D2B0280B1BD913A0077364B`.
- Tracked 4x4 Agilex-3 architecture — SHA-256
  `ED9E2FC95E64D1F4DF5472E899DF23FF5D9D8FF0A59AFCFA71A42DE0C0B4C8DC`.
- Tracked compiler-only 8x4 candidate architecture — SHA-256
  `72F721C270DD5E07838182F77DCFD07D4D01A52798841B6DDFDC74116CEE5D16`.
- Prior FPGA AI RTL SOF programmed on 2026-08-19 — SHA-256
  `2A2D6855BA8A711CC3D900EB06CEAC1C6A1F97425CB73E928349913AF4CCB1B0`.
- Prior FPGA AI control/streaming ELF downloaded on 2026-08-19 — SHA-256
  `D5E08A534B2026A66CF3A79DF103235F49CA6A8653F45D1287AE75F4BC053BF1`.
- Clean single-source evaluation SOF programmed on 2026-08-21 — SHA-256
  `459382CBBBB3E8A59421213961DF75DED2F36CBEC0E3944F405D12A450D8423A`.
- Counter-enabled FPGA AI control/streaming ELF downloaded on 2026-08-21 —
  SHA-256
  `2C9DA5763B635724AC5A5D222754061150978EC5168091C274DA61C1A705C894`.
- Prior FPGA AI JTAG-UART capture from 2026-08-19 — SHA-256
  `29CBD4AE043AA0026A3C00CA7130D7AD8CFFA7AE56D0CF2084C85F1AFDF9BBEC`.
- FPGA AI JTAG-UART PATH-repeat capture (2026-08-21) — SHA-256
  `51B0CCD2FFDE77EFE98B3A5D4A70638EB164DCD6C018BFC90B6AEEEB1EDF88EA`.
- Final counter-enabled FPGA AI evaluation capture (2026-08-21) — SHA-256
  `59A68DB1C2A701E7893541F27F3755DFD13C86D57A692D515CF54E0C9AFAA77F`.

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
- The earlier installation audit established that the compiler/toolchain ran,
  but it did not establish the separate CoreDLA hardware feature. The current
  sanitized check confirms that required feature `6AF7_018B` is absent from
  both configured license files. `dla_create_ip` therefore generated its
  evaluation streamer, and the hardware correctness gate is NO-GO.
- Native-Windows Quartus PATH repeat: tool resolution/version 1.2715173 s;
  CoreDLA feature check 4.2616576 s; license-environment import/recheck
  0.9643469 s; JTAG scan 0.1693212 s; SOF programming 5.1400021 s; firmware
  rebuild 23.0358526 s; ELF download 5.1800117 s; UART capture 3.0301871 s.
  Programming succeeded, but class 3, repeated output, and `license=0`
  remained, so this is diagnostic evidence and not a benchmark result.

Per-agent token counts are unavailable for every agent; they are never
estimated. No exact Stopwatch/token metric was exposed for the unavailable
entries.
