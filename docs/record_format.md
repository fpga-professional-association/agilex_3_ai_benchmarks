# Benchmark record format

Canonical definition for `sw/packer/` and `rtl/replay/`. Source: plan §6.

Each record:

| Offset | Field | Size |
|---|---|---|
| 0x00 | Input tensor, INT8, engine-native layout | N bytes |
| N | Golden label (class index) | 1 byte |
| N+1 … | Pad to 64 B stride (burst-aligned) | — |

- `stride = ceil((N + 1) / 64) * 64`
- Records are packed back-to-back at `REC_BASE`, `REC_STRIDE` apart.
- The packer must apply the **exact input quantization scale/zero-point from the compiled model's IR**
  so hardware sees bit-identical tensors to the OpenVINO CPU-INT8 reference. Any drift here shows up
  as an "FPGA accuracy bug" that isn't one (plan §10 risk register).
- Engine-native layout means whatever the compiled inference IP consumes directly (e.g. channel-minor
  blocked layout from the dla_compiler report) — the DMA does no reformatting.
- Binary image layout: `[record 0][record 1]…[record K-1]`, plus a sidecar JSON manifest
  (`<image>.manifest.json`) recording: model id, IR hash, scale/zero-point, N, stride, record count,
  dataset name/split, and the SHA-256 of the image.
- Last 64 KB of HyperRAM (16 MB − 64 KB upward) is reserved for the result log; the record store
  must not extend into it.

Per-dataset strides and capacities (16 MB − 64 KB log reserve; minus resident weights >400 KB):

| Dataset | Input bytes | Stride | Records that fit |
|---|---|---|---|
| ToyADMOS slices (AD) | 640 | 704 | 23,738 |
| Speech Commands (KWS) | 490 | 512 | 32,640 (full 4,890-record test ×6) |
| CIFAR-10 (ResNet-8) | 3,072 | 3,136 | 5,328 (53% of 10,000) |
| VWW 96² (MobileNetV1-0.25) | 27,648 | 27,712 | 603 |
| 224² RGB (MobileNetV2 / ResNet-18) | 150,528 | 150,592 | 87 / 33 (after weights) |
| 416² (Tiny-YOLOv3) | 519,168 | 519,232 | 15 (after weights) |
