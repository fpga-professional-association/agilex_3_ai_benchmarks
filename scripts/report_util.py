"""Shared Quartus report-parsing helpers (issue #10, referenced by scripts/README.md).

Pure text parsing over Quartus Prime Pro report files — no Quartus/Docker/hardware needed to run or
test this module (AGENTS.md: pure-logic parts must be testable without the toolchain). Used by
``report_fmax.py`` and by ``sweep_l0b.py`` (and future sweep drivers, e.g. L1's fmax-vs-PE-array
sweep) to turn `.fit.summary` / `.sta.rpt` / `.syn.rpt` text into plain Python values.

Report file naming (Quartus convention, confirmed against quartus/smoke/*): every stage's report is
named ``<revision>.<stage>.<ext>`` inside the project directory (or inside
``PROJECT_OUTPUT_DIRECTORY`` if a project sets one — see scripts/README.md's
"quartus/**/output_files/" convention, used by sweep_l0b.py's generated projects).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


class ReportParseError(RuntimeError):
    """Raised when an expected report file is missing or doesn't match the expected format.

    Callers must fail loudly (scripts/README.md) rather than emit partial/guessed data.
    """


@dataclass(frozen=True)
class FitUtilization:
    """Resource utilization pulled from a Fitter summary (``<revision>.fit.summary``)."""

    alm_used: int
    alm_total: int
    registers: int
    dsp_used: int
    dsp_total: int
    m20k_used: int
    m20k_total: int
    fitter_status: str


@dataclass(frozen=True)
class FmaxRow:
    """One row of the Timing Analyzer's "Fmax Summary" table."""

    clock_name: str
    fmax_mhz: float
    restricted_fmax_mhz: float
    note: str
    corner: str


_FIT_PATTERNS = {
    "fitter_status": re.compile(r"^Fitter Status\s*:\s*(.+)$", re.MULTILINE),
    "alm": re.compile(r"^Logic utilization \(in ALMs\)\s*:\s*([\d,]+)\s*/\s*([\d,]+)", re.MULTILINE),
    "registers": re.compile(r"^Total dedicated logic registers\s*:\s*([\d,]+)", re.MULTILINE),
    "dsp": re.compile(r"^Total DSP Blocks\s*:\s*([\d,]+)\s*/\s*([\d,]+)", re.MULTILINE),
    "m20k": re.compile(r"^Total RAM Blocks\s*:\s*([\d,]+)\s*/\s*([\d,]+)", re.MULTILINE),
}


def _int(s: str) -> int:
    return int(s.replace(",", ""))


def parse_fit_summary(path: Path) -> FitUtilization:
    """Parse a Quartus ``<revision>.fit.summary`` file into a :class:`FitUtilization`.

    Raises :class:`ReportParseError` if the file is missing or a required field can't be found —
    never returns a partially-filled/guessed result.
    """
    path = Path(path)
    if not path.is_file():
        raise ReportParseError(f"fit summary not found: {path}")
    text = path.read_text()

    m_status = _FIT_PATTERNS["fitter_status"].search(text)
    m_alm = _FIT_PATTERNS["alm"].search(text)
    m_reg = _FIT_PATTERNS["registers"].search(text)
    m_dsp = _FIT_PATTERNS["dsp"].search(text)
    m_m20k = _FIT_PATTERNS["m20k"].search(text)

    missing = [
        name
        for name, m in (
            ("Fitter Status", m_status),
            ("Logic utilization (in ALMs)", m_alm),
            ("Total dedicated logic registers", m_reg),
            ("Total DSP Blocks", m_dsp),
            ("Total RAM Blocks", m_m20k),
        )
        if m is None
    ]
    if missing:
        raise ReportParseError(f"{path}: could not find field(s) {missing!r} in fit summary")

    return FitUtilization(
        alm_used=_int(m_alm.group(1)),
        alm_total=_int(m_alm.group(2)),
        registers=_int(m_reg.group(1)),
        dsp_used=_int(m_dsp.group(1)),
        dsp_total=_int(m_dsp.group(2)),
        m20k_used=_int(m_m20k.group(1)),
        m20k_total=_int(m_m20k.group(2)),
        fitter_status=m_status.group(1).strip(),
    )


_FMAX_ROW_RE = re.compile(
    r"^;\s*([\d.]+)\s*MHz\s*;\s*([\d.]+)\s*MHz\s*;\s*(\S+)\s*;\s*(.*?)\s*;\s*(.*?)\s*;\s*$"
)


def parse_fmax_summary(sta_rpt_path: Path) -> list[FmaxRow]:
    """Parse every row of the "Fmax Summary" table out of a full ``<revision>.sta.rpt``.

    The table looks like (fixed-width, ``;``-delimited, from Quartus's report_timing_summary /
    Fmax Summary panel)::

        ; Fmax      ; Restricted Fmax ; Clock Name ; Note                       ; Worst-Case Operating Conditions ;
        ; 581.4 MHz ; 352.98 MHz      ; clk        ; limit due to ...           ; Slow fix6 0C Model               ;

    Raises :class:`ReportParseError` if the file is missing or no Fmax Summary table is found.
    """
    path = Path(sta_rpt_path)
    if not path.is_file():
        raise ReportParseError(f"STA report not found: {path}")
    text = path.read_text()

    marker = text.find("Fmax Summary")
    if marker == -1:
        raise ReportParseError(f"{path}: no 'Fmax Summary' table found (was quartus_sta run?)")

    rows: list[FmaxRow] = []
    for line in text[marker:].splitlines():
        m = _FMAX_ROW_RE.match(line.strip())
        if m:
            rows.append(
                FmaxRow(
                    fmax_mhz=float(m.group(1)),
                    restricted_fmax_mhz=float(m.group(2)),
                    clock_name=m.group(3),
                    note=m.group(4).strip(),
                    corner=m.group(5).strip(),
                )
            )
        elif rows and line.strip().startswith("This panel reports FMAX"):
            break  # end of table (trailing prose paragraph)

    if not rows:
        raise ReportParseError(f"{path}: 'Fmax Summary' marker found but no data rows parsed")
    return rows


def slow_corner_fmax_mhz(
    rows: list[FmaxRow], clock_name: str | None = None, restricted: bool = False
) -> float:
    """Worst-case (minimum) Fmax among rows run at a "Slow" corner.

    PLAN §7 L0b wants "fmax from timing report (slow corner)". Reports the plain **Fmax** column by
    default, not Restricted Fmax — confirmed empirically during issue #10's development that
    Restricted Fmax on this device/speed-grade is pinned to a fixed "limit due to minimum pulse
    width restriction" ceiling (352.98 MHz at the Slow fix6 0C Model corner) that is *identical*
    across completely unrelated designs (a trivial 4-bit counter and this module's soft-MAC array
    both hit exactly 352.98 MHz Restricted Fmax), while plain Fmax differs between them and does
    move with the actual logic (e.g. soft_mac_array's W=4/M=64 point: Fmax 436.11 MHz vs. the
    counter's 581.4 MHz). That ceiling is a clock-network/register property at this corner, not a
    function of the combinational logic being characterized, so it would flatten out exactly the
    per-W density differences this level exists to measure. Pass ``restricted=True`` to get the
    old (clamped) behavior back if ever needed.
    """
    candidates = [
        r for r in rows if r.corner.startswith("Slow") and (clock_name is None or r.clock_name == clock_name)
    ]
    if not candidates:
        raise ReportParseError(
            f"no Slow-corner Fmax row found (clock_name={clock_name!r}) among {rows!r}"
        )
    if restricted:
        return min(r.restricted_fmax_mhz for r in candidates)
    return min(r.fmax_mhz for r in candidates)


def grep_report(path: Path, pattern: str) -> list[str]:
    """Return every line in ``path`` matching ``pattern`` (case-insensitive substring or regex).

    Generic helper used e.g. to confirm (or refute) that a Quartus synthesis report actually
    mentions "Fractal Synthesis" for a given compile (docs/toolchain.md's empirical check).
    """
    path = Path(path)
    if not path.is_file():
        raise ReportParseError(f"report not found: {path}")
    rx = re.compile(pattern, re.IGNORECASE)
    return [line for line in path.read_text().splitlines() if rx.search(line)]
