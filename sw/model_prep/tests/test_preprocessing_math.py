"""Preprocessing-math tests that don't need TensorFlow/librosa/torch (issue #2)."""

from __future__ import annotations

import numpy as np

from models import ad, dscnn


def test_dscnn_spectrogram_length_matches_reference_formula():
    # keras_model.py:prepare_model_settings -- 16kHz/1s clip, 30ms window, 20ms stride.
    desired_samples = 16000
    window_size_samples = 480
    window_stride_samples = 320
    expected = 1 + (desired_samples - window_size_samples) // window_stride_samples
    assert dscnn.SPECTROGRAM_LENGTH == expected == 49


def test_ad_frame_vectors_shape_and_dim():
    # 128 mel bins x 200 frames (the [50:250] central slice) -> (196, 640).
    log_mel = np.arange(128 * 200, dtype=np.float64).reshape(128, 200)
    vectors = ad.frame_vectors(log_mel)
    assert vectors.shape == (196, 640)


def test_ad_frame_vectors_concatenation_is_a_sliding_window():
    # Build a log_mel where column t is filled with value t, so frame_vectors' t-th 128-wide
    # slice at output row r should be all-equal to (r + t).
    n_mels, n_frames_total = 4, 10
    log_mel = np.tile(np.arange(n_frames_total, dtype=np.float64), (n_mels, 1))
    vectors = ad.frame_vectors(log_mel, n_mels=n_mels, frames=5)
    assert vectors.shape == (6, 20)
    for r in range(6):
        for t in range(5):
            assert np.all(vectors[r, n_mels * t: n_mels * (t + 1)] == r + t)


def test_ad_frame_vectors_too_short_returns_empty():
    log_mel = np.zeros((4, 3))  # fewer than FRAMES=5 columns
    vectors = ad.frame_vectors(log_mel, n_mels=4, frames=5)
    assert vectors.shape == (0, 20)
