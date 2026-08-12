"""Canonical source-audit manifest helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_manifest_hash(manifest: dict[str, Any]) -> str:
    """Hash a complete manifest so an audit cannot use a weakened copy."""
    encoded = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
