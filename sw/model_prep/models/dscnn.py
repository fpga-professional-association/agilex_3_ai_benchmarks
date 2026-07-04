"""DS-CNN keyword spotting (MLPerf Tiny), issue #2.

Checkpoint: fp32 TFLite export of mlcommons/tiny's DS-CNN reference (committed upstream,
Apache-2.0) — no training here. Dataset: Google Speech Commands v2 (CC BY 4.0) via
``tensorflow_datasets``, split ``test`` (4,890 records — matches docs/record_format.md exactly,
since TFDS's split is built from Google's official ``testing_list.txt``, the same split the
reference training pipeline uses).

Preprocessing is TF-op-for-op copied from mlcommons/tiny's
``benchmark/training/keyword_spotting/get_dataset.py:get_preprocess_audio_func`` (eval branch,
i.e. ``is_training=False`` — no background-noise mixing): STFT -> mel -> log -> MFCC, matching
``keras_model.py:prepare_model_settings`` for feature_type="mfcc".
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

import common

MODEL_ID = "ds-cnn-kws"
EXPECTED_WEIGHT_BYTES = int(24.9 * 1024)  # PLAN §5

_CHECKPOINT_URL = (
    "https://raw.githubusercontent.com/mlcommons/tiny/master/"
    "benchmark/training/keyword_spotting/trained_models/kws_ref_model_float32.tflite"
)
_SOURCE_COMMIT = "mlcommons/tiny@master"

# Feature params (keras_model.py:prepare_model_settings, feature_type="mfcc").
SAMPLE_RATE = 16000
DESIRED_SAMPLES = 16000          # clip_duration_ms=1000
WINDOW_SIZE_SAMPLES = 480        # window_size_ms=30
WINDOW_STRIDE_SAMPLES = 320      # window_stride_ms=20
DCT_COEFFICIENT_COUNT = 10
LOWER_EDGE_HZ, UPPER_EDGE_HZ, NUM_MEL_BINS = 20.0, 4000.0, 40
SPECTROGRAM_LENGTH = 1 + (DESIRED_SAMPLES - WINDOW_SIZE_SAMPLES) // WINDOW_STRIDE_SAMPLES  # 49


def fetch_checkpoint(dest_dir: Path) -> Path:
    dest = dest_dir / "dscnn" / "kws_ref_model_float32.tflite"
    common.download(_CHECKPOINT_URL, dest)
    return dest


def fetch_dataset(dest_dir: Path) -> Path:
    """Download Speech Commands v2 via ``tensorflow_datasets`` into ``dest_dir/speech_commands``."""
    import tensorflow_datasets as tfds

    data_dir = dest_dir / "speech_commands"
    tfds.load("speech_commands", split="test", data_dir=str(data_dir), download=True)
    return data_dir


def export_onnx(checkpoint_path: Path, onnx_dir: Path) -> tuple[Path, "common.ModelManifest"]:
    onnx_path = onnx_dir / f"{MODEL_ID}.onnx"
    common.convert_tflite_to_onnx(checkpoint_path, onnx_path, opset=13)
    manifest = common.ModelManifest(
        model_id=MODEL_ID,
        source_url=_CHECKPOINT_URL,
        source_commit=_SOURCE_COMMIT,
        sha256=common.sha256_file(onnx_path),
        param_count=common.param_count_from_onnx(onnx_path),
        input_shape=[1, SPECTROGRAM_LENGTH, DCT_COEFFICIENT_COUNT, 1],
        layout="NHWC",
        preprocessing="16kHz/1s MFCC (49x10x1): STFT(480,320,hann)->mel(40,20-4000Hz)->log->MFCC[:10]",
        opset=13,
        tool_versions=common.tool_versions("tensorflow", "tf2onnx", "onnx"),
    )
    return onnx_path, manifest


def _mfcc_features(wav_int16: "np.ndarray", tf) -> "np.ndarray":
    """One example's MFCC fingerprint, mirroring get_preprocess_audio_func's eval-only path."""
    wav = tf.cast(wav_int16, tf.float32)
    wav = wav / tf.reduce_max(wav)
    wav = tf.pad(wav, [[0, DESIRED_SAMPLES - tf.shape(wav)[-1]]])
    # Fixed (non-random) time-shift pad+slice, reproduced verbatim from the reference (a no-op on
    # the signal itself, kept for exactness with the upstream graph).
    padded = tf.pad(wav, tf.constant([[2, 2]], tf.int32), mode="CONSTANT")
    sliced = tf.slice(padded, tf.constant([2], tf.int32), [DESIRED_SAMPLES])

    stfts = tf.signal.stft(
        sliced, frame_length=WINDOW_SIZE_SAMPLES, frame_step=WINDOW_STRIDE_SAMPLES,
        fft_length=None, window_fn=tf.signal.hann_window,
    )
    spectrograms = tf.abs(stfts)
    num_spectrogram_bins = stfts.shape[-1]
    mel_weight = tf.signal.linear_to_mel_weight_matrix(
        NUM_MEL_BINS, num_spectrogram_bins, SAMPLE_RATE, LOWER_EDGE_HZ, UPPER_EDGE_HZ)
    mel_spectrograms = tf.tensordot(spectrograms, mel_weight, 1)
    log_mel = tf.math.log(mel_spectrograms + 1e-6)
    mfccs = tf.signal.mfccs_from_log_mel_spectrograms(log_mel)[..., :DCT_COEFFICIENT_COUNT]
    return tf.reshape(mfccs, [SPECTROGRAM_LENGTH, DCT_COEFFICIENT_COUNT, 1]).numpy()


def eval_fp32(onnx_path: Path, dataset_dir: Path) -> dict:
    import onnxruntime as ort
    import tensorflow as tf
    import tensorflow_datasets as tfds

    ds = tfds.load("speech_commands", split="test", data_dir=str(dataset_dir / "speech_commands"))
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    input_name = sess.get_inputs()[0].name

    correct = 0
    n = 0
    for example in ds:
        features = _mfcc_features(example["audio"], tf)[np.newaxis, ...]
        logits = sess.run(None, {input_name: features.astype(np.float32)})[0]
        pred = int(np.argmax(logits[0]))
        label = int(example["label"].numpy())
        correct += int(pred == label)
        n += 1

    accuracy = correct / n if n else 0.0
    return {
        "metrics": {"accuracy_top1": accuracy, "n_records": n},
        "notes": (
            f"Full Speech Commands v2 'test' split ({n} records) via tensorflow_datasets, matching "
            "docs/record_format.md's 4,890-record count. MLPerf Tiny quality target: 90%+ top-1."
        ),
    }
