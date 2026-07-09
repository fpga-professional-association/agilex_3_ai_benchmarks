"""Shared plumbing for the model-zoo pipeline (issue #2): paths, downloads, manifests, results JSON.

No third-party deps at module level — this must import cleanly with just the standard library
(``requests`` is imported lazily inside ``download()``) so pure-logic tests can run in CI, which
deliberately doesn't install the heavy model_prep stack (AGENTS.md/.github/workflows/ci.yml).
"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = REPO_ROOT / "models"
DOWNLOADS_DIR = MODELS_DIR / "downloads"
ONNX_DIR = MODELS_DIR / "onnx"
DATASETS_DIR = REPO_ROOT / "datasets"
RESULTS_DIR = REPO_ROOT / "results"

CHUNK_BYTES = 1 << 20  # 1 MiB


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, *, sha256: str | None = None, desc: str | None = None) -> Path:
    """Download ``url`` to ``dest`` unless it already exists with the right hash (idempotent).

    Raises ``ValueError`` if ``sha256`` is given and the (existing or freshly downloaded) file
    doesn't match — a corrupt partial download must never silently pass as a cache hit.
    """
    import requests  # local: keeps common.py importable with zero third-party deps (see module docstring)

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if sha256 is None or sha256_file(dest) == sha256:
            return dest
        dest.unlink()  # stale/corrupt cache

    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=CHUNK_BYTES):
                if chunk:
                    fh.write(chunk)
    tmp.rename(dest)

    if sha256 is not None and sha256_file(dest) != sha256:
        got = sha256_file(dest)
        dest.unlink()
        raise ValueError(f"{url}: sha256 mismatch (expected {sha256}, got {got})")
    return dest


def tool_versions(*names: str) -> dict[str, str]:
    """Best-effort version lookup for the named importable packages, plus python itself.

    Missing packages are omitted rather than erroring — callers only ask for what they used.
    """
    versions = {"python": platform.python_version()}
    for name in names:
        try:
            mod = __import__(name)
        except ImportError:
            continue
        versions[name] = getattr(mod, "__version__", "unknown")
    return versions


@dataclass
class ModelManifest:
    """Provenance record for one exported ONNX model (issue #2 deliverable)."""

    model_id: str
    source_url: str
    source_commit: str
    sha256: str
    param_count: int
    input_shape: list[int]
    layout: str
    preprocessing: str
    opset: int
    tool_versions: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "source_url": self.source_url,
            "source_commit": self.source_commit,
            "sha256": self.sha256,
            "param_count": self.param_count,
            "input_shape": self.input_shape,
            "layout": self.layout,
            "preprocessing": self.preprocessing,
            "opset": self.opset,
            "tool_versions": self.tool_versions,
            "notes": self.notes,
        }

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")


def write_result(
    path: Path,
    *,
    kind: str,
    level: str,
    subject: str,
    date: str,
    config: dict[str, Any],
    metrics: dict[str, Any],
    plan_ref: str | None = None,
    notes: str | None = None,
) -> None:
    """Write one ``results/`` JSON conforming to ``result.schema.json`` (PLAN §10)."""
    data: dict[str, Any] = {
        "kind": kind,
        "level": level,
        "subject": subject,
        "date": date,
        "config": config,
        "metrics": metrics,
    }
    if plan_ref is not None:
        data["plan_ref"] = plan_ref
    if notes is not None:
        data["notes"] = notes
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


@dataclass
class ModelSpec:
    """Uniform interface each model module exposes, so the four CLI dispatchers stay generic.

    ``metric`` is one of "top1", "auc", "informational" (the last for Tiny-YOLOv3: no accuracy
    eval in v1 scope, PLAN §9 PH2 step 5). ``expected_weight_bytes`` is the PLAN §5 INT8 weight
    size in bytes (INT8 byte count ≈ param count), used for the ±10% sanity check in export_onnx.py.
    """

    model_id: str
    expected_weight_bytes: int
    metric: str
    fetch_checkpoint: Callable[[Path], Path]
    fetch_dataset: Optional[Callable[[Path], Path]]
    export_onnx: Callable[[Path, Path], tuple]
    eval_fp32: Optional[Callable[[Path, Path], dict]]


def convert_tflite_to_onnx(tflite_path: Path, onnx_path: Path, *, opset: int = 13) -> None:
    """Convert a fp32 TFLite model to ONNX via tf2onnx (used by the MLPerf Tiny four)."""
    import tf2onnx.convert  # local import: keeps common.py importable without tensorflow

    onnx_path.parent.mkdir(parents=True, exist_ok=True)
    tf2onnx.convert.from_tflite(str(tflite_path), opset=opset, output_path=str(onnx_path))


def param_count_from_onnx(onnx_path: Path) -> int:
    """Total element count across all ONNX initializers (weights) — the manifest's param_count."""
    import onnx  # heavy import kept local so common.py stays light for pure-logic tests

    model = onnx.load(str(onnx_path))
    total = 0
    for init in model.graph.initializer:
        n = 1
        for d in init.dims:
            n *= d
        total += n
    return total
