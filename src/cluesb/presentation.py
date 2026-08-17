from __future__ import annotations

from cluesb.model import NodeKind, UsbNode


def usb_route(node: UsbNode) -> str | None:
    if node.kind is not NodeKind.HUB or node.location_id is None:
        return None
    return f"{node.location_id:08X}"


def usb_tier_suffix(node: UsbNode) -> str:
    return f" — Tier {node.tree_depth + 1}"
