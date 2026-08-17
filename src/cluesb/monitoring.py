from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable

from cluesb.collectors.base import TopologyCollector
from cluesb.diagnostics import diagnose_snapshot
from cluesb.diff import EventKind, TopologyDiffer, TopologyEvent
from cluesb.model import HealthStatus, UsbNode, UsbSnapshot


class TopologyMonitor:
    def __init__(
        self,
        collector: TopologyCollector,
        *,
        history_limit: int = 1000,
        tombstone_seconds: float = 5.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.collector = collector
        self.history: deque[TopologyEvent] = deque(maxlen=history_limit)
        self.current = UsbSnapshot.empty()
        self.displayed = self.current
        self.last_error: str | None = None
        self.paused = False
        self._lock = asyncio.Lock()
        self._differ = TopologyDiffer()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._tombstone_lifetime = timedelta(seconds=tombstone_seconds)
        self._tombstones: dict[str, tuple[UsbNode, datetime]] = {}
        self._incomplete_scans: dict[str, int] = {}

    async def refresh(self) -> bool:
        if self._lock.locked():
            return False
        async with self._lock:
            try:
                incoming = self._track_incomplete(diagnose_snapshot(await self.collector.snapshot()))
            except Exception as error:
                self.last_error = str(error)
                return False
            events = self._differ.diff(self.current, incoming)
            for event in events:
                self.history.append(event)
                if event.kind is EventKind.DISCONNECTED and event.node_id in self.current.nodes:
                    old = self.current.nodes[event.node_id]
                    self._tombstones[event.node_id] = (
                        replace(old, status=HealthStatus.DISCONNECTED),
                        self._clock() + self._tombstone_lifetime,
                    )
                elif event.kind in (EventKind.CONNECTED, EventKind.MOVED):
                    self._tombstones.pop(event.node_id, None)
            self.current = incoming
            self.last_error = None
            if not self.paused:
                self.displayed = self._with_tombstones(incoming)
            return True

    def _track_incomplete(self, snapshot: UsbSnapshot) -> UsbSnapshot:
        nodes = dict(snapshot.nodes)
        active = set(snapshot.nodes)
        self._incomplete_scans = {key: value for key, value in self._incomplete_scans.items() if key in active}
        for node_id, node in snapshot.nodes.items():
            incomplete = node.kind.value in ("device", "unknown") and (
                node.vendor_id is None or node.product_id is None or node.speed_bps is None
            )
            if not incomplete:
                self._incomplete_scans.pop(node_id, None)
                continue
            count = self._incomplete_scans.get(node_id, 0) + 1
            self._incomplete_scans[node_id] = count
            if count >= 3:
                message = f"Incomplete USB properties persisted for {count} scans"
                nodes[node_id] = replace(node, status=HealthStatus.WARNING, diagnostics=node.diagnostics + (message,))
        return replace(snapshot, nodes=nodes)

    def _with_tombstones(self, snapshot: UsbSnapshot) -> UsbSnapshot:
        now = self._clock()
        self._tombstones = {key: item for key, item in self._tombstones.items() if item[1] > now}
        if not self._tombstones:
            return snapshot
        nodes = dict(snapshot.nodes)
        nodes.update({key: item[0] for key, item in self._tombstones.items()})
        for key, (node, _) in self._tombstones.items():
            if node.parent_id in nodes:
                parent = nodes[node.parent_id]
                if key not in parent.children:
                    nodes[node.parent_id] = replace(parent, children=parent.children + (key,))
        roots = list(snapshot.roots)
        for key, (node, _) in self._tombstones.items():
            if node.parent_id is None and key not in roots:
                roots.append(key)
        return replace(snapshot, nodes=nodes, roots=tuple(roots))

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False
        self.displayed = self._with_tombstones(self.current)
