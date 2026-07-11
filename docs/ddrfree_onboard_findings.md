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

## The blocker: compute never engages (cross-platform signature)

On silicon the 32-B output is **byte-identical across ≥6 different inputs, across two bitstreams
whose config ROMs differ in ~100 words (`--ffolding-option` 1 vs 0; weights byte-identical), and
across weight-corrupting reconfig**: the output streamer drains a buffer that compute never
writes. `FILTER/FEATURE/OUTPUT` CSR counters read 0 after "completed" inference. This is the
same signature as the HyperRAM platform's hang ("0 filter reads / 0 input reads / 0 output
writes") — which was attributed to HyperRAM write corruption; with the DDR-free platform showing
it on **pristine ROM-baked parameters**, the common suspect is the AGX3-adapted CoreDLA arch/RTL
itself (both arches hand-adapted from AGX7 templates, FP12AGX tensor-FP DSP config; AGX3 support
in 2026.1.1 is new and the vendor's own AGX3 ED may be INT8-only-validated).

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
