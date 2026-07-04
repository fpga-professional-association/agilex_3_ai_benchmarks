"""Put sw/model_prep/ on sys.path so tests can import common, models, fetch_models, etc."""

import sys
from pathlib import Path

MODEL_PREP_DIR = Path(__file__).resolve().parent.parent
if str(MODEL_PREP_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_PREP_DIR))
