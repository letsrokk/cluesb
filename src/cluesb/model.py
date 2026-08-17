from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping


class NodeKind(StrEnum):
    CONTROLLER = "controller"
    HUB = "hub"
    DEVICE = "device"
    UNKNOWN = "unknown"


class ClassificationConfidence(StrEnum):
    AUTHORITATIVE = "authoritative"
    HEURISTIC = "heuristic"
    UNKNOWN = "unknown"


class HealthStatus(StrEnum):
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    DISCONNECTED = "disconnected"
    UNKNOWN = "unknown"


class ConnectionType(StrEnum):
    INTERNAL = "Internal"
    HDMI = "HDMI"
    DISPLAYPORT = "DisplayPort"
    USB_C = "USB-C"
    THUNDERBOLT = "Thunderbolt/USB4"
    UNKNOWN = "Unknown"


class CorrelationConfidence(StrEnum):
    AUTHORITATIVE = "authoritative"
    STRONG = "strong"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class UsbNode:
    id: str
    name: str
    kind: NodeKind
    classification: ClassificationConfidence
    parent_id: str | None
    children: tuple[str, ...] = ()
    registry_path: str | None = None
    registry_class: str | None = None
    registry_entry_id: int | None = None
    location_id: int | None = None
    address: int | None = None
    port: int | None = None
    vendor_id: int | None = None
    product_id: int | None = None
    manufacturer: str | None = None
    product: str | None = None
    serial_number: str | None = None
    device_class: int | None = None
    device_subclass: int | None = None
    device_protocol: int | None = None
    bcd_usb: int | None = None
    configuration: int | None = None
    speed_bps: int | None = None
    speed_name: str = "Unknown"
    raw_speed: Any = None
    tree_depth: int = 0
    hub_depth: int = 0
    controller_id: str | None = None
    path_ids: tuple[str, ...] = ()
    path_ceiling_bps: int | None = None
    physical_identity: str | None = None
    status: HealthStatus = HealthStatus.UNKNOWN
    diagnostics: tuple[str, ...] = ()
    raw_properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CollectorMetadata:
    command: tuple[str, ...] = ()
    duration_seconds: float | None = None
    warnings: tuple[str, ...] = ()
    source: str = "ioreg"


@dataclass(frozen=True, slots=True)
class DisplayController:
    id: str
    name: str
    bus: str | None = None
    children: tuple[str, ...] = ()
    raw_properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DisplayNode:
    id: str
    name: str
    controller_id: str
    connection_type: ConnectionType = ConnectionType.UNKNOWN
    vendor_id: int | None = None
    product_id: int | None = None
    serial_number: str | None = None
    pixel_width: int | None = None
    pixel_height: int | None = None
    mode_width: int | None = None
    mode_height: int | None = None
    refresh_hz: float | None = None
    bit_depth: int | None = None
    is_main: bool | None = None
    is_mirrored: bool | None = None
    is_online: bool | None = None
    transport_path: tuple[str, ...] = ()
    transport_depth: int | None = None
    correlation: CorrelationConfidence = CorrelationConfidence.NONE
    correlation_evidence: tuple[str, ...] = ()
    link_speed: str = "Not reported"
    status: HealthStatus = HealthStatus.OK
    diagnostics: tuple[str, ...] = ()
    raw_properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ThunderboltNode:
    id: str
    name: str
    parent_id: str | None
    children: tuple[str, ...] = ()
    domain_uuid: str | None = None
    route: str | None = None
    receptacle_id: str | None = None
    device_uid: str | None = None
    depth: int = 0
    speed_text: str | None = None
    speed_is_capability: bool = False
    raw_properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UsbSnapshot:
    timestamp: datetime
    nodes: Mapping[str, UsbNode]
    roots: tuple[str, ...]
    metadata: CollectorMetadata = CollectorMetadata()
    system: Mapping[str, Any] = field(default_factory=dict)
    display_controllers: Mapping[str, DisplayController] = field(default_factory=dict)
    displays: Mapping[str, DisplayNode] = field(default_factory=dict)
    thunderbolt_nodes: Mapping[str, ThunderboltNode] = field(default_factory=dict)
    thunderbolt_roots: tuple[str, ...] = ()
    display_metadata: CollectorMetadata = CollectorMetadata(source="system_profiler:displays")
    thunderbolt_metadata: CollectorMetadata = CollectorMetadata(source="system_profiler:thunderbolt")

    @classmethod
    def empty(cls) -> "UsbSnapshot":
        return cls(datetime.now(timezone.utc), {}, ())
