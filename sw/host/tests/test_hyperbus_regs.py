"""Prove the host register constants stay in sync with docs/hyperbus.md's #14 addendum (issue #14).

Same convention as test_scoreboard_regs.py against docs/register_map.md, but docs/hyperbus.md has
THREE separate CSR-map tables (hb_trainer / l3_memtest_engine / l3_bw_engine) that reuse names like
CTRL/STATUS/BASE_ADDR across tables, so each table is parsed and checked independently rather than
folded into one shared name->offset dict.
"""

import re
from pathlib import Path

import hyperbus as hb

HYPERBUS_MD = Path(__file__).resolve().parents[3] / "docs" / "hyperbus.md"

# Maps each doc section heading to the host REG dict it must match.
SECTION_TO_REG = {
    "`hb_trainer`": hb.TRAINER_REG,
    "`l3_memtest_engine`": hb.MEMTEST_REG,
    "`l3_bw_engine`": hb.BW_REG,
}

ROW_RE = re.compile(r"^\|\s*(0x[0-9A-Fa-f]+)\s*\|\s*([A-Z0-9_]+)\s*\|")
HEADING_RE = re.compile(r"^### CSR map \((.+)\)\s*$")


def _parse_sections() -> dict[str, dict[str, int]]:
    """Return {heading_paren_text: {NAME: offset}} for every '### CSR map (...)' section."""
    sections: dict[str, dict[str, int]] = {}
    current: dict[str, int] | None = None
    for line in HYPERBUS_MD.read_text().splitlines():
        h = HEADING_RE.match(line)
        if h:
            current = {}
            sections[h.group(1)] = current
            continue
        if current is None:
            continue
        m = ROW_RE.match(line)
        if m:
            current[m.group(2)] = int(m.group(1), 16)
    return sections


def test_doc_has_all_three_sections():
    sections = _parse_sections()
    for heading in SECTION_TO_REG:
        assert any(heading in key for key in sections), f"missing doc section for {heading}"


def test_every_doc_register_matches_host_constant():
    sections = _parse_sections()
    for heading, reg in SECTION_TO_REG.items():
        key = next(k for k in sections if heading in k)
        doc = sections[key]
        assert doc, f"failed to parse any registers from the {heading} section"
        for name, off in doc.items():
            assert name in reg, f"{heading}: {name} in the doc but missing from hyperbus.py"
            assert reg[name] == off, (
                f"{heading}: {name} doc 0x{off:02X} != host 0x{reg[name]:02X}")


def test_host_has_no_extra_registers():
    sections = _parse_sections()
    for heading, reg in SECTION_TO_REG.items():
        key = next(k for k in sections if heading in k)
        doc = sections[key]
        for name in reg:
            assert name in doc, f"{heading}: hyperbus.py has {name} not documented in hyperbus.md"
