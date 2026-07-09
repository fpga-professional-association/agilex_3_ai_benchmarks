#!/usr/bin/env python3
"""Read back the result-log region from HyperRAM (issue #21 step 1).

Per docs/record_format.md, the top ``LOG_RESERVE`` bytes of HyperRAM hold one predicted-class byte
per record, written by ``rtl/scoreboard/result_log_writer.sv``. Read with the run still resident --
no reprogram between run and readback, or the log is stale.
"""

from __future__ import annotations

import argparse
from pathlib import Path

HR_BYTES = 16 * 1024 * 1024      # PLAN §1: Winbond W957D8NB, 16 MB
LOG_RESERVE = 64 * 1024          # docs/record_format.md: top 64 KB reserved for the result log


def read_result_log(transport, n_records: int, *, hr_bytes: int = HR_BYTES,
                    log_reserve: int = LOG_RESERVE) -> list[int]:
    """Predicted class per record (1 byte each), from the top ``log_reserve`` bytes of HyperRAM."""
    if n_records < 0:
        raise ValueError("n_records must be non-negative")
    if n_records > log_reserve:
        raise ValueError(f"{n_records} records exceeds the {log_reserve}-byte log reserve")
    log_base = hr_bytes - log_reserve
    return list(transport.read_block(log_base, n_records))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n-records", type=int, required=True)
    ap.add_argument("--out", required=True, help="write raw predicted-class bytes here (1 B/record)")
    ap.add_argument("--csr-master", default="scoreboard")
    ap.add_argument("--mem-master", default="hyperram")
    args = ap.parse_args(argv)

    from transport import SystemConsoleTransport

    transport = SystemConsoleTransport(csr_master=args.csr_master, mem_master=args.mem_master)
    preds = read_result_log(transport, args.n_records)
    Path(args.out).write_bytes(bytes(preds))
    print(f"wrote {args.out} ({len(preds)} predicted-class bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
