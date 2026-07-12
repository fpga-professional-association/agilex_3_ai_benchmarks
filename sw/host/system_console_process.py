"""Low-level subprocess driver for Intel/Altera System Console.

This is the mechanical half of the real JTAG transport: spawn `system-console -cli`, write it Tcl
command lines on stdin, and read back its `%`-prompt-delimited replies on stdout. It is transcribed
from the vendor's own JTAG MMD, which does exactly this over a `boost::process` pipe pair --
`$COREDLA_ROOT/runtime/coredla_device/mmd/system_console/mmd_wrapper.cpp` (`send_command`,
`capture_till_prompt`, `wait_for_prompt`, all char-by-char, delimited on the literal `%` prompt
character system-console's CLI emits). See docs/coredla_inference_driver.md for the citation trail.

This module knows NOTHING about CoreDLA CSR semantics -- `coredla_csr_handshake.SystemConsoleTransport`
layers that on top of `send()`. Splitting the prompt-framing state machine out here means it can be
unit-tested with a fake byte stream (no `system-console` binary, no board): tests/test_system_console_process.py
feeds `wait_for_prompt()` characters directly and checks timeout/EOF/framing behaviour. Only `spawn()`'s
actual `subprocess.Popen(...)` call and the reader thread it starts are the hardware/tool-dependent
seam (pragma no cover) -- exactly the same discipline `coredla_csr_handshake.py`'s `open()`/`_send()`
placeholders already used.
"""

from __future__ import annotations

import queue
import subprocess
import threading
import time

PROMPT_CHAR = "%"


class SystemConsoleError(RuntimeError):
    """system-console did not respond as expected (timeout, EOF, or a Tcl-level `error`)."""


class SystemConsoleProcess:
    """Drives one `system-console -cli` subprocess over stdin/stdout pipes.

    Usage (see `coredla_csr_handshake.SystemConsoleTransport.open()` for the exact sequence this
    repo needs):

        p = SystemConsoleProcess()
        p.spawn()                       # subprocess.Popen(["system-console", "-cli"], ...)
        p.wait_for_prompt()             # consume the initial banner
        p.send("set ::cl(sof) top.sof")
        p.send("source system_console_script.tcl")   # runs claim_*_service etc.
        reply = p.send("master_read_32 $::g_dla_csr_service 0x80000000 1")
        p.close()
    """

    def __init__(self, *, system_console_bin: str = "system-console", timeout_s: float = 80.0):
        # DLA_SYSTEM_CONSOLE_TIMEOUT_MS in mmd_wrapper.cpp is 80000 ms; matched here as the default.
        self.system_console_bin = system_console_bin
        self.timeout_s = timeout_s
        self._proc: subprocess.Popen | None = None
        self._q: "queue.Queue[str | None]" = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._stopped = threading.Event()

    @property
    def is_open(self) -> bool:
        return self._proc is not None

    # -- process lifecycle (hardware/tool seam: needs the real `system-console` binary) ----------
    def spawn(self) -> None:  # pragma: no cover - needs the real system-console binary
        if self._proc is not None:
            raise SystemConsoleError("SystemConsoleProcess already spawned")
        self._proc = subprocess.Popen(
            [self.system_console_bin, "-cli"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
        self._stopped.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def _reader_loop(self) -> None:  # pragma: no cover - real-process plumbing
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while not self._stopped.is_set():
                ch = self._proc.stdout.read(1)
                if ch == "":
                    break
                self._q.put(ch)
        finally:
            self._q.put(None)  # EOF sentinel, mirrors out.fail() in capture_till_prompt

    def close(self) -> None:
        self._stopped.set()
        if self._proc is not None:  # pragma: no cover - real-process plumbing
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None

    # -- prompt framing (pure state machine; unit-tested directly) --------------------------------
    def wait_for_prompt(self, *, timeout_s: float | None = None) -> str:
        """Block until a `%` prompt char is seen (or EOF/timeout). Returns everything read before
        it -- mirrors `mmd_wrapper.cpp`'s `capture_till_prompt`, one char at a time, `%`-delimited.

        Feed characters via `self._q` directly (bypassing `spawn()`) to unit test this without a
        real subprocess -- see tests/test_system_console_process.py.
        """
        budget = timeout_s if timeout_s is not None else self.timeout_s
        deadline = time.monotonic() + budget
        buf: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SystemConsoleError(
                    f"Timeout waiting for system-console prompt after {budget}s; "
                    f"captured so far: {''.join(buf)!r}")
            try:
                ch = self._q.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if ch is None:
                raise SystemConsoleError(f"system-console EOF; captured so far: {''.join(buf)!r}")
            if ch == PROMPT_CHAR:
                return "".join(buf)
            buf.append(ch)

    def send(self, tcl: str, *, timeout_s: float | None = None) -> str:
        """Write one Tcl command line (+ newline), flush, and return its `%`-delimited reply."""
        if self._proc is None or self._proc.stdin is None:
            raise SystemConsoleError("SystemConsoleProcess not spawned; call spawn() first")
        self._write_line(tcl)  # pragma: no cover - real-process plumbing
        return self.wait_for_prompt(timeout_s=timeout_s)  # pragma: no cover - real-process plumbing

    def _write_line(self, tcl: str) -> None:  # pragma: no cover - real-process plumbing
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(tcl + "\n")
        self._proc.stdin.flush()
