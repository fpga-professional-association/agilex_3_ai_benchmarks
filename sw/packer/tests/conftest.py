"""Put sw/packer/ on sys.path so tests can import packlib, layouts, pack_records, inspect_recimg."""

import sys
from pathlib import Path

PACKER_DIR = Path(__file__).resolve().parent.parent
if str(PACKER_DIR) not in sys.path:
    sys.path.insert(0, str(PACKER_DIR))
