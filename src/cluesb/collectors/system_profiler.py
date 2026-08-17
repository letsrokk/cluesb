from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import plistlib
import time
from typing import Any

from cluesb.collectors.base import TopologyCollector
from cluesb.model import UsbSnapshot


@dataclass(frozen=True, slots=True)
class ProfilerRecord:
    name: str
    vendor_id: int | None
    product_id: int | None
    serial_number: str | None
    power: dict[str, Any]
    raw: dict[str, Any]


def _hex_integer(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        token = value.strip().split()[0]
        try:
            return int(token, 0)
        except ValueError:
            return None
    return None


def parse_system_profiler(data: bytes) -> list[ProfilerRecord]:
    archive = plistlib.loads(data)
    records: list[ProfilerRecord] = []

    def walk(item: dict[str, Any], *, bus: bool = False) -> None:
        children = item.get("_items", [])
        is_device = bus or any(key in item for key in ("vendor_id", "product_id", "serial_num", "device_speed"))
        if is_device and any(key in item for key in ("vendor_id", "product_id", "serial_num")):
            power = {key: value for key, value in item.items() if "current" in key or "power" in key}
            records.append(ProfilerRecord(
                str(item.get("_name", "Unknown USB device")),
                _hex_integer(item.get("vendor_id")),
                _hex_integer(item.get("product_id")),
                item.get("serial_num"),
                power,
                {key: value for key, value in item.items() if key != "_items"},
            ))
        for child in children:
            if isinstance(child, dict):
                walk(child, bus=True)

    for section in archive if isinstance(archive, list) else [archive]:
        if isinstance(section, dict):
            for item in section.get("_items", []):
                if isinstance(item, dict):
                    walk(item)
    return records


def enrich_snapshot(snapshot: UsbSnapshot, records: list[ProfilerRecord]) -> UsbSnapshot:
    nodes = dict(snapshot.nodes)
    used: set[int] = set()
    for node_id, node in snapshot.nodes.items():
        candidates = [(index, record) for index, record in enumerate(records) if index not in used
                      and record.vendor_id == node.vendor_id and record.product_id == node.product_id
                      and (not record.serial_number or not node.serial_number or record.serial_number == node.serial_number)]
        if len(candidates) != 1:
            continue
        index, record = candidates[0]
        used.add(index)
        raw = dict(node.raw_properties)
        raw["SystemProfiler"] = record.raw
        nodes[node_id] = replace(
            node,
            name=record.name if node.name.startswith("IOUSB") or node.name == "Unknown USB node" else node.name,
            serial_number=node.serial_number or record.serial_number,
            raw_properties=raw,
        )
    return replace(snapshot, nodes=nodes)


class SystemProfilerCollector:
    async def available_data_type(self) -> str | None:
        process = await asyncio.create_subprocess_exec(
            "/usr/sbin/system_profiler", "-listDataTypes",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode:
            return None
        return "SPUSBHostDataType" if b"SPUSBHostDataType" in stdout else None

    async def records(self) -> list[ProfilerRecord]:
        data_type = await self.available_data_type()
        if not data_type:
            return []
        process = await asyncio.create_subprocess_exec(
            "/usr/sbin/system_profiler", data_type, "-xml", "-detailLevel", "mini",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        return parse_system_profiler(stdout) if process.returncode == 0 else []


class EnrichedCollector:
    """Fast primary snapshots with independently cached best-effort enrichment."""

    def __init__(self, primary: TopologyCollector, profiler: Any | None = None, *, refresh_seconds: float = 30.0) -> None:
        self.primary = primary
        self.profiler = profiler or SystemProfilerCollector()
        self.refresh_seconds = refresh_seconds
        self._records: list[ProfilerRecord] = []
        self._next_refresh = 0.0

    async def snapshot(self) -> UsbSnapshot:
        snapshot = await self.primary.snapshot()
        warnings = list(snapshot.metadata.warnings)
        now = time.monotonic()
        if now >= self._next_refresh:
            try:
                self._records = await self.profiler.records()
            except Exception as error:
                warnings.append(f"System Information enrichment failed: {error}")
            finally:
                self._next_refresh = now + self.refresh_seconds
        enriched = enrich_snapshot(snapshot, self._records)
        if warnings != list(enriched.metadata.warnings):
            enriched = replace(enriched, metadata=replace(enriched.metadata, warnings=tuple(warnings)))
        return enriched
