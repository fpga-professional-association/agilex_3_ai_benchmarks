# Toolchain install & verify (issue #1)

PLAN §9 PH0 calls for Quartus Prime Pro ≥25.3 with Agilex 3 device support and FPGA AI Suite from
the same installer. On this host the toolchain is **not** natively installed — it runs as official
Altera/Intel Docker images, wrapped by `scripts/env.sh`. This doc records exact versions, why
Docker instead of a native installer, image tags, and every quirk a future session would otherwise
have to rediscover.

## Why Docker images, not a native installer

This host is WSL2 (Ubuntu 24.04), not bare-metal Linux or Windows. Altera ships the Quartus Prime
Pro and FPGA AI Suite installers as GUI-driven `.run`/installer-jar bundles that assume either a
real X11 desktop or a fully scripted silent-install path that Altera does not officially document
for WSL2. Rather than fight a GUI installer inside WSL2 (or reverse-engineer an unsupported silent
install), both tools are pulled as Altera's own official Docker images:

- `alterafpga/quartus-pro:26.1-agilex3`
- `alterafpga/fpgaaisuite:2026.1.1-quartus`

These are Altera's own supported distribution channel for this toolchain (published under the
`alterafpga` Docker Hub org), not a third-party repackaging, so this satisfies PLAN §9 PH0 without
an unsupported/unofficial install path. `scripts/env.sh` shadows the real tool names
(`quartus_sh`, `dla_compiler`, ...) with shell functions that transparently `docker run` the right
image, bind-mounting the repo at `/workspace` and mapping the host `$PWD` onto the equivalent
`/workspace`-relative path — so from the caller's side (`scripts/build.sh`, interactive shell use)
it behaves like a native install on `$PATH`.

## Exact versions installed

| Tool | Version | Notes |
| --- | --- | --- |
| Quartus Prime Pro | **26.1.0 Build 110 03/26/2026, SC Pro Edition** | `quartus_sh --version`; satisfies PLAN's `≥25.3` |
| FPGA AI Suite | **2026.1.1+b17** | `dla_compiler --version` (also aliased as `dlac`) |
| Questa | **not available** | Neither Docker image bundles Questa. This is a real gap against AGENTS.md / issue #1 Step 1, which calls for installing "Questa*-Intel FPGA Starter Edition (for device-primitive simulation later)". Device-primitive (DSP/M20K/IO) testbenches called for in `sim/README.md` have no simulator in this environment yet — Verilator cannot simulate those primitives. Not papering over this: it needs a separate resolution (a Questa Docker/container source, or a native Questa install added to the host) before any issue that needs device-primitive simulation can be verified end-to-end. |
| Python | **3.12.3** | Identical on the host `.venv` (`.venv/bin/python3 --version`) and inside the `fpgaaisuite` image (`docker run --rm alterafpga/fpgaaisuite:2026.1.1-quartus python3 --version`) |

## Exact image tags

| Image | Tag | Purpose |
| --- | --- | --- |
| `alterafpga/quartus-pro` | `26.1-agilex3` | Quartus Prime Pro 26.1.0 Build 110, SC Pro Edition, Agilex 3 device support |
| `alterafpga/fpgaaisuite` | `2026.1.1-quartus` | FPGA AI Suite 2026.1.1+b17 (`dla_compiler`/`dlac`, `dla_create_ip`, `dla_benchmark`, `dla_build_example_design.py`) |
| `openvino/ubuntu24_runtime` | `2025.4.0` | **Required by FPGA AI Suite 2026.1.1** — the `fpgaaisuite` image does not bundle OpenVINO at all and needs this exact version. Its `/opt/intel/openvino` has been extracted to `~/openvino_2025.4.0` on the host and is bind-mounted read-only at `/opt/intel/openvino` inside the `fpgaaisuite` container by `scripts/env.sh` (`$AI_SUITE_OPENVINO_DIR`, defaults to `~/openvino_2025.4.0`). |
| `openvino/ubuntu24_dev` | `2026.2.1` | General-purpose OpenVINO dev image for model-prep work (issues #2/#3). Unrelated to the AI-Suite-required 2025.4.0 above — do not conflate the two; the AI Suite will not accept 2026.2.1. |

## Known quirks / gotchas

### (a) libudev/FlexLM `realloc()` heap-corruption crash — the big one

Every invocation of the `alterafpga/quartus-pro:26.1-agilex3` container — regardless of design,
project, or Quartus sub-tool — is vulnerable to a crash during FlexLM license checkout. Mechanism:

1. Quartus's FlexLM licensing code (inside `libsys_cpt.so`) does a VM/hypervisor fingerprinting
   probe as part of license checkout (`strings` on `libsys_cpt.so` shows "Search UDEV for
   QEMU/XEN/PARALLELS/...").
2. That probe `dlopen()`s `libudev.so.1` from the container's own glibc/systemd userland.
3. On this container image, that particular `dlopen()` triggers an `RTLD_DEEPBIND`-vs-TBB-allocator
   mismatch, and the resulting call into libudev's `udev_enumerate_scan_devices()` corrupts the
   heap and aborts the whole process with `realloc(): invalid pointer`.
4. Because license checkout happens implicitly on essentially every Quartus command that touches a
   design, this crash was previously observed killing `quartus_sh --flow compile` **mid-Fitter**
   (Fitter is typically the first stage that does a full checkout), taking down an otherwise
   perfectly good compile.

**Fix:** bind-mount `/dev/null` over the container's `libudev.so.1`:

```
-v /dev/null:/usr/lib/x86_64-linux-gnu/libudev.so.1
```

This makes the `dlopen("libudev.so.1")` fail cleanly (the file exists per the dynamic linker, but
opening it as a shared object fails) instead of crashing — FlexLM just skips the VM-fingerprinting
step it was going to use the library for, and license checkout proceeds normally. This is safe
repo-wide: across the whole Quartus install, only two shared objects reference `libudev` at all —
`libsys_cpt.so` (licensing, the one that needs to keep working) and `libsld_filejni.so` (JTAG/
SignalTap JNI bridge, unused by this repo's headless `synth`/`fit`/`sta`/`asm` flows). `scripts/env.sh`
applies this mount unconditionally in `_envsh_run_quartus` (every `quartus_*`/`qsys-*` function goes
through it), with the mechanism documented inline there.

The `fpgaaisuite` image was spot-checked (`dla_compiler --version` after sourcing `env.sh`) and did
not reproduce this crash; it has not been deep-tested against the Fitter code path specifically
(issue #1 doesn't require an AI-Suite-side Quartus fit), so the mount is applied only to the
`quartus-pro`-image functions for now.

### (b) `fpgaaisuite`'s `setupvars.sh` clobbers `"$@"` when sourced

The AI Suite's own environment-setup script (`/opt/altera/fpga_ai_suite/ubuntu/dla/setupvars.sh`)
contains a bare tcsh-compat line (`set tcsh_msg=...`) left over from a shared csh/bash script
template. Because `_envsh_run_ai_suite` sources both `/opt/intel/openvino/setupvars.sh` and this
script (in that order) rather than running them in a subshell, that `set` statement executes as a
plain bash builtin and clobbers the *sourcing* shell's positional parameters (`"$@"`) as a side
effect. `scripts/env.sh` already works around this: it stashes the real command into an array
(`CMD=("$@")`) **before** sourcing either `setupvars.sh`, then `exec`s from the array afterward.
Nothing else needs to change here — just don't remove that stash if touching
`_envsh_run_ai_suite`.

### (c) `quartus_map` is removed in 26.1

Running `quartus_map` in this image errors immediately:

```
Internal Error: Sub-system: QSYN, File: /quartus/synth/qsyn/qsyn_cmd.cpp, Line: 845
Command quartus_map is no longer supported. Please use quartus_syn.
```

Use `quartus_syn` everywhere in this repo going forward; `scripts/env.sh` only exposes
`quartus_syn` (there is no `quartus_map` shim).

### (d) `qsys-generate` has no dedicated `--version` flag in 26.1

Both `qsys-generate --version` and `qsys-generate -v` error (`Unrecognized switch version` /
`Unrecognized switch v` respectively) — there is no separate qsys-generate version number to
record. Its version is the shared Quartus 26.1.0 Build 110 release, confirmed via
`quartus_sh --version`. Do not invent a distinct qsys-generate version string.

### (e) No Questa in either Docker image

See the versions table above — neither `alterafpga/quartus-pro:26.1-agilex3` nor
`alterafpga/fpgaaisuite:2026.1.1-quartus` bundles Questa. Confirmed by inspecting both images;
there is no `questa`/`vsim`/`qverify` binary in either. This is a real, currently-unresolved gap
against AGENTS.md's requirement that device-primitive (DSP/M20K/IO) testbenches run under Questa
(Verilator cannot simulate device primitives — see `sim/README.md`).

## AI Suite paths (needed by later issues)

Both confirmed present inside the `alterafpga/fpgaaisuite:2026.1.1-quartus` container by listing
them directly (not assumed):

- **Example-design build script** (needed by issue #7):
  `/opt/altera/fpga_ai_suite/ubuntu/dla/bin/dla_build_example_design.py`
  (89068 bytes, executable, dated 2026-05-15 — present and runnable via the `dla_build_example_design.py`
  wrapper function in `scripts/env.sh`).
- **Example architecture files directory** (needed by issue #6):
  `/opt/altera/fpga_ai_suite/ubuntu/dla/example_architectures/`. Agilex-3-relevant files confirmed
  present:
  - `AGX3_Performance.arch`
  - `AGX3_Small_NoSoftmax.arch`
  - `AGX3_Small_Softmax.arch`

  (The directory also holds architectures for A10/AGX5/AGX7/AGX9/S10 and a `README.txt` — not
  reproduced here; only the AGX3 files are relevant to this project's device.)

## Performance estimator invocation (issue #6)

PLAN §9 PH0: the estimator is a mode of `dla_compiler` itself (not a separate binary), read from
`dla_compiler --help` in the installed 2026.1.1 image (not guessed from memory or older-version
docs):

```
dla_compiler --fanalyze-performance \
  --march <path/to/AGX3_*.arch> \
  --network-file <path/to/model.xml> \
  --foutput-format open_vino_hetero \
  --fplugin HETERO:FPGA \
  --o out.aot \
  --dumpdir <scratch-dir> \
  --overwrite-output-files \
  --fdump-performance-report perf.txt \
  --fassumed-memory-bandwidth <MB/s>
```

Confirmed since 25.1 the estimator does take external memory bandwidth as an input, exactly as
PLAN §9 PH0 says: `--fassumed-memory-bandwidth <MB/s>` (help text: *"Sets the available external
memory bandwidth for each DLA IP in MB/s ... Do not set this option for architectures that do not
use external memory"*). `scripts/estimate.py` (issue #6) wraps this invocation.

Three things learned only by actually running it against this project's 7 IR models (`ds-cnn-kws`
first, per issue #6 step 2, then the rest), not documented anywhere in `--help`:

1. **`--network-file` takes the OpenVINO IR `.xml` directly** (with the matching `.bin` alongside
   it) — the `--help` text's own worked example references a `model.yml` (an Open Model Zoo
   downloader convention), which doesn't exist anywhere in this image; the vendor's own
   `example_graphs/*/common_functions.sh` confirms the real call is
   `dla_compiler --network-file ./output/$MODEL.xml --march ...`.
2. **`--fplugin` must be exactly `HETERO:FPGA` (or exactly `HETERO:CPU`) for any INT8/NNCF
   (FakeQuantize) graph** — the default `HETERO:FPGA,CPU` errors immediately with *"Quantized
   graphs only supported through HETERO:FPGA or CPU"*. This matters because it means a quantized
   graph **cannot** fall back an unsupported single op to the CPU host the way an fp32 graph can
   (verified directly: the same graph that fails outright as INT8 under `HETERO:FPGA` compiles fine
   as fp32 under the default `HETERO:FPGA,CPU`, with the unsupported op offloaded to CPU and a
   merged heterogeneous throughput reported instead). Four of this project's seven models hit this
   exact wall — see `results/reports/ph0_estimator.md` for which, and the precise
   compiler-reported reason for each (an oversized global-average-pool window/stride, a
   "Transpose does not precede an FC layer" placement rule, and — for Tiny-YOLOv3 — a bundled
   dynamic-control-flow NMS subgraph that no static-dataflow accelerator, FPGA or otherwise, can
   run).
3. **The performance report lands at `<dumpdir>/<network-name>/reports/perf_0.txt`** (or
   `.../reports/perf-merged_subgraph_estimates.txt` for a heterogeneous multi-subgraph compile),
   not at the `--fdump-performance-report` filename directly — that flag only sets the report's
   *basename* inside the `reports/` subdirectory the tool creates under `--dumpdir`. The number to
   read is the last `FINAL THROUGHPUT = ... fps` line in that file.

`--dumpdir`/`--network-file`/`--march` must all be paths **inside** the Docker bind mount
(`$_ENVSH_ROOT`, i.e. under the repo root) — an absolute host path outside the repo (e.g. `/tmp/...`)
resolves to a location inside the ephemeral container instead and vanishes when `docker run --rm`
exits. `scripts/estimate.py` always resolves paths relative to the repo root for this reason.

## Unlicensed IP generation cap

**Verified against the live public repo**, not taken on faith: fetched
`https://raw.githubusercontent.com/altera-fpga/agilex-ed-ai-suite/main/README.md` directly. Its
current wording (as of this verification) is:

> All examples have a hard limit of 100'000 inference requests. Please refer to the documentation
> on ["--licensed/--unlicensed" IP generation](https://docs.altera.com/r/docs/863373/2026.1.1/fpga-ai-suite-handbook/ip-generation-utility-command-line-options)
> for details about this limitation.

That is **100,000** inference requests (the repo's README uses an apostrophe as a thousands
separator: `100'000`), documented in the **FPGA AI Suite Handbook**'s IP generation utility
command-line-options page (linked above, under the `docs.altera.com/r/docs/863373/2026.1.1/`
tree — same `2026.1.1` doc revision as the installed AI Suite version).

**This differs from PLAN §9 PH1's stated figure.** PLAN §9 PH1 currently reads: "License gate:
unlicensed IP generation is hard-limited to **10,000** inference requests." The live upstream
README says **100,000**, a 10x difference. This was not assumed either way — it was fetched fresh
from the public repo above. Flagging loudly per AGENTS.md rather than silently editing PLAN.md:
**PLAN §9 PH1's "10,000" figure appears stale/incorrect against the current upstream doc and should
be reviewed/updated by a maintainer**; soak-run planning (PLAN §9 PH1) should use the verified
100,000 figure until PLAN.md is explicitly corrected. Licensed IP generation (to lift the cap
entirely) is the same doc page's "--licensed" IP-generation-utility flow; this issue did not need
to exercise it, only locate where it's documented.

## Machine-readable versions

```json
{
  "quartus": {
    "product": "Quartus Prime Pro",
    "version": "26.1.0",
    "build": "110",
    "build_date": "2026-03-26",
    "edition": "SC Pro Edition",
    "device_support": "Agilex 3",
    "image": "alterafpga/quartus-pro:26.1-agilex3"
  },
  "ai_suite": {
    "product": "FPGA AI Suite",
    "version": "2026.1.1+b17",
    "image": "alterafpga/fpgaaisuite:2026.1.1-quartus",
    "openvino_required": "2025.4.0",
    "openvino_image": "openvino/ubuntu24_runtime:2025.4.0"
  },
  "questa": {
    "available": false,
    "reason": "Not bundled in alterafpga/quartus-pro:26.1-agilex3 or alterafpga/fpgaaisuite:2026.1.1-quartus; no separate Questa image installed on this host as of this issue."
  },
  "python": {
    "version": "3.12.3",
    "environments": ["host .venv", "alterafpga/fpgaaisuite:2026.1.1-quartus"]
  }
}
```

## How this was verified

- `source scripts/env.sh && quartus_sh --version` →
  `Version 26.1.0 Build 110 03/26/2026 SC Pro Edition` (≥25.3, satisfied).
- `source scripts/env.sh && dla_compiler --version` → `2026.1.1+b17`.
- `source scripts/env.sh && qsys-generate --version` / `-v` → both error (see quirk (d)); version
  is the shared Quartus 26.1.0 Build 110 release.
- `scripts/build.sh smoke` (== `quartus_sh --flow compile smoke -c smoke` from `quartus/smoke/`,
  through `scripts/env.sh` with the libudev fix in place) → `Quartus Prime Full Compilation was
  successful. 0 errors, 4 warnings`; license line in the log: `Info (24849): Successfully acquired
  license for quartus_agilex3.`; no license errors anywhere in the log.
- `quartus/smoke/smoke.sof` exists after that compile (533644 bytes) — gitignored, not committed.
