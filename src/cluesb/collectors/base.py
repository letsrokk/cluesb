from __future__ import annotations

from typing import Protocol

from cluesb.model import UsbSnapshot


class TopologyCollector(Protocol):
    async def snapshot(self) -> UsbSnapshot: ...
