# rtl/replay/ — record-replay datapath (issue #16)

Streams records from HyperRAM through the engine and peels golden labels off to the scoreboard
(PLAN §6). `HyperRAM → HBMC+DMA (linear bursts) → M20K ping-pong → engine`, label byte → scoreboard.

| Module | Role |
|---|---|
| `record_framer.sv` | Avalon-MM read **master** over the #13 controller; reads each record's `stride` words in linear bursts and routes them: tensor → ping-pong, label word → label FIFO, pad → discarded. Loop mode; log-reserve overrun guard. |
| `pingpong_buf.sv` | Word-based 2× record buffer (engine never starves at a boundary — seamless switch, no bubble). `CUT_THROUGH=1` swaps it for a streaming FIFO for records larger than M20K (PLAN §6 config-b). |
| `replay_top.sv` | Framer + ping-pong + label FIFO; exposes the Avalon master, engine stream, label stream, and `issue_valid`. |

## Design decisions

- **Direct Avalon-MM master, not mSGDMA.** The #13 HyperBus controller presents a plain Avalon-MM
  burst slave, so a direct master is the least logic and the easiest to close timing; an mSGDMA would
  add a descriptor engine for no benefit at this access pattern (linear bursts, one record at a time).
- **Word-based (16-bit) throughout.** HyperRAM reads are 16-bit and tensor byte counts are even, so a
  record is a whole number of words — no byte straddling between tensor and label. Layout is fixed at
  pack time; the datapath does **no** reformatting (PLAN §6, `docs/record_format.md`).
- **Label decoupled from tensor.** The label word trails the tensor words in memory, so the scoreboard
  pops labels in record order independently of tensor draining (a zero-latency consumer would
  otherwise race the label; a real engine's compute latency hides this).
- **Cut-through flow control.** In `CUT_THROUGH` mode the framer gates each burst on `wr_burst_ok`
  (FIFO room for a burst) so a record larger than the FIFO streams without dropping; the ping-pong
  path leaves `wr_burst_ok` tied high (each record has a dedicated full-size buffer).

## Verification

`sim/replay/run.sh` (Verilator): bit-exact replay of a packer-produced fixture, ordered labels, exact
record count, starvation (zero gaps at boundaries), backpressure, loop wraparound, overrun guard,
cut-through streaming, and an integration sim wiring the **real** #13 controller + device BFM + #15
scoreboard, pushing 100 records with DONE/PASS checked. Fixture: `sim/replay/gen_fixture.py`.
