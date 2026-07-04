"""Prove the host register constants stay in sync with docs/register_map.md (issue #17)."""

import re
from pathlib import Path

import scoreboard as sb

REG_MAP_MD = Path(__file__).resolve().parents[3] / "docs" / "register_map.md"


def _parse_doc_offsets() -> dict[str, int]:
    """Pull `| 0xNN | NAME | ... |` rows from the register-map table."""
    offsets = {}
    row = re.compile(r"^\|\s*(0x[0-9A-Fa-f]+)\s*\|\s*([A-Z0-9_]+)\s*\|")
    for line in REG_MAP_MD.read_text().splitlines():
        m = row.match(line)
        if m:
            offsets[m.group(2)] = int(m.group(1), 16)
    return offsets


def test_every_doc_register_matches_host_constant():
    doc = _parse_doc_offsets()
    assert doc, "failed to parse any registers from docs/register_map.md"
    for name, off in doc.items():
        assert name in sb.REG, f"{name} in the doc but missing from scoreboard.REG"
        assert sb.REG[name] == off, f"{name}: doc 0x{off:02X} != host 0x{sb.REG[name]:02X}"


def test_host_has_no_extra_registers():
    doc = _parse_doc_offsets()
    for name in sb.REG:
        assert name in doc, f"scoreboard.REG has {name} not documented in register_map.md"
