# sw/host/

Host-side control plane (JTAG System Console — control/readback ONLY, never the timed data path,
PLAN §8). Loads record images into HyperRAM, configures the scoreboard (`docs/register_map.md`),
starts runs, polls DONE_COUNT, reads back counters, and emits `results/` JSON. Includes the
unlicensed-IP-cap check (DONE_COUNT vs intent, PLAN §10) — `run_bench.py`'s `UNLICENSED_CAP` is
currently `10_000` (issue #17, matching PLAN §9 PH1's stated figure at the time). Note found while
working issue #7: `docs/toolchain.md` (issue #1) since verified the live upstream cap is actually
**100,000**, not 10,000 (fetched fresh from `altera-fpga/agilex-ed-ai-suite`'s README). Flagging
here rather than silently patching `run_bench.py` — that's issue #17's already-merged code and a
behavior change belongs in its own PR, not a drive-by inside #7.

`smoke_infer.py` (issue #7) is a separate, smaller tool targeting the FPGA AI Suite hostless-JTAG
example design's own JTAG-Avalon memory map (DDR global-memory window + CoreDLA CSR) — a different
platform than the scoreboard/HyperRAM record-replay path the rest of this directory drives. See its
module docstring and `docs/board_bringup.md`.
