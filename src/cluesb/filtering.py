from __future__ import annotations

from cluesb.model import UsbSnapshot
from cluesb.presentation import usb_route


def matching_node_ids(snapshot: UsbSnapshot, query: str) -> set[str]:
    needle = query.strip().casefold()
    if not needle:
        return set(snapshot.nodes)
    result: set[str] = set()
    for node in snapshot.nodes.values():
        fields = (
            node.name, node.manufacturer, node.serial_number, node.kind.value,
            node.speed_name,
            f"{node.vendor_id:04x}" if node.vendor_id is not None else "",
            f"{node.product_id:04x}" if node.product_id is not None else "",
            str(node.speed_bps or ""),
            usb_route(node),
        )
        if any(needle in str(field or "").casefold() for field in fields):
            result.update(node.path_ids)
    return result


def matching_display_ids(snapshot: UsbSnapshot, query: str) -> set[str]:
    needle = query.strip().casefold()
    if not needle:
        return set(snapshot.displays)
    result: set[str] = set()
    for display in snapshot.displays.values():
        controller = snapshot.display_controllers.get(display.controller_id)
        path_names = [snapshot.thunderbolt_nodes[node_id].name for node_id in display.transport_path
                      if node_id in snapshot.thunderbolt_nodes]
        refresh = f"{display.refresh_hz:g}Hz" if display.refresh_hz is not None else ""
        fields = (
            display.name, controller.name if controller else "", display.connection_type.value,
            display.vendor_id, display.product_id, display.serial_number,
            f"{display.mode_width} x {display.mode_height}",
            f"{display.pixel_width} x {display.pixel_height}", refresh,
            display.link_speed, *path_names,
        )
        if any(needle in str(field or "").casefold() for field in fields):
            result.add(display.id)
    return result


def matching_thunderbolt_ids(snapshot: UsbSnapshot, query: str) -> set[str]:
    needle = query.strip().casefold()
    if not needle:
        return set(snapshot.thunderbolt_nodes)
    result: set[str] = set()
    for node in snapshot.thunderbolt_nodes.values():
        fields = (
            node.name, node.route, node.receptacle_id, node.device_uid,
            f"depth {node.depth}", node.speed_text,
        )
        if not any(needle in str(field or "").casefold() for field in fields):
            continue
        current = node
        seen: set[str] = set()
        while current.id not in seen:
            result.add(current.id)
            seen.add(current.id)
            if not current.parent_id or current.parent_id not in snapshot.thunderbolt_nodes:
                break
            current = snapshot.thunderbolt_nodes[current.parent_id]
    return result
