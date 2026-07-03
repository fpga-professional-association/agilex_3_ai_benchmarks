# sw/packer/

Dataset → HyperRAM record image. Binary format: `docs/record_format.md` (INT8 tensor + golden label,
64-B stride, quantized with the exact IR scale/zero-point). Emits `<name>.recimg` + sidecar
`.manifest.json`. Pure numpy; unit-testable without OpenVINO via the manifest-driven quantize path.
