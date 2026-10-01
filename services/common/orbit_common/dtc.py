"""OBD-II Diagnostic Trouble Code parsing. Pattern: [PCBU][0-3][0-9A-F]{3}."""

import re
from dataclasses import dataclass

DTC_RE = re.compile(r"\b([PCBU])([0-3])([0-9A-F]{3})\b")
_SYSTEM = {"P": "Powertrain", "C": "Chassis", "B": "Body", "U": "Network"}

# Codes that matter to charging/battery decisions (subset, documented in docs/algorithms.md)
BATTERY_CODES = {
    "P0A80": "Replace hybrid/EV battery pack",
    "P0A7F": "Battery pack deterioration",
    "P0AFA": "Battery system voltage low",
    "P0B3D": "Battery module voltage high",
    "P1A10": "Charging system fault (OEM)",
    "P0D2A": "Charger converter temperature high",
}


@dataclass(frozen=True, slots=True)
class Dtc:
    code: str
    system: str
    generic: bool
    description: str | None

    @property
    def is_battery(self) -> bool:
        return self.code in BATTERY_CODES


def parse(code: str) -> Dtc | None:
    m = DTC_RE.fullmatch(code.strip().upper())
    if not m:
        return None
    sys_, kind, _ = m.groups()
    full = m.group(0)
    return Dtc(code=full, system=_SYSTEM[sys_], generic=kind in "02", description=BATTERY_CODES.get(full))


def extract_all(raw: str) -> list[str]:
    """Pull every DTC out of a free-form OEM string like 'P0301;P0A80,' — O(n) regex scan."""
    return [m.group(0) for m in DTC_RE.finditer(raw.upper())]
