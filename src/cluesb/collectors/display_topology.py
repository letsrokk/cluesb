from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import time
from typing import Any, Callable

from cluesb.collectors.base import TopologyCollector
from cluesb.collectors.displays import correlate_display_transports, parse_displays
from cluesb.collectors.thunderbolt import parse_thunderbolt
from cluesb.model import CollectorMetadata, UsbSnapshot


class SystemProfilerDataCollector:
    async def collect(self, data_type: str) -> bytes:
        process = await asyncio.create_subprocess_exec(
            "/usr/sbin/system_profiler", data_type, "-xml", "-detailLevel", "full",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10.0)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError(f"{data_type} timed out after 10 seconds") from None
        if process.returncode:
            message = stderr.decode(errors="replace").strip() or f"exit status {process.returncode}"
            raise RuntimeError(f"{data_type}: {message}")
        return stdout


@dataclass(slots=True)
class _Cache:
    value: Any
    next_refresh: float = 0.0
    metadata: CollectorMetadata = CollectorMetadata()


class DisplayTopologyCollector:
    """Merge cached display and Thunderbolt reports into fast USB snapshots."""

    def __init__(
        self,
        primary: TopologyCollector,
        *,
        profiler: Any | None = None,
        refresh_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.primary = primary
        self.profiler = profiler or SystemProfilerDataCollector()
        self.refresh_seconds = refresh_seconds
        self.clock = clock
        self._display = _Cache(({}, {}))
        self._thunderbolt = _Cache(({}, ()))
        self._lock = asyncio.Lock()

    async def _refresh(self, data_type: str, cache: _Cache, parser: Any) -> None:
        now = self.clock()
        if now < cache.next_refresh:
            return
        started = time.monotonic()
        command = ("/usr/sbin/system_profiler", data_type, "-xml", "-detailLevel", "full")
        try:
            cache.value = parser(await self.profiler.collect(data_type))
            cache.metadata = CollectorMetadata(
                command=command, duration_seconds=time.monotonic() - started,
                source=f"system_profiler:{data_type}",
            )
        except Exception as error:
            cache.metadata = CollectorMetadata(
                command=command, duration_seconds=time.monotonic() - started,
                warnings=(f"Stale cached {data_type} data: {error}",),
                source=f"system_profiler:{data_type}",
            )
        finally:
            cache.next_refresh = now + self.refresh_seconds

    async def snapshot(self) -> UsbSnapshot:
        snapshot = await self.primary.snapshot()
        async with self._lock:
            await asyncio.gather(
                self._refresh("SPDisplaysDataType", self._display, parse_displays),
                self._refresh("SPThunderboltDataType", self._thunderbolt, parse_thunderbolt),
            )
            controllers, displays = self._display.value
            thunderbolt_nodes, thunderbolt_roots = self._thunderbolt.value
        combined = replace(
            snapshot,
            display_controllers=controllers,
            displays=displays,
            thunderbolt_nodes=thunderbolt_nodes,
            thunderbolt_roots=thunderbolt_roots,
            display_metadata=self._display.metadata,
            thunderbolt_metadata=self._thunderbolt.metadata,
        )
        return correlate_display_transports(combined)
