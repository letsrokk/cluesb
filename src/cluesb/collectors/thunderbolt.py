from __future__ import annotations

import hashlib
import plistlib
from typing import Any

from cluesb.model import ThunderboltNode


def _id(*parts: object) -> str:
    return "thunderbolt:" + hashlib.sha256("\0".join(str(part) for part in parts).encode()).hexdigest()[:16]


def _is_connected(value: Any) -> bool:
    token = str(value or "").strip().casefold().replace(" ", "_")
    return token in {"connected", "device_connected", "receptacle_connected"}


def _reported_speed(raw: dict[str, Any]) -> tuple[Any, Any]:
    receptacles = [
        (key, value)
        for key, value in raw.items()
        if key.startswith("receptacle_") and isinstance(value, dict)
    ]
    connected = [item for item in receptacles if _is_connected(item[1].get("receptacle_status_key"))]
    if connected:
        selected = next((item[1] for item in connected if "upstream" in item[0]), connected[0][1])
        speed = selected.get("current_speed_key") or raw.get("current_speed_key")
    else:
        selected = {}
        speed = raw.get("current_speed_key")
        if speed is None:
            selected = next((value for _, value in receptacles if value.get("current_speed_key") is not None), {})
            speed = selected.get("current_speed_key")
    receptacle = selected.get("receptacle_id_key")
    if receptacle is None:
        receptacle = next(
            (value["receptacle_id_key"] for _, value in receptacles if value.get("receptacle_id_key") is not None),
            None,
        )
    return speed, receptacle


def parse_thunderbolt(data: bytes) -> tuple[dict[str, ThunderboltNode], tuple[str, ...]]:
    archive = plistlib.loads(data)
    nodes: dict[str, ThunderboltNode] = {}
    roots: list[str] = []

    def walk(raw: dict[str, Any], parent_id: str | None, depth: int, domain: str | None) -> str:
        route = raw.get("route_string_key")
        domain = str(raw.get("domain_uuid_key") or domain) if raw.get("domain_uuid_key") or domain else None
        speed, receptacle = _reported_speed(raw)
        device_uid = raw.get("device_uid_key") or raw.get("switch_uid_key")
        node_id = _id(domain, route, receptacle, device_uid, raw.get("_name"), depth)
        child_ids = tuple(
            walk(child, node_id, depth + 1, domain)
            for child in raw.get("_items", []) if isinstance(child, dict)
        )
        speed_text = str(speed) if speed is not None else None
        nodes[node_id] = ThunderboltNode(
            node_id, str(raw.get("_name", "Unknown Thunderbolt device")), parent_id,
            child_ids, domain, str(route) if route is not None else None,
            str(receptacle) if receptacle is not None else None,
            str(device_uid) if device_uid is not None else None, depth, speed_text,
            bool(speed_text and speed_text.casefold().startswith("up to")),
            {key: value for key, value in raw.items() if key != "_items"},
        )
        return node_id

    sections = archive if isinstance(archive, list) else [archive]
    for section in sections:
        if not isinstance(section, dict) or section.get("_dataType") != "SPThunderboltDataType":
            continue
        for item in section.get("_items", []):
            if isinstance(item, dict):
                roots.append(walk(item, None, 0, None))
    return nodes, tuple(roots)
