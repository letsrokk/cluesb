from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
import hashlib
import json
import secrets
from typing import Any, Mapping

from cluesb.model import UsbSnapshot


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"encoding": "hex", "value": value.hex()}
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_plain(item) for item in value]
    return value


def _redact(value: Any, salt: bytes, key: str = "") -> Any:
    if "serial" in key.casefold() and value not in (None, ""):
        digest = hashlib.sha256(salt + str(value).encode()).hexdigest()[:20]
        return f"sha256:{digest}"
    if isinstance(value, dict):
        return {item_key: _redact(item, salt, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, salt, key) for item in value]
    return value


def snapshot_document(snapshot: UsbSnapshot, *, events: list[Any] | tuple[Any, ...] = (), redact: bool = False) -> dict[str, Any]:
    document = {
        "schema_version": 3,
        "timestamp": snapshot.timestamp.isoformat(),
        "system": _plain(snapshot.system),
        "controllers": list(snapshot.roots),
        "nodes": [_plain(node) for node in snapshot.nodes.values()],
        "events": _plain(events),
        "warnings": [message for node in snapshot.nodes.values() for message in node.diagnostics],
        "collector": _plain(snapshot.metadata),
        "display_controllers": [_plain(controller) for controller in snapshot.display_controllers.values()],
        "displays": [_plain(display) for display in snapshot.displays.values()],
        "thunderbolt": [_plain(node) for node in snapshot.thunderbolt_nodes.values()],
        "display_collector": _plain(snapshot.display_metadata),
        "thunderbolt_collector": _plain(snapshot.thunderbolt_metadata),
    }
    return _redact(document, secrets.token_bytes(32)) if redact else document


def snapshot_json(snapshot: UsbSnapshot, *, events: list[Any] | tuple[Any, ...] = (), redact: bool = False) -> str:
    return json.dumps(snapshot_document(snapshot, events=events, redact=redact), indent=2, sort_keys=True)
