"""Shared helpers for reading and validating result JSON files (issue #4).

Every measured/estimated/reference number in this project is one JSON file under ``results/``
conforming to ``results/schema/result.schema.json`` (PLAN §10: "numbers without configs are noise").
This module centralizes schema loading, file discovery, and the cross-field rules the JSON Schema
cannot express, so ``validate_results.py`` and ``make_report.py`` agree on what a valid result is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "results"
SCHEMA_PATH = RESULTS_DIR / "schema" / "result.schema.json"

# Directories under results/ that hold generated artifacts, not result JSONs.
_NON_RESULT_DIRS = {"schema", "reports"}


@dataclass(frozen=True)
class ResultFile:
    """A loaded result JSON plus its on-disk path."""

    path: Path
    data: dict[str, Any]

    @property
    def rel_path(self) -> str:
        return self.path.relative_to(REPO_ROOT).as_posix()


def load_schema(schema_path: Path = SCHEMA_PATH) -> dict[str, Any]:
    with schema_path.open() as fh:
        return json.load(fh)


def iter_result_paths(results_dir: Path = RESULTS_DIR) -> list[Path]:
    """All result JSONs under ``results/``, excluding schema/ and reports/.

    Sorted for deterministic output.
    """
    paths: list[Path] = []
    for p in sorted(results_dir.rglob("*.json")):
        rel_parts = p.relative_to(results_dir).parts
        if rel_parts and rel_parts[0] in _NON_RESULT_DIRS:
            continue
        paths.append(p)
    return paths


def load_result(path: Path) -> ResultFile:
    with path.open() as fh:
        return ResultFile(path=path, data=json.load(fh))


def _get(data: dict[str, Any], dotted: str) -> Any:
    """Fetch ``data["a"]["b"]`` for ``dotted == "a.b"``; returns None if absent."""
    cur: Any = data
    for key in dotted.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def cross_field_errors(data: dict[str, Any]) -> list[str]:
    """Rules the schema cannot express (see issue #4 / result.schema.json notes).

    R1: a ``measured`` result runs on silicon at some fabric clock, so ``config.fclk_mhz``
        is mandatory (PLAN §10 checklist: f_clk logged per result).
    R2: any model taken through the FPGA (an ``estimate`` or ``measured`` result naming a
        ``config.model``) went through an FPGA AI Suite architecture file, so ``config.arch_file``
        is mandatory. A ``reference`` result is the CPU/OpenVINO baseline and is exempt.
    """
    errors: list[str] = []
    kind = data.get("kind")
    if kind == "measured" and _get(data, "config.fclk_mhz") is None:
        errors.append("R1: kind='measured' requires config.fclk_mhz (PLAN §10: log f_clk per result)")
    if kind in ("estimate", "measured") and _get(data, "config.model") is not None:
        if _get(data, "config.arch_file") is None:
            errors.append(
                "R2: a %s result naming config.model requires config.arch_file "
                "(the model was compiled against an FPGA AI Suite architecture file)" % kind
            )
    return errors


def validate_result(
    data: dict[str, Any], validator: jsonschema.protocols.Validator
) -> list[str]:
    """Return a list of human-readable error strings for one result dict (empty == valid)."""
    errors: list[str] = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "<root>"
        errors.append(f"schema: at '{loc}': {err.message}")
    errors.extend(cross_field_errors(data))
    return errors


def make_validator(schema: dict[str, Any] | None = None) -> jsonschema.protocols.Validator:
    schema = schema if schema is not None else load_schema()
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)
