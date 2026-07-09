"""Pure-logic coverage for smoke_infer.py against MockTransport (issue #7) — no board,
system-console, or Quartus needed (AGENTS.md: isolate pure-logic parts behind small functions)."""

import sys
from pathlib import Path

import pytest

import smoke_infer as si

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import reslib  # noqa: E402


def test_argmax_int8_basic():
    # signed INT8: 0x05=5, 0x7F=127, 0x80=-128, 0xFB=-5
    assert si.argmax_int8(bytes([0x05, 0x7F, 0x80, 0xFB])) == 1
    assert si.argmax_int8(bytes([0xFB, 0x05, 0x02])) == 1


def test_argmax_int8_empty_raises():
    with pytest.raises(ValueError):
        si.argmax_int8(b"")


def test_smoke_infer_roundtrip_via_mock():
    t = si.MockTransport(mem_size=4096)
    # program the "output tensor" the mock DDR will hand back after run_inference()
    t.write_ddr(0x1000, bytes([0x01, 0x02, 0x7F, 0x00]))  # argmax -> index 2 (127)

    run = si.smoke_infer(t, input_bytes=b"\x01" * 490, input_addr=0x0,
                          output_addr=0x1000, output_bytes=4)

    assert run["argmax"] == 2
    assert t.inference_calls == 1
    assert t.read_ddr(0x0, 490) == b"\x01" * 490  # input tensor really landed at input_addr


def test_build_result_schema_valid_and_match_flagged():
    run = {"output": bytes([0, 0, 0x7F, 0]), "argmax": 2}
    result = si.build_result(run, model="ds-cnn-kws",
                             arch_file="models/arch/AGX3_Small_NoSoftmax.arch",
                             date="2026-07-04", subject="ds-cnn-kws-hostless-jtag-smoke",
                             fclk_mhz=150.0, expected_argmax=2,
                             tool_versions={"ai_suite": "2026.1.1+b17"})
    schema = reslib.load_schema()
    import jsonschema
    jsonschema.validate(result, schema)

    assert result["kind"] == "measured"
    assert result["level"] == "PH1"
    assert result["config"]["device"] == "A3CY100BM16AE7S"
    assert result["metrics"]["accuracy_top1"] == 1.0
    assert "matches_cpu_int8=True" in result["notes"]


def test_build_result_mismatch_flagged_not_hidden():
    run = {"output": bytes([0, 0x7F, 0, 0]), "argmax": 1}
    result = si.build_result(run, model="ds-cnn-kws",
                             arch_file="models/arch/AGX3_Small_NoSoftmax.arch",
                             date="2026-07-04", subject="ds-cnn-kws-hostless-jtag-smoke",
                             expected_argmax=3)
    assert result["metrics"]["accuracy_top1"] == 0.0
    assert "matches_cpu_int8=False" in result["notes"]


def test_system_console_transport_raises_without_board():
    with pytest.raises(NotImplementedError):
        si.SystemConsoleTransport("top.sof")


def test_main_reports_board_needed(capsys, tmp_path):
    input_path = tmp_path / "in.bin"
    input_path.write_bytes(b"\x00" * 490)
    rc = si.main(["--sof", "top.sof", "--input", str(input_path),
                  "--output-addr", "0x1000", "--output-bytes", "4"])
    assert rc == 3
    assert "needs a board" in capsys.readouterr().err
