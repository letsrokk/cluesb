import json
from datetime import datetime, timezone

from cluesb.diff import EventKind, TopologyEvent
from cluesb.export import snapshot_document, snapshot_json
from cluesb.filtering import matching_node_ids
from cluesb.filtering import matching_display_ids, matching_thunderbolt_ids
from cluesb.collectors.displays import parse_displays
from .test_displays import display_plist, thunderbolt_plist
from cluesb.collectors.thunderbolt import parse_thunderbolt
from dataclasses import replace

from .test_ioreg_parser import archive
from cluesb.collectors.ioreg import parse_ioreg


def test_filter_keeps_matching_node_and_ancestors():
    snapshot = parse_ioreg(archive())
    matching = matching_node_ids(snapshot, "partial")
    endpoint = next(node for node in snapshot.nodes.values() if node.name == "Partial Device")
    assert matching == set(endpoint.path_ids)


def test_json_redacts_serials_in_normalized_and_raw_data():
    snapshot = parse_ioreg(archive())
    endpoint = next(node for node in snapshot.nodes.values() if node.name == "Partial Device")
    raw = dict(endpoint.raw_properties)
    raw["USB Serial Number"] = "secret"
    from dataclasses import replace
    nodes = dict(snapshot.nodes)
    nodes[endpoint.id] = replace(endpoint, serial_number="secret", raw_properties=raw)
    snapshot = replace(snapshot, nodes=nodes)
    output = snapshot_json(snapshot, redact=True)
    assert "secret" not in output
    document = json.loads(output)
    exported = next(item for item in document["nodes"] if item["name"] == "Partial Device")
    assert exported["serial_number"].startswith("sha256:")
    assert exported["raw_properties"]["USB Serial Number"].startswith("sha256:")


def test_export_has_version_and_controller_roots():
    document = snapshot_document(parse_ioreg(archive()))
    assert document["schema_version"] == 3
    assert document["controllers"]
    assert document["displays"] == []
    assert document["thunderbolt"] == []


def test_schema_v3_exports_usb_connect_context_and_nulls_for_other_events():
    events = (
        TopologyEvent(
            EventKind.CONNECTED,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            "device",
            "Camera",
            {},
            connect_speed="USB 5Gbps",
            connect_tier=4,
        ),
        TopologyEvent(
            EventKind.DISCONNECTED,
            datetime(2026, 1, 2, tzinfo=timezone.utc),
            "device",
            "Camera",
            {},
        ),
    )

    exported = snapshot_document(parse_ioreg(archive()), events=events)["events"]

    assert exported[0]["connect_speed"] == "USB 5Gbps"
    assert exported[0]["connect_tier"] == 4
    assert exported[1]["connect_speed"] is None
    assert exported[1]["connect_tier"] is None


def test_rate_label_is_exported_and_filterable():
    snapshot = parse_ioreg(archive(speed=480_000_000))
    hub = next(node for node in snapshot.nodes.values() if node.name == "Fast Hub")

    assert hub.id in matching_node_ids(snapshot, "480mbps")
    exported = next(
        node for node in snapshot_document(snapshot)["nodes"] if node["id"] == hub.id
    )
    assert exported["speed_name"] == "USB 480Mbps"


def test_hub_location_route_is_filterable_as_zero_padded_hex():
    snapshot = parse_ioreg(archive())
    hub = next(node for node in snapshot.nodes.values() if node.name == "Fast Hub")

    assert hub.id in matching_node_ids(snapshot, "01000000")


def test_display_export_filter_and_recursive_serial_redaction():
    snapshot = parse_ioreg(archive())
    controllers, displays = parse_displays(display_plist())
    snapshot = replace(snapshot, display_controllers=controllers, displays=displays)
    display = next(iter(displays.values()))

    assert display.id in matching_display_ids(snapshot, "120hz")
    assert display.id in matching_display_ids(snapshot, "internal")
    output = snapshot_json(snapshot, redact=True)
    assert "1234" not in output
    document = json.loads(output)
    assert document["displays"][0]["serial_number"].startswith("sha256:")


def test_thunderbolt_filter_keeps_matching_dock_and_bus_ancestor():
    snapshot = parse_ioreg(archive())
    nodes, roots = parse_thunderbolt(thunderbolt_plist())
    snapshot = replace(snapshot, thunderbolt_nodes=nodes, thunderbolt_roots=roots)
    bus = nodes[roots[0]]
    dock = nodes[bus.children[0]]

    assert matching_thunderbolt_ids(snapshot, "dock-1") == {bus.id, dock.id}
    assert matching_thunderbolt_ids(snapshot, "depth 1") == {bus.id, dock.id}
