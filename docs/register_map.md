# Scoreboard register map (Avalon-MM, JTAG-visible)

Canonical definition for `rtl/scoreboard/`. Word-addressed offsets in bytes; all registers 32-bit.
Source: plan §6. Implemented by `rtl/scoreboard/scoreboard.sv` (issue #15).

| Addr | Register | Access | Function |
|---|---|---|---|
| 0x00 | CTRL | RW | bit0 START (self-clearing) · bit1 LOOP_EN · bit2 SOFT_RESET (self-clearing) |
| 0x04 | N_RECORDS | RW | records per pass |
| 0x08 | REC_STRIDE | RW | record stride in bytes (64-B multiple) |
| 0x0C | REC_BASE | RW | HyperRAM base address of record store |
| 0x10 | CYCLES_LO | RO | low 32 bits of the 64-bit cycle span; **reading this latches the atomic snapshot** |
| 0x14 | CYCLES_HI | RO | high 32 bits (from snapshot) |
| 0x18 | DONE_COUNT | RO | inferences retired (from snapshot) |
| 0x1C | PASS_COUNT | RO | count of (argmax == golden label) (from snapshot) |
| 0x20 | LAT_MIN | RO | per-inference cycle minimum (from snapshot) |
| 0x24 | LAT_MAX | RO | per-inference cycle maximum (from snapshot) |
| 0x28 | STATUS | RO | bit0 RUNNING · bit1 DONE · bit2 ISSUE_FIFO_OVF · bit3 TS_FIFO_UNDERFLOW · bit4 CLEARING |
| 0x2C | HIST_SHIFT | RW | latency-histogram bucket width = 2^HIST_SHIFT cycles |
| 0x30 | HIST_ADDR | RW | histogram bucket index to read (0..63) |
| 0x34 | HIST_DATA | RO | histogram count at HIST_ADDR |
| 0x38 | LOG_BASE | RW | HyperRAM base for the optional per-record result log (result_log_writer) |

**Histogram access — windowed, not a 64-word block.** The plan sketched `HIST[64]` as a block at
0x28, but 64 consecutive words (0x28–0x124) would overlap `HIST_SHIFT` at 0x2C. It is instead
exposed as a `HIST_ADDR`/`HIST_DATA` window: write the bucket index to 0x30, read the count from
0x34. The host reads all 64 buckets in a loop and reconstructs p50/p99 (the M20K-backed histogram is
one true dual-port memory internally; the window is just its read port on the CSR bus).

Derived results (host side, never computed in hardware):

```
CYCLES_64 = (CYCLES_HI << 32) | CYCLES_LO      # span: first-issue → last-retire, in fabric cycles
FPS       = DONE_COUNT * f_clk / CYCLES_64
accuracy  = PASS_COUNT / DONE_COUNT
```

Rules (all enforced by the RTL + its testbench):

- **Snapshot semantics.** Reading CYCLES_LO (0x10) atomically latches CYCLES_64, DONE_COUNT,
  PASS_COUNT, LAT_MIN, LAT_MAX into shadow registers; reads of 0x14/0x18/0x1C/0x20/0x24 return that
  coherent snapshot. Counters remain readable while a run is in progress.
- **SOFT_RESET and START** both clear all counters and the histogram; **neither touches the
  configuration registers** (N_RECORDS, REC_STRIDE, REC_BASE, HIST_SHIFT, LOG_BASE). START also arms
  a fresh measurement window.
- **Clock domains.** The engine retires inferences in the hot (PE-array) domain; per-inference
  latency and the cycle-span window are measured there against a hot free-running counter, then each
  completed measurement crosses to the cool (CSR) domain through one async FIFO. `CYCLES_64`'s
  `f_clk` is therefore the **hot-domain** fabric clock (PLAN §3 LV4 domain split).
- **64-bit cycle counter is mandatory:** at 300 MHz a 32-bit counter wraps in 14.3 s.
- **argmax tie-break:** lowest class index wins. The result-log / parity gate (#21) must use the
  same rule so hardware and OpenVINO agree bit-for-bit.

## Hot-domain result interface (to the engine / replay datapath, issue #16)

Per retired inference the engine presents, in the hot clock domain: `res_valid`, the golden `label`,
and either a class index (`RESULT_MODE=INDEX`) or a `NUM_CLASSES` logit vector
(`RESULT_MODE=LOGITS`, argmax done inside the scoreboard front-end). A separate `issue_valid` strobe
marks each record handed to the engine; issue/retire are paired in order (in-order retirement
assumed — batch=1 sequential overlay) through a `MAX_INFLIGHT`-deep timestamp FIFO to produce
per-inference latency.
