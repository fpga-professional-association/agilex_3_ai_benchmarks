"""read_result_log.py against MockTransport -- no board needed (issue #21)."""

from __future__ import annotations

import pytest
from read_result_log import HR_BYTES, LOG_RESERVE, read_result_log
from transport import MockTransport


def test_reads_from_the_top_log_reserve_region():
    t = MockTransport()
    log_base = HR_BYTES - LOG_RESERVE
    preds = bytes([3, 1, 4, 1, 5, 9, 2, 6])
    t.write_block(log_base, preds)

    got = read_result_log(t, len(preds))
    assert got == list(preds)


def test_rejects_more_records_than_the_log_reserve_holds():
    t = MockTransport()
    with pytest.raises(ValueError):
        read_result_log(t, LOG_RESERVE + 1)


def test_rejects_negative_n_records():
    t = MockTransport()
    with pytest.raises(ValueError):
        read_result_log(t, -1)


def test_zero_records_returns_empty():
    t = MockTransport()
    assert read_result_log(t, 0) == []
