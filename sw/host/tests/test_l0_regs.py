"""Prove sw/host/l0_regs.py stays in sync with rtl/microbench/l0_tensor_chain/README.md's register
map table and with rtl/common/bench_pkg.sv's L0_ADDR_* localparams (issue #9, same idea as
test_scoreboard_regs.py for issue #15)."""

import re
from pathlib import Path

import l0_regs as l0

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "rtl" / "microbench" / "l0_tensor_chain" / "README.md"
BENCH_PKG = REPO_ROOT / "rtl" / "common" / "bench_pkg.sv"


def _parse_readme_offsets() -> dict[str, int]:
    """Pull `| 0xNN | NAME | ... |` rows from the README's register-map table."""
    offsets = {}
    row = re.compile(r"^\|\s*(0x[0-9A-Fa-f]+)\s*\|\s*([A-Z0-9_]+)\s*\|")
    for line in README.read_text().splitlines():
        m = row.match(line)
        if m:
            offsets[m.group(2)] = int(m.group(1), 16)
    return offsets


def _parse_bench_pkg_offsets() -> dict[str, int]:
    """Pull `localparam logic [7:0] L0_ADDR_NAME = 8'hNN;` lines from bench_pkg.sv."""
    offsets = {}
    pat = re.compile(r"L0_ADDR_(\w+)\s*=\s*8'h([0-9A-Fa-f]+)")
    for line in BENCH_PKG.read_text().splitlines():
        m = pat.search(line)
        if m:
            offsets[m.group(1)] = int(m.group(2), 16)
    return offsets


def test_readme_offsets_match_host_constants():
    doc = _parse_readme_offsets()
    assert doc, "failed to parse any registers from the module README's register-map table"
    for name, off in doc.items():
        assert name in l0.REG, f"{name} in the README but missing from l0_regs.REG"
        assert l0.REG[name] == off, f"{name}: README 0x{off:02X} != host 0x{l0.REG[name]:02X}"


def test_host_has_no_extra_registers_vs_readme():
    doc = _parse_readme_offsets()
    for name in l0.REG:
        assert name in doc, f"l0_regs.REG has {name} not documented in the module README"


def test_bench_pkg_offset_values_match_host_constants():
    """Same offset VALUES as bench_pkg::L0_ADDR_* (RTL uses ADDR-only names like L0_ADDR_DONE; the
    host uses more descriptive dict keys like DONE_COUNT — same naming-style split scoreboard.py
    already has vs. bench_pkg's plain ADDR_DONE, so this checks the multiset of offset values, not
    name-for-name identity)."""
    rtl = _parse_bench_pkg_offsets()
    assert rtl, "failed to parse any L0_ADDR_* localparams from bench_pkg.sv"
    assert sorted(rtl.values()) == sorted(l0.REG.values())


def test_ctrl_status_bit_positions_match_rtl_reuse():
    """l0_tensor_chain.sv reuses bench_pkg's CTRL_START/ST_RUNNING/ST_DONE bit positions as-is
    (README: "CTRL/STATUS bit positions are deliberately kept identical ... and reused as-is")."""
    text = BENCH_PKG.read_text()
    ctrl_start = int(re.search(r"CTRL_START\s*=\s*(\d+)", text).group(1))
    st_running = int(re.search(r"ST_RUNNING\s*=\s*(\d+)", text).group(1))
    st_done = int(re.search(r"ST_DONE\s*=\s*(\d+)", text).group(1))
    assert l0.CTRL_START == ctrl_start
    assert l0.ST_RUNNING == st_running
    assert l0.ST_DONE == st_done
