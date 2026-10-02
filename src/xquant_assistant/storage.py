"""Atomic local artifact and state storage."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .domain import json_value


def atomic_write_json(path: str | Path, payload: Any) -> Path:
    """Write strict JSON through a sibling temporary file."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(
        json.dumps(json_value(payload), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


def immutable_write_json(path: str | Path, payload: Any) -> Path:
    """Publish a fully written file exactly once, including concurrent captures."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(json_value(payload), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def read_json(path: str | Path) -> dict[str, Any]:
    """Read a UTF-8 JSON mapping."""

    loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"expected JSON object: {path}")
    return loaded


def write_csv(path: str | Path, rows: Iterable[dict[str, Any]]) -> Path:
    """Write row dictionaries as UTF-8 CSV."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([json_value(row) for row in rows]).to_csv(
        destination, index=False, encoding="utf-8-sig"
    )
    return destination


def hash_payload(payload: Any) -> str:
    """Return deterministic SHA-256 for a JSON-compatible payload."""

    encoded = json.dumps(
        json_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def hash_source_tree(root: str | Path) -> str:
    """Hash Python source paths and contents in deterministic order."""

    base = Path(root)
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*.py")):
        digest.update(path.relative_to(base).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
