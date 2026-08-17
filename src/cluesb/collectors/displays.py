from __future__ import annotations

from dataclasses import replace
import hashlib
import plistlib
import re
from typing import Any

from cluesb.model import (
    ConnectionType,
    CorrelationConfidence,
    DisplayController,
    DisplayNode,
    UsbSnapshot,
)


def _identifier(prefix: str, *parts: object) -> str:
    value = "\0".join(str(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def _integer(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    try:
        return int(token, 16 if any(character in "abcdef" for character in token) else 0)
    except ValueError:
        return None


def _dimensions(value: Any) -> tuple[int | None, int | None]:
    match = re.search(r"(\d+)\s*x\s*(\d+)", str(value or ""), re.IGNORECASE)
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def _refresh(value: Any) -> float | None:
    match = re.search(r"@\s*([0-9.]+)\s*Hz", str(value or ""), re.IGNORECASE)
    return float(match.group(1)) if match else None


def _depth(value: Any) -> int | None:
    match = re.search(r"(\d+)\s*-?Bit", str(value or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _yes(value: Any) -> bool | None:
    if value is None:
        return None
    token = str(value).casefold()
    return token in {"yes", "true", "1", "spdisplays_yes"}


def _connection(value: Any) -> ConnectionType:
    token = str(value or "").casefold()
    if "internal" in token or "built-in" in token:
        return ConnectionType.INTERNAL
    if "hdmi" in token:
        return ConnectionType.HDMI
    if "thunderbolt" in token:
        return ConnectionType.THUNDERBOLT
    if "usb-c" in token or "usb_c" in token:
        return ConnectionType.USB_C
    if "displayport" in token or "display_port" in token:
        return ConnectionType.DISPLAYPORT
    return ConnectionType.UNKNOWN


def _get(raw: dict[str, Any], key: str) -> Any:
    return raw.get(key, raw.get(f"_{key}"))


def parse_displays(data: bytes) -> tuple[dict[str, DisplayController], dict[str, DisplayNode]]:
    archive = plistlib.loads(data)
    controllers: dict[str, DisplayController] = {}
    displays: dict[str, DisplayNode] = {}
    sections = archive if isinstance(archive, list) else [archive]
    for section in sections:
        if not isinstance(section, dict) or section.get("_dataType") != "SPDisplaysDataType":
            continue
        for index, item in enumerate(section.get("_items", [])):
            if not isinstance(item, dict):
                continue
            controller_id = _identifier("display-controller", item.get("_name", "GPU"), item.get("sppci_bus"), index)
            child_ids: list[str] = []
            display_items = item.get("spdisplays_ndrvs", item.get("_items", []))
            for display_index, raw in enumerate(display_items):
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("_name", "Unknown display"))
                vendor = _integer(_get(raw, "spdisplays_display-vendor-id"))
                product = _integer(_get(raw, "spdisplays_display-product-id"))
                serial = _get(raw, "spdisplays_display-serial-number")
                stable_hint = serial or _get(raw, "spdisplays_displayID") or f"{name}:{display_index}"
                display_id = _identifier("display", vendor, product, stable_hint, controller_id)
                pixel_width, pixel_height = _dimensions(
                    _get(raw, "spdisplays_pixels") or _get(raw, "spdisplays_pixelresolution")
                )
                resolution = _get(raw, "spdisplays_resolution")
                mode_width, mode_height = _dimensions(resolution)
                connection = _connection(raw.get("spdisplays_connection_type") or raw.get("spdisplays_display_type"))
                displays[display_id] = DisplayNode(
                    id=display_id, name=name, controller_id=controller_id,
                    connection_type=connection, vendor_id=vendor, product_id=product,
                    serial_number=str(serial) if serial is not None else None,
                    pixel_width=pixel_width, pixel_height=pixel_height,
                    mode_width=mode_width, mode_height=mode_height,
                    refresh_hz=_refresh(resolution),
                    bit_depth=_depth(raw.get("spdisplays_depth")),
                    is_main=_yes(raw.get("spdisplays_main")),
                    is_mirrored=_yes(raw.get("spdisplays_mirror")),
                    is_online=_yes(raw.get("spdisplays_online")),
                    raw_properties={key: value for key, value in raw.items() if key != "_items"},
                )
                child_ids.append(display_id)
            controllers[controller_id] = DisplayController(
                controller_id, str(item.get("_name", "Unknown display controller")),
                str(item.get("sppci_bus")) if item.get("sppci_bus") is not None else None,
                tuple(child_ids), {key: value for key, value in item.items() if key != "_items"},
            )
    return controllers, displays


def correlate_display_transports(snapshot: UsbSnapshot) -> UsbSnapshot:
    """Attach only paths supported by explicit identifiers shared by both sources."""
    displays = dict(snapshot.displays)
    for display_id, display in displays.items():
        if display.connection_type is ConnectionType.INTERNAL:
            continue
        raw_values = {str(value) for key, value in display.raw_properties.items()
                      if any(token in key.casefold() for token in ("route", "domain", "receptacle", "registry"))}
        matches = []
        for node in snapshot.thunderbolt_nodes.values():
            node_values = {value for value in (node.domain_uuid, node.route, node.receptacle_id) if value}
            if raw_values & node_values:
                matches.append(node)
        if len(matches) == 1:
            node = matches[0]
            path: list[str] = []
            seen: set[str] = set()
            while node and node.id not in seen:
                path.append(node.id)
                seen.add(node.id)
                node = snapshot.thunderbolt_nodes.get(node.parent_id) if node.parent_id else None
            path.reverse()
            displays[display_id] = replace(
                display, transport_path=tuple(path), transport_depth=max(0, len(path) - 1),
                correlation=CorrelationConfidence.STRONG,
                correlation_evidence=("Shared transport identifier reported by System Information",),
            )
        elif display.connection_type is ConnectionType.HDMI:
            displays[display_id] = replace(display, transport_depth=0)
    return replace(snapshot, displays=displays)
