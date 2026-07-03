# sw/host/

Host-side control plane (JTAG System Console — control/readback ONLY, never the timed data path,
PLAN §8). Loads record images into HyperRAM, configures the scoreboard (`docs/register_map.md`),
starts runs, polls DONE_COUNT, reads back counters, and emits `results/` JSON. Includes the
10,000-inference unlicensed-IP-cap check (DONE_COUNT vs intent, PLAN §10).
