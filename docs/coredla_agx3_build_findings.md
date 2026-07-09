# Track A — DDR-free AGX3 CoreDLA on the AXC3000: build findings

**Question:** Can we stand up a **DDR-free** Agilex 3 CoreDLA example design for the AXC3000
(device `A3CY100BM16AE7S`), sidestepping HyperRAM by keeping the tiny model weights resident
on-chip? Get the *real* answer, board-free, as far as Quartus will take it.

**Bottom line:**
1. **No AGX3 DDR-free example design or arch ships** in FPGA AI Suite 2026.1.1 — DDR-free is a
   supported, packaged flow only for **AGX5** (`agx5e_modular_ddrfree`) and **AGX7**
   (`agx7_iseries_ddrfree`). The only two AGX3 platforms shipped, `agx3c_jtag` and
   `agx3_soc_m2m`, are **both DDR-backed** (LPDDR4). So the memory note ("AGX3 had no ddrfree
   example") is correct for the *packaged* designs.
2. **But DDR-free is NOT family-gated in the compiler/IP generator.** A hand-authored
   `family:'AGX3'` DDR-free arch is fully accepted: `dla_compiler` compiles it and
   **`dla_create_ip` generates a complete DDR-free CoreDLA IP** (304 RTL files + on-chip weight
   MIF ROMs) for a real TinyML model (resnet8-cifar10 INT8).
3. That IP **compiles all the way through Quartus 26.1 for the AXC3000 device**
   `A3CY100BM16AE7S`. The Performance-class config overflows the small C100 (M20K 431/262 = 164 %,
   ALM 104 %), but a **trimmed `AGX3_Ddrfree_Fit` config FITS and fully compiles with 0 errors**
   (synth + fit + STA + assembler): **ALM 29,888/34,000 (88 %), M20K 247/262 (94 %),
   DSP 63/276 (23 %)**, DLA datapath **closing timing at ~343 MHz in DSP tensor mode**.

The DDR-free path is therefore **real and viable for AGX3** and needs no HyperRAM — a right-sized
DLA fits the AXC3000 die today. The remaining gaps are (a) the model-vs-device M20K budget is
tight (the C100's 262 M20K is the binding wall), and (b) there is **no AGX3 DDR-free platform
wrapper** to turn the QoR-fitting IP into a board-programmable, host-drivable design.

---

## 1. Is DDR-free supported for AGX3? (Step 1)

`dla_build_example_design.py list` (FPGA AI Suite 2026.1.1):

| Example design | Family | Memory |
|---|---|---|
| `agx3_soc_m2m` | Agilex 3 | **DDR** (HPS SoC, m2m) |
| `agx3c_jtag` | Agilex 3 | **DDR** — 2GB LPDDR4, JTAG data plane |
| `agx5e_modular_ddrfree` | Agilex 5 | **DDR-free** |
| `agx7_iseries_ddrfree` | Agilex 7 | **DDR-free** |

- The `agx3c_jtag` README confirms: *"relies on a JTAG-Avalon IP to allow an x86 host to access
  the DDR memory"*, *"uses the 2GB 2133 Mb/s x32 LPDDR4 interface"*, device `A3CY135BM16AE6S`
  (the C-series **135** dev-kit part, not the AXC3000's **100**). Not DDR-free, wrong device.
- DDR-free **arch** files ship only for AGX7 (`AGX7_Streaming_ocp_Ddrfree*.arch`). None for AGX3.
- The DDR-free mechanism is a set of **family-agnostic arch keys**, not a family:
  `enable_on_chip_parameters`, `disable_external_memory`, `input_stream_interface` /
  `output_stream_interface`, `layout_transform_params`, `config_network.config_cache_depth`.
  The config-network RTL `dla_ddrfree_config_data_read.sv` is generic.
- `compile_ip.sh` (the standalone IP QoR harness) explicitly accepts `-f AGX3`.

**Conclusion:** DDR-free is not *packaged* for AGX3, but the compiler and IP generator treat it as
a per-arch capability that works for `family:'AGX3'`. We proved it by building one.

## 2. Generating the AGX3 DDR-free IP + compiling a model (Step 2)

Authored `quartus/coredla_agx3_ddrfree/arch/AGX3_Ddrfree.arch` = AGX3_Performance datapath
(k16/c16, FP12AGX, softmax/sigmoid/prelu, `enable_scale`, `enable_round_clamp`) + the DDR-free
keys + a layout transform sized for a 3x32x32 input.

Errors hit and fixed along the way (each was a concrete arch/flag issue, **never a "not
supported for AGX3"**):

| Symptom | Fix |
|---|---|
| `DDR-free filters require (1062) filter cache depth ... filter_depth is (512)` | bump `filter_scratchpad.filter_depth` |
| `map::at` at AOT export (both NC autoencoder and NCHW conv models) | root cause = input node had no on-FPGA home; add `layout_transform_params` |
| `Quantized graphs with FakeQuantize require Scale enabled and Round Clamp activation` | add `pe_array.enable_scale` + `activation.enable_round_clamp` |
| `HETERO plugin attempted to fallback unsupported FPGA node (input_1, Parameter)` | same layout-transform fix (streaming input consumed on-FPGA) |
| `Softmax custom kernel is not enabled` (resnet8 ends in softmax; FPGA-only plugin) | add the `custom_aux_primitive` softmax block |

Winning command (weights baked into on-chip ROMs from the graph-ops-rewritten resnet8 INT8 IR):

```
dla_create_ip --arch arch/AGX3_Ddrfree.arch \
  --model models/scratch/ir/resnet8_nchw/int8/resnet8-cifar10.xml \
  --ip-dir coredla_ip --skip-sim-env --overwrite
# -> Generated architecture path .../verilog/AGX3_Ddrfree_AGX3
# -> 304 .sv/.v files, 51 .mif (incl. ddrfree_filter_hw_*.mif / ddrfree_bias_scale_hw_*.mif)
```

`dla_compiler --fanalyze-area` on the arch (DSP mode = **"Tensor FP with ChainOut"**, i.e. tensor
mode, not classic): ALMs 48,518 / ALUTs 66,590 / **DSPs 42** / Registers 111,295 / **M20Ks 291**
(area-model estimate; full report in `docs/coredla_agx3_ddrfree_area_estimate.txt`).

**Note (generator quirk):** the generated top wrapper declares `localparam string DEVICE = "AGX5"`
for an AGX3-family IP, and Quartus runs "Agilex5 protocol IP" rule checks — AGX3 (early-access)
reuses the AGX5 DSP/primitive path. Synthesis for the AGX3 device still succeeds.

## 3. Quartus build for the AXC3000 (Step 3)

`quartus/coredla_agx3_ddrfree/scripts/build.sh` is a single-command, board-free reproducible
build: `dla_create_ip` -> instantiate in Altera's `dla_top_quartus_wrapper` area/fmax harness ->
`quartus_sh --flow compile`, targeting `A3CY100BM16AE7S`. Heavy outputs (`coredla_ip/`, `build/`)
are gitignored; only `arch/`, `scripts/`, `docs/` are committed.

- **Analysis & Synthesis: SUCCESS** — 0 errors, device `A3CY100BM16AE7S`, Family "Agilex 3".
  (`docs/coredla_agx3_ddrfree_synth_summary.txt`)
- **Synth logic-utilization estimate: 35,377 / 34,000 ALMs = 104 %**, 86,285 registers,
  95 DSP blocks post-merging. The C100 has only ~34k ALMs → the Performance-class DDR-free DLA
  is marginally over the smallest AGX3 part.
- **Fitter (full compile) verdict: does NOT fit the C100 — and the binding wall is M20K, not
  ALM.** `Error (170019): Project requires 431 M20K RAM blocks, but the selected device can
  contain only 262 M20K RAM blocks.` (Plus the 104% ALM estimate.) This is the *core DDR-free
  tradeoff made concrete*: moving the weights + config-network + stream FIFOs on-chip trades
  DRAM for **431 M20K vs the C100's 262** (164%). The resnet8 weights themselves are tiny
  (~78 KB); the M20K is dominated by the DLA's on-chip scratchpads, the config cache (which was
  over-provisioned at `config_cache_depth 19201` vs the model's 2817 — a padding waste Quartus
  flagged), and the 128-bit stream FIFOs.

### Trimmed variant to fit the C100
`arch/AGX3_Ddrfree_Fit.arch` keeps the DDR-free machinery + softmax but drops sigmoid / prelu /
eltwise-mult and right-sizes the on-chip memories. Build it with
`ARCH_NAME=AGX3_Ddrfree_Fit scripts/build.sh`. Its ALM estimate is **28,899 / 34,000 = 85 %**
(DSP 63) — the ALM overflow is solved. The M20K wall came down in steps as the on-chip memories
were right-sized:

| Config | M20K required | vs C100 (262) |
|---|---|---|
| `AGX3_Ddrfree` (Performance-class) | **431** | 164 % — overflow |
| `AGX3_Ddrfree_Fit`, `config_cache 3201`, `filter_depth 512` | **275** | 105 % — 13 over |
| `AGX3_Ddrfree_Fit`, + `stream_buffer 8192->4096` | **247** | **94 % — FITS** |

Takeaway: the C100's **262 M20K blocks are the hard ceiling** for a DDR-free DLA — the on-chip
weight/config/stream memories, not the logic, are what bind.

**The `AGX3_Ddrfree_Fit` config (stream_buffer 4096) FITS and fully compiles on the AXC3000
`A3CY100BM16AE7S`, 0 errors through the Assembler:**

| Resource | Used | Device | % |
|---|---|---|---|
| ALM logic | 29,888 | 34,000 | 88 % |
| M20K (RAM blocks) | 247 | 262 | 94 % |
| Block memory bits | 4,021,962 | 5,365,760 | 75 % |
| DSP blocks | 63 | 276 | 23 % |
| Registers | 87,288 | — | — |

**Timing closes** (Slow 0C corner, real restricted Fmax — the SDC's 1 GHz "probe" clocks are a
best-fmax measurement trick, so the raw "-3.8 ns VIOLATED" slack is expected and just encodes
the Fmax below):

| Clock | Fmax | Restricted Fmax |
|---|---|---|
| `clk_dla` (DLA datapath) | 384.76 MHz | **342.94 MHz** |
| `clk_ddr` | 453.31 MHz | 352.98 MHz |
| `clk_pcie` | 653.59 MHz | 352.98 MHz |
| `clk_axi` (CSR) | **207.21 MHz** | 207.21 MHz |

The DLA datapath closes at **~343 MHz in DSP tensor mode** on the AXC3000 device — note this is
the `dla_compiler`-generated CoreDLA IP, and it directly contrasts with the ~50 MHz classic-mode
ceiling seen for hand-written custom tensor RTL (L1 #11): the vendor IP flow gets tensor mode +
high Fmax that the custom-RTL path could not.

**Bitstream (.sof):** the Assembler runs clean (0 errors) but **deliberately emits no `.sof`** —
`Critical Warning (25196/25207): a programming file will not be generated because ... pins
missing pin location / I/O Standard assignments`. That is expected: this is Altera's standalone
**area/fmax harness** (`dla_top_quartus_wrapper`, 5 unconstrained clock/reset ports), whose job is
resource + timing QoR, not a programmable image. A programmable `.sof` needs a real AXC3000
platform top with a pinout — i.e. the missing AGX3 DDR-free platform of Section 4. (Per the task,
the board is off-limits and must not be programmed regardless.)

Fit/STA summaries saved in `docs/coredla_agx3_ddrfree_fit_summary.txt` /
`docs/coredla_agx3_ddrfree_sta_summary.txt`.

## 4. Can it run on the board? (the remaining gap)

Even with a fitting bitstream, **there is no AGX3 DDR-free platform / runtime**. The DDR-free IP
exposes AXI4-Stream in/out (`i_istream_axi_t_*` / `o_ostream_axi_t_*`, 128-bit) + a CSR AXI-Lite,
not a DDR port. To actually run inference you must supply a platform that feeds those streams
(e.g. a hostless JTAG+streaming top like `agx3c_jtag`, or retarget the AGX5/7 `ddrfree_common`
platform to AGX3 + the AXC3000 pinout) and the matching `dla_benchmark` runtime plugin. That is
net-new platform RTL, out of scope here, and the board is owned by another agent (not programmed).

**So:** DDR-free on AGX3 is real and needs no HyperRAM, but shipping it end-to-end on the AXC3000
still needs (a) a fitting arch on the C100 and (b) a hand-built AGX3 DDR-free platform wrapper.
The HyperRAM-backed path (another agent's domain) is *not* the only option, but it is the only one
with a ready-made AGX3 platform today.

## Reproduce
```
cd quartus/coredla_agx3_ddrfree
scripts/build.sh --synth      # fast: IP-gen + analysis&synthesis (proves it builds for the C100)
scripts/build.sh              # full: + fit + timing + asm -> build/output_files/top.sof
ARCH_NAME=AGX3_Ddrfree_Fit scripts/build.sh   # trimmed variant intended to fit the C100
```
