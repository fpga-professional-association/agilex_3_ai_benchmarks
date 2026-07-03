# Scoreboard register map (Avalon-MM, JTAG-visible)

Canonical definition for `rtl/scoreboard/`. Word-addressed offsets in bytes; all registers 32-bit
unless noted. Source: plan §6.

| Addr | Register | Access | Function |
|---|---|---|---|
| 0x00 | CTRL | RW | bit0 START (self-clearing) · bit1 LOOP_EN · bit2 SOFT_RESET |
| 0x04 | N_RECORDS | RW | records per pass |
| 0x08 | REC_STRIDE | RW | record stride in bytes (64-B multiple) |
| 0x0C | REC_BASE | RW | HyperRAM base address of record store |
| 0x10 | CYCLES_64 | RO, 64-bit (0x10 lo / 0x14 hi) | free-running counter latched first-issue → last-retire |
| 0x18 | DONE_COUNT | RO | inferences retired |
| 0x1C | PASS_COUNT | RO | count of (argmax == golden label) |
| 0x20 | LAT_MIN | RO | per-inference cycle minimum |
| 0x24 | LAT_MAX | RO | per-inference cycle maximum |
| 0x28 | HIST[64] | RO | optional latency histogram, one M20K; bucket width via HIST_SHIFT CSR (0x2C) → p50/p99 |

Derived results (host side, never computed in hardware):

```
FPS      = DONE_COUNT * f_clk / CYCLES_64
accuracy = PASS_COUNT / DONE_COUNT
```

Rules:

- Counters must be readable while a run is in progress (snapshot semantics: latch all counters
  atomically when CYCLES_64 low word is read).
- SOFT_RESET clears counters and histogram, not configuration registers.
- The scoreboard lives in the cool clock domain; retire events cross via a small async FIFO.
- 64-bit cycle counter is mandatory: at 300 MHz a 32-bit counter wraps in 14.3 s.
