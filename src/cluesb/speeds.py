from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True, slots=True)
class UsbSpeed:
    bps: int | None
    label: str
    detail: str
    raw: Any


_SPEEDS = {
    1_500_000: ("USB 1.5Mbps", "Low Speed"),
    12_000_000: ("USB 12Mbps", "Full Speed"),
    480_000_000: ("USB 480Mbps", "High Speed"),
    5_000_000_000: ("USB 5Gbps", "SuperSpeed"),
    10_000_000_000: ("USB 10Gbps", "SuperSpeedPlus"),
    20_000_000_000: ("USB 20Gbps", "SuperSpeedPlus x2"),
}
_ENUM_SPEEDS = {
    1: 1_500_000,
    2: 12_000_000,
    3: 480_000_000,
    4: 5_000_000_000,
    5: 10_000_000_000,
    6: 20_000_000_000,
}


def normalize_speed(raw: Any) -> UsbSpeed:
    bps: int | None = None
    if isinstance(raw, bool):
        pass
    elif isinstance(raw, int):
        bps = _ENUM_SPEEDS.get(raw, raw if raw in _SPEEDS else None)
    elif isinstance(raw, bytes):
        bps = int.from_bytes(raw, "little") if len(raw) <= 8 else None
        bps = _ENUM_SPEEDS.get(bps, bps if bps in _SPEEDS else None)
    elif isinstance(raw, str):
        text = raw.lower().replace("/s", "ps")
        match = re.search(r"(20|10|5)\s*g(?:b|bit)?ps", text)
        if match:
            bps = int(match.group(1)) * 1_000_000_000
        else:
            match = re.search(r"(480|12|1\.5)\s*m(?:b|bit)?ps", text)
            if match:
                bps = int(float(match.group(1)) * 1_000_000)
    label, detail = _SPEEDS.get(bps, ("Unknown", "Unknown"))
    return UsbSpeed(bps, label, detail, raw)


def format_speed(bps: int | None) -> str:
    return _SPEEDS.get(bps, ("Unknown", "Unknown"))[0]
