"""Put sw/host/ on sys.path so tests can import transport, scoreboard, latency, etc."""

import sys
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parent.parent
if str(HOST_DIR) not in sys.path:
    sys.path.insert(0, str(HOST_DIR))
