"""FC autoencoder anomaly detection, ToyCar (MLPerf Tiny / DCASE2020 Task2), issue #2.

Checkpoint: fp32 TFLite export of mlcommons/tiny's ``ad01.h5`` (dense autoencoder
640->128x4->8->128x4->640, Hitachi Ltd., MIT license) — no training here. Dataset: DCASE2020
ToyCar dev set (Zenodo, CC BY-NC-SA 4.0 — non-commercial/share-alike; noted here since results
derived from it inherit that restriction).

Feature extraction and AUC scoring are ported from mlcommons/tiny's
``benchmark/training/anomaly_detection/common.py:file_to_vector_array`` and ``01_test.py``:
128-mel log-power spectrogram (librosa, n_fft=1024, hop=512), central frames [50:250), 5-frame
sliding window -> 640-dim vectors; per-file anomaly score = mean squared reconstruction error;
AUC per machine ID, then averaged across the 4 ToyCar IDs (matches the reference's own metric).
"""

from __future__ import annotations

import glob
import re
import zipfile
from pathlib import Path

import numpy as np

import common

MODEL_ID = "ad-toycar"
EXPECTED_WEIGHT_BYTES = int(267 * 1024)  # PLAN §5

_CHECKPOINT_URL = (
    "https://raw.githubusercontent.com/mlcommons/tiny/master/"
    "benchmark/training/anomaly_detection/trained_models/ad01_fp32.tflite"
)
_SOURCE_COMMIT = "mlcommons/tiny@master"
_DATASET_URL = "https://zenodo.org/record/3678171/files/dev_data_ToyCar.zip?download=1"

# baseline.yaml feature params.
N_MELS = 128
FRAMES = 5
N_FFT = 1024
HOP_LENGTH = 512
POWER = 2.0
INPUT_DIM = N_MELS * FRAMES  # 640


def fetch_checkpoint(dest_dir: Path) -> Path:
    dest = dest_dir / "ad" / "ad01_fp32.tflite"
    common.download(_CHECKPOINT_URL, dest)
    return dest


def fetch_dataset(dest_dir: Path) -> Path:
    """Download+extract the ToyCar dev set into ``dest_dir/toycar/dev_data/ToyCar``."""
    root = dest_dir / "toycar"
    extracted = root / "dev_data" / "ToyCar"
    if not extracted.exists():
        zip_path = root / "dev_data_ToyCar.zip"
        common.download(_DATASET_URL, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(root / "dev_data")
    return extracted


def export_onnx(checkpoint_path: Path, onnx_dir: Path) -> tuple[Path, "common.ModelManifest"]:
    onnx_path = onnx_dir / f"{MODEL_ID}.onnx"
    common.convert_tflite_to_onnx(checkpoint_path, onnx_path, opset=13)
    manifest = common.ModelManifest(
        model_id=MODEL_ID,
        source_url=_CHECKPOINT_URL,
        source_commit=_SOURCE_COMMIT,
        sha256=common.sha256_file(onnx_path),
        param_count=common.param_count_from_onnx(onnx_path),
        input_shape=[1, INPUT_DIM],
        layout="flat",
        preprocessing="128-mel log-power spectrogram, 5-frame window -> 640-dim vector (DCASE2020 baseline)",
        opset=13,
        tool_versions=common.tool_versions("tensorflow", "tf2onnx", "onnx"),
        notes="Dataset (ToyCar, Zenodo) is CC BY-NC-SA 4.0 -- non-commercial, share-alike.",
    )
    return onnx_path, manifest


def frame_vectors(log_mel: np.ndarray, *, n_mels: int = N_MELS, frames: int = FRAMES) -> np.ndarray:
    """5-frame sliding-window concatenation over a (n_mels, T) log-mel array -> (T-frames+1, n_mels*frames).

    Pure numpy, no audio I/O -- the part of ``file_to_vector_array`` (mlcommons/tiny common.py)
    that's unit-testable without librosa/a real .wav file.
    """
    vector_array_size = log_mel.shape[1] - frames + 1
    if vector_array_size < 1:
        return np.empty((0, n_mels * frames))
    vectors = np.zeros((vector_array_size, n_mels * frames))
    for t in range(frames):
        vectors[:, n_mels * t: n_mels * (t + 1)] = log_mel[:, t: t + vector_array_size].T
    return vectors


def _file_to_vector_array(path: str) -> np.ndarray:
    import librosa

    y, sr = librosa.load(path, sr=None, mono=False)
    mel = librosa.feature.melspectrogram(y=y, sr=sr, n_fft=N_FFT, hop_length=HOP_LENGTH,
                                          n_mels=N_MELS, power=POWER)
    log_mel = 20.0 / POWER * np.log10(mel + np.finfo(float).eps)
    log_mel = log_mel[:, 50:250]
    return frame_vectors(log_mel)


def eval_fp32(onnx_path: Path, dataset_dir: Path) -> dict:
    import onnxruntime as ort
    from sklearn import metrics as skmetrics

    toycar_dir = dataset_dir / "toycar" / "dev_data" / "ToyCar"
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    input_name = sess.get_inputs()[0].name

    test_files = sorted(glob.glob(str(toycar_dir / "test" / "*.wav")))
    machine_ids = sorted(set(re.findall(r"id_[0-9][0-9]", " ".join(test_files))))

    aucs = []
    n_files_total = 0
    for id_str in machine_ids:
        normal = sorted(glob.glob(str(toycar_dir / "test" / f"normal_{id_str}*.wav")))
        anomaly = sorted(glob.glob(str(toycar_dir / "test" / f"anomaly_{id_str}*.wav")))
        files = normal + anomaly
        y_true = np.concatenate([np.zeros(len(normal)), np.ones(len(anomaly))])

        y_pred = np.zeros(len(files))
        for i, path in enumerate(files):
            vectors = _file_to_vector_array(path).astype(np.float32)
            recon = sess.run(None, {input_name: vectors})[0]
            errors = np.mean(np.square(vectors - recon), axis=1)
            y_pred[i] = np.mean(errors)

        aucs.append(skmetrics.roc_auc_score(y_true, y_pred))
        n_files_total += len(files)

    auc = float(np.mean(aucs)) if aucs else 0.0
    return {
        "metrics": {"auc": auc, "n_records": n_files_total},
        "notes": (
            f"ToyCar dev-set test files, {len(machine_ids)} machine IDs "
            f"({n_files_total} files total), per-ID AUC averaged (matches 01_test.py's metric). "
            "MLPerf Tiny reference ballpark: AUC >= 0.83; published runs vary ~0.80-0.85 depending "
            "on training-set composition and seed -- this uses the committed reference checkpoint "
            "as-is, no retraining."
        ),
    }
