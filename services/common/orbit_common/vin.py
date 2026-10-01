"""VIN validation: 17 chars, no I/O/Q, ISO 3779 check digit at position 9."""

import re
from functools import lru_cache

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_TRANSLIT = {
    c: v
    for c, v in zip(
        "ABCDEFGHJKLMNPRSTUVWXYZ",
        [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9],
        strict=True,
    )
}
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def _value(ch: str) -> int:
    return int(ch) if ch.isdigit() else _TRANSLIT[ch]


def check_digit(vin16: str) -> str:
    """Compute the check digit for a VIN whose 9th char is ignored. O(1) (17 iterations)."""
    total = sum(_value(c) * w for c, w in zip(vin16, _WEIGHTS, strict=True) if w)
    r = total % 11
    return "X" if r == 10 else str(r)


@lru_cache(maxsize=262_144)  # a fleet repeats the same VINs every second; validate each once
def is_valid(vin: str) -> bool:
    return bool(VIN_RE.match(vin)) and check_digit(vin) == vin[8]


def make_valid(prefix17: str) -> str:
    """Return the VIN with a corrected check digit (used by simulator and seed)."""
    v = prefix17.upper()
    if not VIN_RE.match(v):
        raise ValueError(f"bad VIN charset: {v}")
    return v[:8] + check_digit(v) + v[9:]
