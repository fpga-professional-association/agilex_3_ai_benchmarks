# DDR-free resnet8 on the AXC3000: first-silicon findings (2026-07-10/11)

The lost `axc3000_ddrfree/` platform dir was reconstructed (this commit), the resnet8 `.sof`
rebuilt bit-comparable to the lost one (fit: ALM 88%, M20K 224/262=85%, DSP 63; STA clean at
300 MHz kernel / 100 MHz system, kernel setup slack +0.080 ns), and **run on the physical board
for the first time**. Everything works except the one thing that matters: the PE array never
computes. Evidence chain below so nothing is re-derived.

## Verified working on silicon (measured, not assumed)

| Item | Evidence |
|---|---|
| Bitstream programs; CSR + ISSP alive | `design_load`, CSR reads, ISSP reset release (design boots held in reset — `system_source` powers up 0) |
| kernel PLL: 25 MHz XO → ~300 MHz clk_dla | hw_timer counted 292.9 M cycles/s on silicon |
| On-chip memories bit-exact over JTAG | probe writes/readback |
| Full JTAG inference flow | vendor ed0 lib (address map matches our board.qsys exactly); completion counter = 1 per frame |
| Ingress + layout transform + frame geometry | **half-frame probe**: 3072 B → completion stays 0 (waits); +3072 B → completes. Config'd frame = exactly 3072 F16 elements |
| Egress DMA + readback | 32 B output, per-16-byte-beat byte-reversed in memory (decode: reverse each beat, parse FP16 LE, lanes 10–15 = 0xFFFF strobe pad) |

## Input wire format (settled empirically via the bit-accurate emulator)

`dla_benchmark -cm graph.aot -plugins plugins_emulation.xml` with **.bin** inputs (BMPs are
silently ignored → seeded random!) consumes the stream bytes verbatim (`GetStreamingData`).
For resnet8: **6144 B, F16, HWC pixel-interleaved, raw 0–255 values, no pre-byte-reversal**
(hwc → cat@1.0; chw → frog; zeros → airplane@0.49 matching CPU-reference f(zeros)). The FP12AGX
numerics are healthy in emulation. Note: the phase-2 ingress-buffer sizing assumed "3×32×32 INT8
= 3072 B" — the real stream is F16 = 6144 B (the vendor lib's partitioning handles the 4096-B
buffer, and framing is element-count-based — no tlast on the DLA ingress — so splitting is safe).

## The blocker: constant output — corrected analysis (RTL forensics)

On silicon the 32-B output is **byte-identical across ≥6 different inputs, across two bitstreams
whose config ROMs differ in ~100 words (`--ffolding-option` 1 vs 0; weights byte-identical), and
across weight-corrupting reconfig** — while the bit-accurate emulator computes correctly with
the same artifacts.

**Corrections from the RTL deep-dive** (see `scratch/ddrfree_run/probe_debug_network.tcl`
header for citations):

- The `FILTER/FEATURE/OUTPUT` CSR counters are wired **only to the DDR-LSU AXI channels**,
  which `DISABLE_DDR=1` ties to 0 (`dla_dma.sv:440-445,636,704,772,927`) — they are
  **expected-zero and non-diagnostic in DDR-free mode**. An earlier revision of this analysis
  inferred "PE never ran" from them; that inference was invalid.
- The completion counter increments when the **output streamer** finishes its configured frame
  (`dla_dma.sv:427`), not when compute finishes — so "completes with constant output" is
  consistent with the two remaining theories.
- On the (DDR-based) HyperRAM platform those counters ARE diagnostic — that diagnosis stands.
- Tensor-FP DSP config is ruled out: the vendor's own AGX3 archs generate the identical wrapper
  `DEVICE="AGX5"` + FP12AGX primitive selection.
- The vendor ships **no AGX3 ddrfree/streaming arch** (AGX3 examples are DDR-based with
  streaming/OCP off). Our arch grafts `enable_on_chip_parameters + disable_external_memory +
  streaming` onto AGX3 — feature-sufficient (compiles; emulates correctly) but an RTL control
  path never vendor-validated on this family.

**Two live theories**, discriminated by `scratch/ddrfree_run/probe_debug_network.tcl` (dumps
`dla_interface_profiling_counters` over the clk_dla CSR debug ring — a TIMEOUT is itself a
clk_dla/reset verdict): (B1–B3) the on-chip config intercept / config-network clk_ddr→clk_dla
distribution / sequencer never starts the PE (probe: config interfaces idx3-8 = 0), vs (B4)
compute runs and the constant egress is an output-streamer/buffer-address issue (probe: PE→xbar
idx10 > 0 and input-dependent). If B1: file a vendor case (unvalidated AGX3+OCP+streaming
combination) and prefer the vendor-validated DDR-based path.

## Vendor tooling bugs found (reproducible)

1. `dla_build_example_design.py build --on-chip-parameters-dir` is parsed (shown in banner) but
   **not forwarded to dla_create_ip** — MIFs are silently regenerated with default flags.
2. **Online reconfiguration cannot work on multi-bank arches**: `dla_top.sv` hardcodes
   `csr_scratchpad_update_addr_if.data.mem_id = '0`, so the ed0 reconfig flow writes at most one
   of the 9 filter/bias banks (partially) — it corrupts rather than loads. (RTL-confirmed.)
3. `generate_sof.tcl` pipes `quartus_cdb -t dla_adjust_pll.tcl` through `| tee`, masking crashes:
   on this device the PLL retune's `write_atom_netlist` dies with a U2B2 internal error
   (post-STA, pre-asm) — harmless here only because the DB was untouched and asm baked the
   fit-time 300 MHz config.
4. `quartus_pgm` reprogramming under a live SLD session wedges service discovery;
   use system-console `design_load` instead. Fast param swap without recompile:
   overwrite MIFs + `quartus_cdb --update_mif` + `quartus_asm` (~35 s).

## Repro assets

`scratch/ddrfree_run/` (not committed): `run_functional.tcl` (env-driven wrapper; system-console
eats `--args`), patched ed0 lib (buffer-size globals 4096/1024), `img_hwc_f16.bin` (known-good
input), `probe_half.tcl` (frame-geometry probe), `board_session.sh` (self-recovering session),
emulator reference flow. Board access: shared devkit lock (`scripts/devkit_lock.sh`).

## Next

RTL forensics on the LT→sequencer→PE start chain (in flight); compare our adapted arches against
the vendor-shipped `AGX3_Performance_AGX3` (INT8) — rebuilding DDR-free from the vendor AGX3 arch
at INT8 is the most promising fallback if FP12AGX-on-AGX3 is the dead element.
