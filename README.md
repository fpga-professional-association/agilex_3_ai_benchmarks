# Agilex-3 MLPerf Tiny demo

This repository contains a reproducible Agilex-3 MLPerf Tiny image-
classification path. Sources are pinned by commit; run `powershell -ExecutionPolicy
Bypass -File scripts/fetch_sources.ps1` on Windows to fetch them. The workflow
is Windows PowerShell based and does not require WSL.

Hardware status: the minimal Quartus build and Nios V/m BSP/application link
succeeded. The derivative has HyperRAM deliberately inactive at exactly **0
MHz**, under the **150 MHz** hard cap. Its 512 KiB on-chip memory contains
234,228 bytes of requested image sections; alignment places the runtime end at
241,092 bytes. Stack and heap share the remaining 283,196-byte linker window.
Final Quartus timing met requirements
(worst setup slack 1.985 ns, worst hold slack 0.035 ns); the 100 MHz Nios clock
closed at 124.77 MHz. The final Quartus build took exactly 245.051386 s and
the Nios BSP/application link took 12.6219946 s.

The original Arrow baseline remains blocked by the unlicensed Synaptic Labs
SLL feature `AE7C_0062` (98.6 s baseline attempt). No hardware was programmed.
Official accuracy remains blocked: permitted CIFAR-10 evaluation data is not
available, and there is no permitted TFLite runtime oracle. Deterministic host
checksums and synthetic-input smoke tests are regression evidence only, not an
accuracy claim.

See [PROVENANCE.md](PROVENANCE.md) for pinned revisions, hashes, and timing
notes, and [TASK_PROFILE.md](TASK_PROFILE.md) for the execution profile.
