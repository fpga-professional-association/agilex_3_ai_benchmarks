"""Board-free tests for the MLPerf Tiny on-board runner (Track C).

The whole point: the full runner control flow (write input -> kick -> read output -> latency /
throughput / accuracy math -> schema-valid results) runs against MockTinyTransport with NO board.
Same pattern as test_run_l2.py (#12): drive run_model end to end through the mock, then assert on
the derived numbers and validate the emitted JSON against results/schema/result.schema.json.
"""

import json
import sys
from pathlib import Path

import pytest

# conftest.py puts sw/host/ on sys.path; add scripts/ for reslib (schema validation), like #12.
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import reslib  # noqa: E402

import run_tiny_benchmark as rt  # noqa: E402
from run_tiny_benchmark import Bundle, MockTinyTransport, Record  # noqa: E402


def _sbyte(v: int) -> int:
    """int8 -> unsigned byte."""
    return v & 0xFF


def _logits(vals: list[int]) -> bytes:
    """Pack a list of signed int8 logits into a bytes output tensor."""
    return bytes(_sbyte(v) for v in vals)


# ----------------------------- pure math -----------------------------
def test_argmax_int8_signed():
    # class 2 has the largest signed logit; class 3 is a large NEGATIVE (0x81 = -127), not the max.
    assert rt.argmax_int8(_logits([1, -5, 40, -127])) == 2


def test_percentile_nearest_rank():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert rt.percentile_nearest_rank(vals, 50) == 5.0     # ceil(0.5*10)=5th
    assert rt.percentile_nearest_rank(vals, 99) == 10.0    # ceil(0.99*10)=10th
    assert rt.percentile_nearest_rank(vals, 0) == 1.0
    assert rt.percentile_nearest_rank(vals, 100) == 10.0


def test_summarize_latency_and_throughput():
    # 3000 cycles @ 300 MHz = 10.0 us; mix so p50/p99/min/max differ.
    cycles = [3000, 6000, 3000, 9000]   # -> 10, 20, 10, 30 us
    s = rt.summarize_latency(cycles, fclk_mhz=300.0)
    assert s["latency_us_min"] == pytest.approx(10.0)
    assert s["latency_us_max"] == pytest.approx(30.0)
    assert s["latency_us_p50"] == pytest.approx(10.0)     # sorted [10,10,20,30], ceil(.5*4)=2nd=10
    assert s["latency_us_p99"] == pytest.approx(30.0)
    # fps = N / sum(seconds) = 4 / (70us) = 4 / 70e-6
    assert s["fps"] == pytest.approx(4 / 70e-6, rel=1e-9)
    assert s["n_records"] == 4


def test_summarize_latency_rejects_nonpositive_cycles():
    with pytest.raises(RuntimeError, match="non-positive"):
        rt.summarize_latency([3000, 0], fclk_mhz=300.0)


def test_accuracy_crosscheck_all_signals():
    records = [
        Record(input_bytes=b"", label=0, cpu_pred=0, ref_output=_logits([9, 0, 0])),
        Record(input_bytes=b"", label=1, cpu_pred=2, ref_output=_logits([0, 5, 0])),  # cpu wrong-ish
        Record(input_bytes=b"", label=2, cpu_pred=2, ref_output=_logits([0, 0, 7])),
    ]
    preds = [0, 1, 2]
    hw_outputs = [_logits([9, 0, 0]), _logits([0, 9, 0]), _logits([0, 0, 7])]
    acc = rt.accuracy_crosscheck(preds, records, hw_outputs)
    # ground truth: 0,1,2 == preds -> 100%
    assert acc["accuracy_top1"] == pytest.approx(1.0)
    # cpu_pred: 0,2,2 vs preds 0,1,2 -> agree on rec0 & rec2 = 2/3
    assert acc["cpu_agreement_pct"] == pytest.approx(100.0 * 2 / 3)
    # ref_output vs hw_output: rec0 & rec2 match, rec1 differs (0x05 vs 0x09) = 2/3
    assert acc["output_match_pct"] == pytest.approx(100.0 * 2 / 3)


def test_accuracy_crosscheck_no_labels_is_none():
    records = [Record(input_bytes=b"", cpu_pred=0), Record(input_bytes=b"", cpu_pred=1)]
    acc = rt.accuracy_crosscheck([0, 1], records, [_logits([1, 0]), _logits([0, 1])])
    assert acc["accuracy_top1"] is None
    assert acc["output_match_pct"] is None
    assert acc["cpu_agreement_pct"] == pytest.approx(100.0)


# ----------------------------- end-to-end through the mock -----------------------------
def _classifier_bundle(model_id="ds-cnn-kws", n=5, out_bytes=4):
    records, queue = [], []
    for i in range(n):
        cls = i % out_bytes
        logits = [0] * out_bytes
        logits[cls] = 60                        # make argmax == cls deterministically
        out = _logits(logits)
        records.append(Record(input_bytes=bytes([i]) * 8, label=cls, cpu_pred=cls, ref_output=out))
        queue.append((out, 3000 + i * 300))     # cycles vary per record
    bundle = Bundle(model_id=model_id, metric="top1", output_bytes=out_bytes, records=records)
    return bundle, queue


def test_run_model_end_to_end_mock():
    bundle, queue = _classifier_bundle(n=5)
    t = MockTinyTransport()
    t.program_run(bundle.records, queue)
    run = rt.run_model(t, bundle, fclk_mhz=300.0, warmup=2)
    assert run["n_run"] == 5
    assert run["preds"] == [0, 1, 2, 3, 0]
    # warmup(2) + 5 timed = 7 inference calls
    assert t.inference_calls == 7
    assert run["latency"]["n_records"] == 5
    assert run["latency"]["latency_us_min"] > 0


def test_warmup_is_free_and_does_not_perturb_timed_set():
    # Input-keyed mock: warmup replays record[0] for free; the timed cycle counts are unchanged.
    bundle, queue = _classifier_bundle(n=3)
    t = MockTinyTransport()
    t.program_run(bundle.records, queue)
    run0 = rt.run_model(t, bundle, fclk_mhz=300.0, warmup=0)
    t2 = MockTinyTransport()
    t2.program_run(bundle.records, queue)
    run5 = rt.run_model(t2, bundle, fclk_mhz=300.0, warmup=5)
    assert run0["cycles"] == run5["cycles"]           # timed set identical regardless of warmup
    assert t2.inference_calls == 5 + 3                 # warmup counted but discarded


def test_latency_result_is_schema_valid():
    bundle, queue = _classifier_bundle(n=6)
    t = MockTinyTransport()
    t.program_run(bundle.records, queue)
    run = rt.run_model(t, bundle, fclk_mhz=300.0, warmup=1)
    res = rt.build_latency_result(run, bundle, fclk_mhz=300.0,
                                  arch_file="models/arch/AGX3_Performance.arch",
                                  date="2026-07-09", sof_path="quartus/x/top.sof")
    errs = reslib.validate_result(res, reslib.make_validator())
    assert errs == [], errs
    assert res["kind"] == "measured" and res["level"] == "L5"
    assert res["metrics"]["latency_us_p50"] > 0
    assert res["metrics"]["fps"] > 0


def test_accuracy_result_is_schema_valid_classifier():
    bundle, queue = _classifier_bundle(n=6)
    t = MockTinyTransport()
    t.program_run(bundle.records, queue)
    run = rt.run_model(t, bundle, fclk_mhz=300.0, warmup=1)
    res = rt.build_accuracy_result(run, bundle, fclk_mhz=300.0,
                                   arch_file="models/arch/AGX3_Performance.arch",
                                   date="2026-07-09")
    errs = reslib.validate_result(res, reslib.make_validator())
    assert errs == [], errs
    assert res["metrics"]["accuracy_top1"] == pytest.approx(1.0)  # constructed to be perfect


def test_accuracy_result_auc_model_inherits_cpu_reference():
    # ad-toycar: metric=auc, no per-record labels -> accuracy_top1 carries the CPU-INT8 AUC.
    out = _logits([3, 0])
    records = [Record(input_bytes=b"\x01" * 8, cpu_pred=0, ref_output=out) for _ in range(4)]
    bundle = Bundle(model_id="ad-toycar", metric="auc", output_bytes=2, records=records,
                    cpu_int8_metric_name="auc", cpu_int8_metric_value=0.845,
                    cpu_int8_results_path="results/ph2_ad-toycar-int8_20260704.json")
    t = MockTinyTransport()
    t.program_run(records, [(out, 5000)] * 4)
    run = rt.run_model(t, bundle, fclk_mhz=300.0, warmup=1)
    res = rt.build_accuracy_result(run, bundle, fclk_mhz=300.0,
                                   arch_file="models/arch/AGX3_Performance.arch", date="2026-07-09")
    errs = reslib.validate_result(res, reslib.make_validator())
    assert errs == [], errs
    assert res["metrics"]["accuracy_top1"] == pytest.approx(0.845)
    assert "AUC" in res["notes"]


def test_measured_missing_fclk_would_fail_schema():
    # Guard that our schema validation actually enforces R1 (fclk mandatory for measured).
    bundle, queue = _classifier_bundle(n=3)
    t = MockTinyTransport()
    t.program_run(bundle.records, queue)
    run = rt.run_model(t, bundle, fclk_mhz=300.0, warmup=0)
    res = rt.build_latency_result(run, bundle, fclk_mhz=300.0,
                                  arch_file="models/arch/AGX3_Performance.arch", date="2026-07-09")
    del res["config"]["fclk_mhz"]
    errs = reslib.validate_result(res, reslib.make_validator())
    assert any("R1" in e for e in errs), errs


# ----------------------------- bundle loading roundtrip -----------------------------
def test_load_bundle_roundtrip(tmp_path):
    recdir = tmp_path / "records"
    recdir.mkdir()
    entries = []
    for i in range(3):
        f = recdir / f"rec_{i:05d}.bin"
        f.write_bytes(bytes([i]) * 8)
        of = recdir / f"ref_{i:05d}.bin"
        of.write_bytes(_logits([0, 0, 9]))
        entries.append({"file": f"records/{f.name}", "label": 2, "cpu_pred": 2,
                        "ref_output_file": f"records/{of.name}"})
    (tmp_path / "reference.json").write_text(json.dumps({
        "model_id": "resnet8-cifar10", "metric": "top1", "output_bytes": 3,
        "input_addr": 0, "output_addr": 0x1000,
        "cpu_int8": {"metric_name": "top1", "value": 0.85,
                     "results_path": "results/ph2_resnet8-cifar10-int8_20260704.json"},
        "records": entries,
    }))
    bundle = rt.load_bundle(tmp_path)
    assert bundle.model_id == "resnet8-cifar10"
    assert len(bundle.records) == 3
    assert bundle.records[0].ref_output == _logits([0, 0, 9])
    assert bundle.output_addr == 0x1000
    assert bundle.cpu_int8_metric_value == 0.85

    # And run that loaded bundle straight through the mock.
    t = MockTinyTransport()
    t.program_run(bundle.records, [(_logits([0, 0, 9]), 4000)] * 3)
    run = rt.run_model(t, bundle, fclk_mhz=250.0, warmup=1)
    assert run["preds"] == [2, 2, 2]


def test_load_bundle_rejects_non_tiny_model(tmp_path):
    (tmp_path / "reference.json").write_text(json.dumps({
        "model_id": "yolov3-tiny", "metric": "informational", "output_bytes": 4, "records": []}))
    with pytest.raises(ValueError, match="MLPerf Tiny four"):
        rt.load_bundle(tmp_path)


def test_real_transport_is_board_only():
    # The only board-touching object must refuse to construct off-board (never fakes numbers).
    with pytest.raises(NotImplementedError, match="board bring-up"):
        rt.CoreDlaCsrTransport("top.sof")
