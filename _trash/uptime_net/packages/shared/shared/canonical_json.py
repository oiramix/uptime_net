"""Deterministic JSON serialization for signing.

MVP rule (frozen): avoid floats for signature stability. Use ints for *_ms fields.
"""

from __future__ import annotations

import json
from typing import Any, Iterable


class CanonicalJSONError(ValueError):
    pass


def _reject_non_json(x: Any, path: str = "$") -> None:
    # JSON allows: dict, list, str, int, float, bool, None.
    # We DISALLOW float in MVP for stable signatures.
    if x is None or isinstance(x, (str, bool, int)):
        return
    if isinstance(x, float):
        raise CanonicalJSONError(f"Float is not allowed in canonical JSON (path {path}).")
    if isinstance(x, list):
        for i, v in enumerate(x):
            _reject_non_json(v, f"{path}[{i}]")
        return
    if isinstance(x, dict):
        for k, v in x.items():
            if not isinstance(k, str):
                raise CanonicalJSONError(f"Non-string key {k!r} at {path}")
            _reject_non_json(v, f"{path}.{k}")
        return
    raise CanonicalJSONError(f"Non-JSON type {type(x).__name__} at {path}")


def canonical_dumps(obj: Any) -> bytes:
    """Return canonical JSON bytes.

    - UTF-8
    - sort keys lexicographically
    - no whitespace
    - rejects NaN/Inf (allow_nan=False)
    - rejects floats entirely (MVP)
    """
    _reject_non_json(obj)
    s = json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return s.encode("utf-8")


def strip_keys_deep(obj: Any, keys: Iterable[str]) -> Any:
    """Deep-copy obj while removing any dict keys matching `keys` at any depth."""
    keyset = set(keys)
    if isinstance(obj, list):
        return [strip_keys_deep(v, keyset) for v in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in keyset:
                continue
            out[k] = strip_keys_deep(v, keyset)
        return out
    return obj
