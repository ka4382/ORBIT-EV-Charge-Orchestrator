"""Presentation-layer privacy: finance and auditor roles never see precise vehicle positions."""

from __future__ import annotations

from typing import Any

MASKED_ROLES = frozenset({"finance", "auditor"})
PRECISION = 2  # decimals ≈ 1.1 km


def masks_location(role: str) -> bool:
    return role in MASKED_ROLES


def mask_coord(value: float | None) -> float | None:
    return None if value is None else round(float(value), PRECISION)


def mask_location(role: str, record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a copy of `record` with lat/lon rounded when the role requires it."""
    if record is None or not masks_location(role):
        return record
    out = dict(record)
    for k in ("lat", "lon"):
        if k in out:
            out[k] = mask_coord(out[k])
    return out
