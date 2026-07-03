# sw/model_prep/

Model pipeline (PLAN §9 PH2): fetch/reproduce the MLPerf Tiny four (DS-CNN KWS, ResNet-8 CIFAR-10,
MobileNetV1-0.25 VWW, AD autoencoder) plus the >400 KB set (MobileNetV2, ResNet-18, Tiny-YOLOv3) →
ONNX → OpenVINO IR (2024.6) → NNCF post-training INT8 → accuracy references (fp32 and CPU-INT8).

Outputs land in gitignored `models/{downloads,onnx,ir}/`; accuracy references land in `results/` as
`kind: "reference"` JSON. Every artifact gets a manifest with source URL/commit, hashes, versions.
