from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import plistlib
import time
from typing import Any

from cluesb.model import (
    ClassificationConfidence,
    CollectorMetadata,
    HealthStatus,
    NodeKind,
    UsbNode,
    UsbSnapshot,
)
from cluesb.speeds import normalize_speed


IOREG_COMMAND = ("/usr/sbin/ioreg", "-a", "-l", "-p", "IOUSB")


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, bytes) and len(value) <= 8:
        return int.from_bytes(value, "little")
    if isinstance(value, str):
        try:
            return int(value.strip(), 0)
        except ValueError:
            return None
    return None


def _first(props: dict[str, Any], *keys: str) -> Any:
    return next((props[key] for key in keys if key in props), None)


def _link_speed(props: dict[str, Any]) -> tuple[Any, Any]:
    """Return the best reported operational speed and its normalization.

    ``UsbLinkSpeed`` is the negotiated rate in bits per second. ``USBSpeed``
    and generic speed properties are fallbacks. Apple's older ``Device Speed``
    enum is deliberately ignored because its values do not share that mapping.
    """
    first_reported: Any = None
    for key in ("UsbLinkSpeed", "USBSpeed", "USB Speed", "speed"):
        if key not in props:
            continue
        raw = props[key]
        if first_reported is None:
            first_reported = raw
        speed = normalize_speed(raw)
        if speed.bps is not None:
            return raw, speed
    return first_reported, normalize_speed(first_reported)


def _classify(props: dict[str, Any]) -> tuple[NodeKind, ClassificationConfidence]:
    category = str(props.get("IOMatchCategory", "")).lower()
    cls = str(_first(props, "IOObjectClass", "IOClass") or "")
    if category == "usb-host" or "XHCI" in cls:
        return NodeKind.CONTROLLER, ClassificationConfidence.AUTHORITATIVE
    device_class = _integer(_first(props, "bDeviceClass", "USB Device Class"))
    interface_class = _integer(_first(props, "bInterfaceClass", "USB Interface Class"))
    if device_class == 9 or interface_class == 9 or "USBHub" in cls:
        return NodeKind.HUB, ClassificationConfidence.AUTHORITATIVE
    if "IOUSBHostDevice" in cls or device_class is not None:
        return NodeKind.DEVICE, ClassificationConfidence.AUTHORITATIVE
    name = str(_first(props, "USB Product Name", "IORegistryEntryName") or "")
    if "hub" in name.lower():
        return NodeKind.HUB, ClassificationConfidence.HEURISTIC
    return NodeKind.UNKNOWN, ClassificationConfidence.UNKNOWN


def _identity(path: str, props: dict[str, Any]) -> str:
    if path:
        return hashlib.sha256(path.encode()).hexdigest()[:20]
    ingredients = repr((_first(props, "IORegistryEntryID", "locationID"), sorted(props)))
    return hashlib.sha256(ingredients.encode()).hexdigest()[:20]


def _physical_identity(props: dict[str, Any]) -> str | None:
    serial = _first(props, "USB Serial Number", "kUSBSerialNumberString", "serial_num")
    vid = _integer(_first(props, "idVendor", "vendor_id"))
    pid = _integer(_first(props, "idProduct", "product_id"))
    if not serial or vid is None or pid is None:
        return None
    value = f"{vid:04x}:{pid:04x}:{serial}"
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def parse_ioreg(data: bytes, *, duration_seconds: float | None = None) -> UsbSnapshot:
    archive = plistlib.loads(data)
    if not isinstance(archive, dict) or "IOUSB" not in archive.get("IORegistryPlanes", {}):
        raise ValueError("IOUSB registry plane is unavailable")
    nodes: dict[str, UsbNode] = {}
    child_lists: dict[str, list[str]] = {}
    roots: list[str] = []

    def visit(props: dict[str, Any], parent_id: str | None, parent_path: str) -> None:
        name = str(_first(props, "USB Product Name", "Product Name", "IORegistryEntryName") or "Unknown USB node")
        location = str(props.get("IORegistryEntryLocation", ""))
        entry_id = _integer(props.get("IORegistryEntryID"))
        segment = f"{name}@{location or entry_id or 'unknown'}"
        path = f"{parent_path}/{segment}"
        node_id = _identity(path, props)
        kind, confidence = _classify(props)
        raw_speed, speed = _link_speed(props)
        node = UsbNode(
            id=node_id,
            name=name,
            kind=kind,
            classification=confidence,
            parent_id=parent_id,
            registry_path=path,
            registry_class=str(_first(props, "IOObjectClass", "IOClass") or "") or None,
            registry_entry_id=entry_id,
            location_id=_integer(_first(props, "locationID", "location_id")),
            address=_integer(_first(props, "USB Address", "USB Address Number", "address")),
            port=_integer(_first(props, "PortNum", "port", "port_number")),
            vendor_id=_integer(_first(props, "idVendor", "vendor_id")),
            product_id=_integer(_first(props, "idProduct", "product_id")),
            manufacturer=_first(props, "USB Vendor Name", "kUSBVendorString", "manufacturer"),
            product=_first(props, "USB Product Name", "kUSBProductString", "product_name"),
            serial_number=_first(props, "USB Serial Number", "kUSBSerialNumberString", "serial_num"),
            device_class=_integer(_first(props, "bDeviceClass", "USB Device Class")),
            device_subclass=_integer(_first(props, "bDeviceSubClass", "USB Device Subclass")),
            device_protocol=_integer(_first(props, "bDeviceProtocol", "USB Device Protocol")),
            bcd_usb=_integer(_first(props, "bcdUSB", "USB Release Number")),
            configuration=_integer(_first(props, "bConfigurationValue", "USB Configuration Value")),
            speed_bps=speed.bps,
            speed_name=speed.label,
            raw_speed=raw_speed,
            physical_identity=_physical_identity(props),
            raw_properties={key: value for key, value in props.items() if key != "IORegistryEntryChildren"},
        )
        nodes[node_id] = node
        child_lists[node_id] = []
        if parent_id is None:
            roots.append(node_id)
        else:
            child_lists[parent_id].append(node_id)
        for child in props.get("IORegistryEntryChildren", []):
            if isinstance(child, dict):
                visit(child, node_id, path)

    for child in archive.get("IORegistryEntryChildren", []):
        if isinstance(child, dict):
            visit(child, None, "IOUSB:")

    def enrich(node_id: str, controller_id: str | None, path_ids: tuple[str, ...], hub_depth: int) -> None:
        node = nodes[node_id]
        current_controller = node_id if node.kind is NodeKind.CONTROLLER else controller_id
        current_hub_depth = hub_depth + (1 if node.kind is NodeKind.HUB else 0)
        current_path = path_ids + (node_id,)
        known_speeds = [nodes[item].speed_bps for item in current_path if nodes[item].speed_bps]
        status = HealthStatus.OK if node.kind is not NodeKind.UNKNOWN and node.speed_bps else HealthStatus.UNKNOWN
        nodes[node_id] = replace(
            node,
            children=tuple(child_lists[node_id]),
            tree_depth=len(path_ids),
            hub_depth=current_hub_depth,
            controller_id=current_controller,
            path_ids=current_path,
            path_ceiling_bps=min(known_speeds) if known_speeds else None,
            status=status,
        )
        for child_id in child_lists[node_id]:
            enrich(child_id, current_controller, current_path, current_hub_depth)

    for root_id in roots:
        enrich(root_id, None, (), 0)
    return UsbSnapshot(
        datetime.now(timezone.utc),
        nodes,
        tuple(roots),
        CollectorMetadata(IOREG_COMMAND, duration_seconds, (), "ioreg"),
    )


class IoregCollector:
    def __init__(self, *, timeout: float = 10.0) -> None:
        self.timeout = timeout

    async def snapshot(self) -> UsbSnapshot:
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *IOREG_COMMAND,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(f"ioreg timed out after {self.timeout:g}s") from None
        if process.returncode:
            message = stderr.decode(errors="replace").strip() or f"exit {process.returncode}"
            raise RuntimeError(f"ioreg failed: {message}")
        return parse_ioreg(stdout, duration_seconds=time.monotonic() - started)
