"""Unit tests for sw/host/system_console_process.py's prompt-framing state machine.

No `system-console` binary and no board: `wait_for_prompt()` is fed characters directly through the
internal queue, exactly what the real reader thread would push. `send()`'s no-process guard is
tested directly too; the actual `subprocess.Popen` plumbing (`spawn()`, `_write_line()`) is the one
hardware/tool-dependent seam and is not exercised here (see module docstring).
"""

import pytest

from system_console_process import PROMPT_CHAR, SystemConsoleError, SystemConsoleProcess


def _feed(proc: SystemConsoleProcess, text: str, *, eof: bool = False) -> None:
    for ch in text:
        proc._q.put(ch)
    if eof:
        proc._q.put(None)


def test_wait_for_prompt_returns_text_before_prompt_char():
    proc = SystemConsoleProcess()
    _feed(proc, f"hello world{PROMPT_CHAR}")
    assert proc.wait_for_prompt(timeout_s=1.0) == "hello world"


def test_wait_for_prompt_stops_at_first_prompt_only():
    proc = SystemConsoleProcess()
    _feed(proc, f"first{PROMPT_CHAR}second{PROMPT_CHAR}")
    assert proc.wait_for_prompt(timeout_s=1.0) == "first"
    assert proc.wait_for_prompt(timeout_s=1.0) == "second"


def test_wait_for_prompt_raises_on_eof():
    proc = SystemConsoleProcess()
    _feed(proc, "partial output, no prompt", eof=True)
    with pytest.raises(SystemConsoleError, match="EOF"):
        proc.wait_for_prompt(timeout_s=1.0)


def test_wait_for_prompt_raises_on_timeout():
    proc = SystemConsoleProcess()
    _feed(proc, "still waiting, never a prompt char")
    with pytest.raises(SystemConsoleError, match="Timeout"):
        proc.wait_for_prompt(timeout_s=0.2)


def test_send_without_spawn_raises():
    proc = SystemConsoleProcess()
    with pytest.raises(SystemConsoleError, match="not spawned"):
        proc.send("master_read_32 $::g_dla_csr_service 0x80000000 1")


def test_is_open_reflects_spawn_state():
    proc = SystemConsoleProcess()
    assert proc.is_open is False
