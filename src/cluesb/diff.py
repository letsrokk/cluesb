from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from cluesb.model import DisplayNode, NodeKind, ThunderboltNode, UsbNode, UsbSnapshot


class EventKind(StrEnum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CHANGED = "changed"
    MOVED = "moved"
    FLAPPING = "flapping"


@dataclass(frozen=True, slots=True)
class TopologyEvent:
    kind: EventKind
    timestamp: datetime
    node_id: str
    name: str
    changes: dict[str, tuple[Any, Any]]
    previous_node_id: str | None = None
    physical_identity: str | None = None
    transition_count: int | None = None
    resource_kind: str = "usb"
    connect_speed: str | None = None
    connect_tier: int | None = None


DIAGNOSTIC_FIELDS = (
    "parent_id", "location_id", "address", "port", "vendor_id", "product_id",
    "manufacturer", "serial_number", "kind", "classification", "speed_bps",
    "device_class", "device_subclass", "device_protocol", "bcd_usb", "configuration",
)
DISPLAY_FIELDS = (
    "name", "controller_id", "connection_type", "pixel_width", "pixel_height",
    "mode_width", "mode_height", "refresh_hz", "bit_depth", "is_main",
    "is_mirrored", "is_online", "transport_path", "transport_depth", "correlation",
    "link_speed", "status", "diagnostics",
)
THUNDERBOLT_FIELDS = (
    "name", "parent_id", "children", "route", "receptacle_id", "depth",
    "speed_text", "speed_is_capability",
)
FLAP_TRANSITIONS = 3
FLAP_WINDOW = timedelta(seconds=10)


class TopologyDiffer:
    def __init__(self) -> None:
        self._transitions: dict[str, deque[datetime]] = defaultdict(deque)

    def _event(self, kind: EventKind, at: datetime, node: UsbNode, **kwargs: Any) -> TopologyEvent:
        connect_context = {}
        if kind is EventKind.CONNECTED and node.kind in (NodeKind.DEVICE, NodeKind.HUB):
            connect_context = {
                "connect_speed": node.speed_name or "Unknown",
                "connect_tier": node.tree_depth + 1,
            }
        return TopologyEvent(kind, at, node.id, node.name, kwargs.pop("changes", {}),
                             physical_identity=node.physical_identity, **connect_context, **kwargs)

    def _record_transition(self, at: datetime, node: UsbNode, events: list[TopologyEvent]) -> None:
        identity = node.physical_identity or node.id
        transitions = self._transitions[identity]
        transitions.append(at)
        cutoff = at - FLAP_WINDOW
        while transitions and transitions[0] < cutoff:
            transitions.popleft()
        if len(transitions) == FLAP_TRANSITIONS:
            events.append(self._event(EventKind.FLAPPING, at, node, transition_count=len(transitions)))

    def diff(self, previous: UsbSnapshot, current: UsbSnapshot) -> list[TopologyEvent]:
        events: list[TopologyEvent] = []
        removed = {key: previous.nodes[key] for key in previous.nodes.keys() - current.nodes.keys()}
        added = {key: current.nodes[key] for key in current.nodes.keys() - previous.nodes.keys()}

        removed_by_physical: dict[str, list[UsbNode]] = defaultdict(list)
        added_by_physical: dict[str, list[UsbNode]] = defaultdict(list)
        for node in removed.values():
            if node.physical_identity:
                removed_by_physical[node.physical_identity].append(node)
        for node in added.values():
            if node.physical_identity:
                added_by_physical[node.physical_identity].append(node)
        for node_id, node in list(added.items()):
            old_matches = removed_by_physical.get(node.physical_identity or "", [])
            new_matches = added_by_physical.get(node.physical_identity or "", [])
            if len(old_matches) == len(new_matches) == 1:
                old = old_matches[0]
                events.append(self._event(EventKind.MOVED, current.timestamp, node, previous_node_id=old.id,
                                          changes={"id": (old.id, node.id), "parent_id": (old.parent_id, node.parent_id)}))
                removed.pop(old.id, None)
                added.pop(node_id, None)

        for node in removed.values():
            events.append(self._event(EventKind.DISCONNECTED, current.timestamp, node))
            self._record_transition(current.timestamp, node, events)
        for node in added.values():
            events.append(self._event(EventKind.CONNECTED, current.timestamp, node))
            self._record_transition(current.timestamp, node, events)
        for node_id in previous.nodes.keys() & current.nodes.keys():
            before, after = previous.nodes[node_id], current.nodes[node_id]
            changes = {
                field: (getattr(before, field), getattr(after, field))
                for field in DIAGNOSTIC_FIELDS
                if getattr(before, field) != getattr(after, field)
            }
            if changes:
                events.append(self._event(EventKind.CHANGED, current.timestamp, after, changes=changes))
        events.extend(self._diff_resources(previous.displays, current.displays, current.timestamp,
                                           DISPLAY_FIELDS, "display"))
        events.extend(self._diff_resources(previous.thunderbolt_nodes, current.thunderbolt_nodes,
                                           current.timestamp, THUNDERBOLT_FIELDS, "thunderbolt"))
        return events

    @staticmethod
    def _diff_resources(
        previous: dict[str, DisplayNode] | dict[str, ThunderboltNode] | Any,
        current: dict[str, DisplayNode] | dict[str, ThunderboltNode] | Any,
        at: datetime,
        fields: tuple[str, ...],
        resource_kind: str,
    ) -> list[TopologyEvent]:
        events: list[TopologyEvent] = []
        for node_id in previous.keys() - current.keys():
            node = previous[node_id]
            events.append(TopologyEvent(EventKind.DISCONNECTED, at, node_id, node.name, {},
                                        resource_kind=resource_kind))
        for node_id in current.keys() - previous.keys():
            node = current[node_id]
            events.append(TopologyEvent(EventKind.CONNECTED, at, node_id, node.name, {},
                                        resource_kind=resource_kind))
        for node_id in previous.keys() & current.keys():
            before, after = previous[node_id], current[node_id]
            changes = {field: (getattr(before, field), getattr(after, field)) for field in fields
                       if getattr(before, field) != getattr(after, field)}
            if changes:
                events.append(TopologyEvent(EventKind.CHANGED, at, node_id, after.name, changes,
                                            resource_kind=resource_kind))
        return events
