# rtl/

SystemVerilog sources. Conventions: `AGENTS.md` (reset-less datapath, sync-reset-only architectural
state, CDC only via `rtl/common/`, Avalon-MM CSRs).

| Dir | Contents | Spec |
|---|---|---|
| `common/` | `bench_pkg.sv`, async-FIFO / pulse-sync CDC wrappers, free-running counter | created on first use |
| `scoreboard/` | benchmark scoreboard | `docs/register_map.md` |
| `hyperbus/` | HyperBus controller + PHY, capture training | PLAN §4 |
| `replay/` | mSGDMA glue, ping-pong record buffer, record framer | PLAN §6, `docs/record_format.md` |
| `microbench/l0_tensor_chain/` | tensor-mode DSP dot-product chain | PLAN §7 L0 |
| `microbench/l0b_soft_mac/` | INT4/2/1 ALM multiplier arrays | PLAN §7 L0b |
| `microbench/l1_pe_array/` | parameterized systolic tile for fmax sweeps | PLAN §7 L1 |
| `microbench/l2_m20k_bw/` | banked M20K readers + checksum sinks | PLAN §7 L2 |
