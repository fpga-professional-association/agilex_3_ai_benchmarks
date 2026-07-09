"""Tests for scripts/estimate.py's pure-logic parts (issue #6).

No Docker/Quartus/FPGA AI Suite needed: these exercise report parsing and result-shape helpers
against canned text captured from real ``dla_compiler --fanalyze-performance`` runs (see
``results/reports/ph0_estimator.md`` for how they were produced), per the repo convention that
report-parsing logic must be testable without the real toolchain.
"""

from __future__ import annotations

import json

import pytest

import estimate

# Real perf_0.txt content from `dla_compiler --fanalyze-performance` on ad-toycar/int8 against
# models/arch/AGX3_Performance.arch at 250 MB/s (single FPGA subgraph -- the only shape this
# project's INT8 sweep produces, since quantized graphs can't mix HETERO:FPGA,CPU).
SINGLE_SUBGRAPH_REPORT = """\
----------------------------------------------------------------
Performance Estimator DDR Usage Estimate for: tf2onnx_0
WARNING: Memory Bandwidth Bottleneck Detected.
  - 519.815 MB/s of DDR Bandwidth Required for 852.778 fps
  - Assumed Maximum Available BW Only: 250 MB/s
  Performance is now Memory Bound. Throughput (FPS) has been scaled down to match available bandwidth.
TOTAL DDR SPACE REQUIRED                         =         0.54 MB
      DDR INPUT & OUTPUT BUFFER SIZE             =         0.00 MB
      DDR CONFIG BUFFER SIZE                     =         0.01 MB
      DDR FILTER BUFFER SIZE                     =         0.53 MB
      DDR INTERMEDIATE BUFFER SIZE               =         0.00 MB
NOTE: THIS ESTIMATE ASSUMES 1x I/O BUFFER. THE COREDLA RUNTIME DEFAULTS TO 5
TOTAL DDR TRANSFERS REQUIRED                     =         0.54 MB
      DDR FILTER READS REQUIRED                  =         0.53 MB
      DDR FEATURE READS REQUIRED                 =         0.00 MB
      DDR FEATURE WRITES REQUIRED                =         0.00 MB
NUMBER OF DDR FEATURE READS                      =         1.00
MINIMUM AVERAGE DDR BANDWIDTH REQUIRED           =       519.81 MB/s
ASSUMED DDR BANDWIDTH PER IP INSTANCE            =       250.00 MB/s
----------------------------------------------------------------
Performance Estimator Throughput Breakdown for: tf2onnx_0
Arch: kvec16xcvec16_i12x1_fp12agx_sb8192_xbark16_actk16_poolk4
Number of DLA instances                          =            1
Number of DDR Banks per DLA instance             =         1.00
CoreDLA Target Fmax                              =       350.00 MHz
Batch Size                                       =         1.00
PE-only Conv Throughput No DDR                   =     24613.22 fps
PE-only Conv Throughput                          =       463.45 fps
----------------------------------------------------------------
FINAL THROUGHPUT                                 =       521.58 fps
FINAL THROUGHPUT PER FMAX (CoreDLA)              =         1.49 fps/MHz
"""

# Real merged-heterogeneous-graph tail (fp32, 2 subgraphs -- CPU fallback for one unsupported op).
# Not part of this project's actual INT8 deliverable, but the parser must pick the *last* (merged)
# FINAL THROUGHPUT, not the first per-subgraph one.
HETEROGENEOUS_REPORT = """\
----------------------------------------------------------------
Performance Estimator Throughput Breakdown for: tf2onnx_2
Arch: kvec16xcvec16_i12x1_fp12agx_sb8192_xbark16_actk16_poolk4
PE-only Conv Throughput No DDR                   =   7291666.67 fps
PE-only Conv Throughput                          =     54834.01 fps
----------------------------------------------------------------
FINAL THROUGHPUT                                 =     87558.32 fps
FINAL THROUGHPUT PER FMAX (CoreDLA)              =       250.17 fps/MHz
----------------------------------------------------------------
Performance Estimator DDR Usage Estimate for: A Heterogeneous Graph Performance Estimate
WARNING: Memory Bandwidth Bottleneck Detected.
  - 281.847 MB/s of DDR Bandwidth Required for 0 fps
  - Assumed Maximum Available BW Only: 250 MB/s
TOTAL DDR TRANSFERS REQUIRED                     =         1.28 MB
      DDR FILTER READS REQUIRED                  =         1.21 MB
      DDR FEATURE READS REQUIRED                 =         0.02 MB
      DDR FEATURE WRITES REQUIRED                =         0.03 MB
MINIMUM AVERAGE DDR BANDWIDTH REQUIRED           =       281.85 MB/s
----------------------------------------------------------------
Performance Estimator Throughput Breakdown for: A Heterogeneous Graph Performance Estimate
FINAL THROUGHPUT                                 =       220.07 fps
FINAL THROUGHPUT PER FMAX (CoreDLA)              =         0.63 fps/MHz
----------------------------------------------------------------
"""


def test_parse_single_subgraph_report():
    parsed = estimate.parse_performance_report(SINGLE_SUBGRAPH_REPORT)
    assert parsed["fps"] == 521.58
    assert parsed["ddr_bandwidth_required_mbps"] == 519.81
    assert parsed["ddr_transfers_required_mb"] == 0.54
    assert parsed["ddr_filter_reads_mb"] == 0.53
    assert parsed["ddr_feature_reads_mb"] == 0.0
    assert parsed["ddr_feature_writes_mb"] == 0.0
    assert parsed["memory_bound"] is True


def test_parse_picks_last_final_throughput_in_heterogeneous_report():
    parsed = estimate.parse_performance_report(HETEROGENEOUS_REPORT)
    # Must be the merged 220.07, not the first per-subgraph 87558.32.
    assert parsed["fps"] == 220.07
    assert parsed["ddr_filter_reads_mb"] == 1.21


def test_parse_fails_loudly_on_missing_final_throughput():
    with pytest.raises(estimate.EstimatorError, match="FINAL THROUGHPUT"):
        estimate.parse_performance_report("no headline here, tool output must have changed\n")


def test_parse_is_robust_to_missing_optional_fields():
    # A report with only the headline line -- optional DDR fields should come back None, not crash.
    parsed = estimate.parse_performance_report("FINAL THROUGHPUT                                 =       12.5 fps\n")
    assert parsed["fps"] == 12.5
    assert parsed["ddr_bandwidth_required_mbps"] is None
    assert parsed["memory_bound"] is False
    assert "n_subgraphs" not in parsed


def test_build_notes_flags_weight_restreaming():
    parsed = estimate.parse_performance_report(SINGLE_SUBGRAPH_REPORT)
    notes = estimate.build_notes(parsed, model="ad-toycar", bin_size_mb=0.262, precision="int8")
    assert "re-read factor" in notes
    assert "streamed from external memory" in notes
    assert "Memory-bandwidth bottleneck" in notes


def test_build_notes_flags_activation_spill_vs_plan_constant():
    parsed = {
        "fps": 6.4,
        "ddr_bandwidth_required_mbps": 443.73,
        "ddr_transfers_required_mb": 37.97,
        "ddr_filter_reads_mb": 12.86,
        "ddr_feature_reads_mb": 13.35,
        "ddr_feature_writes_mb": 10.96,
        "memory_bound": True,
    }
    notes = estimate.build_notes(parsed, model="mobilenetv2-1.0-imagenet", bin_size_mb=3.54, precision="int8")
    assert "24.31" in notes  # 13.35 + 10.96
    assert "above it" in notes


def test_default_out_path_naming():
    result = {"subject": "ad-toycar-agx3-performance-estimator-250mbps", "date": "2026-07-04"}
    out = estimate.default_out_path(result)
    assert out.name == "ph0_ad-toycar-agx3-performance-estimator-250mbps_20260704.json"
    assert out.parent == estimate.RESULTS_DIR


def test_sha256_file(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello world")
    import hashlib

    assert estimate.sha256_file(p) == hashlib.sha256(b"hello world").hexdigest()


def test_run_estimator_missing_ir_fails_loudly(tmp_path):
    with pytest.raises(estimate.EstimatorError, match="no int8 IR"):
        estimate.run_estimator("no-such-model-xyz", tmp_path / "fake.arch", 250.0)
