from dataclasses import replace
from datetime import datetime, timedelta, timezone

from cluesb.diagnostics import diagnose_snapshot
from cluesb.diff import EventKind, TopologyDiffer
from cluesb.model import NodeKind, UsbNode, UsbSnapshot
from cluesb.collectors.displays import parse_displays
from .test_displays import display_plist


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def node(node_id="a", *, parent_id=None, speed=5_000_000_000, serial="SERIAL"):
    return UsbNode(
        id=node_id,
        name="Storage",
        kind=NodeKind.DEVICE,
        classification="authoritative",
        parent_id=parent_id,
        speed_bps=speed,
        speed_name="USB 5Gbps",
        serial_number=serial,
        vendor_id=0x1234,
        product_id=0x5678,
        bcd_usb=0x0300,
        physical_identity="physical",
    )


def snapshot(*nodes, at=NOW):
    return UsbSnapshot(at, {item.id: item for item in nodes}, tuple(item.id for item in nodes if item.parent_id is None))


def test_diff_reports_diagnostic_property_change():
    events = TopologyDiffer().diff(snapshot(node()), snapshot(node(speed=480_000_000), at=NOW + timedelta(seconds=1)))
    assert len(events) == 1
    assert events[0].kind is EventKind.CHANGED
    assert events[0].changes["speed_bps"] == (5_000_000_000, 480_000_000)


def test_usb_device_and_hub_connect_events_capture_speed_and_one_based_tier():
    device = replace(node("device"), tree_depth=5)
    hub = replace(node("hub"), kind=NodeKind.HUB, tree_depth=1)

    events = TopologyDiffer().diff(snapshot(), snapshot(device, hub, at=NOW + timedelta(seconds=1)))
    by_id = {event.node_id: event for event in events if event.kind is EventKind.CONNECTED}

    assert (by_id["device"].connect_speed, by_id["device"].connect_tier) == ("USB 5Gbps", 6)
    assert (by_id["hub"].connect_speed, by_id["hub"].connect_tier) == ("USB 5Gbps", 2)


def test_usb_connect_event_preserves_unknown_speed_but_other_events_have_no_context():
    device = replace(node(), speed_bps=None, speed_name="Unknown", tree_depth=2)
    connected = TopologyDiffer().diff(snapshot(), snapshot(device, at=NOW + timedelta(seconds=1)))[0]
    disconnected = TopologyDiffer().diff(snapshot(device), snapshot(at=NOW + timedelta(seconds=2)))[0]

    assert (connected.connect_speed, connected.connect_tier) == ("Unknown", 3)
    assert disconnected.connect_speed is None
    assert disconnected.connect_tier is None


def test_display_mode_change_emits_typed_display_event():
    controllers, displays = parse_displays(display_plist())
    before = UsbSnapshot(NOW, {}, (), display_controllers=controllers, displays=displays)
    display_id, display = next(iter(displays.items()))
    after = replace(before, timestamp=NOW + timedelta(seconds=1),
                    displays={display_id: replace(display, refresh_hz=60.0)})

    events = TopologyDiffer().diff(before, after)

    assert len(events) == 1
    assert events[0].resource_kind == "display"
    assert events[0].kind is EventKind.CHANGED
    assert events[0].changes["refresh_hz"] == (120.0, 60.0)


def test_non_usb_and_controller_connect_events_have_no_usb_context():
    controllers, displays = parse_displays(display_plist())
    before = UsbSnapshot(NOW, {}, (), display_controllers=controllers)
    display_events = TopologyDiffer().diff(
        before,
        replace(before, timestamp=NOW + timedelta(seconds=1), displays=displays),
    )
    controller = UsbNode("controller", "XHCI", NodeKind.CONTROLLER, "authoritative", None)
    controller_event = TopologyDiffer().diff(
        snapshot(), snapshot(controller, at=NOW + timedelta(seconds=1))
    )[0]

    assert all(event.connect_speed is None and event.connect_tier is None for event in display_events)
    assert controller_event.connect_speed is None
    assert controller_event.connect_tier is None


def test_diff_correlates_strong_identity_as_move():
    events = TopologyDiffer().diff(snapshot(node("old")), snapshot(node("new"), at=NOW + timedelta(seconds=1)))
    assert [event.kind for event in events] == [EventKind.MOVED]
    assert events[0].connect_speed is None
    assert events[0].connect_tier is None


def test_ambiguous_companion_identity_is_not_correlated_as_move():
    previous = snapshot(node("old-2"), node("old-3"))
    current = snapshot(node("new-2"), node("new-3"), at=NOW + timedelta(seconds=1))
    events = TopologyDiffer().diff(previous, current)
    assert EventKind.MOVED not in [event.kind for event in events]
    assert [event.kind for event in events].count(EventKind.DISCONNECTED) == 2
    assert [event.kind for event in events].count(EventKind.CONNECTED) == 2


def test_flapping_warning_after_three_transitions_in_ten_seconds():
    differ = TopologyDiffer()
    present = snapshot(node())
    empty = snapshot(at=NOW + timedelta(seconds=1))
    differ.diff(present, empty)
    differ.diff(empty, snapshot(node(), at=NOW + timedelta(seconds=2)))
    events = differ.diff(snapshot(node(), at=NOW + timedelta(seconds=2)), snapshot(at=NOW + timedelta(seconds=3)))
    assert events[-1].kind is EventKind.FLAPPING
    assert events[-1].transition_count == 3
    assert events[-1].connect_speed is None
    assert events[-1].connect_tier is None


def test_downgrade_is_inference_and_usb2_without_evidence_is_not_warning():
    capable = replace(node(speed=480_000_000), speed_name="USB 480Mbps")
    diagnosed = diagnose_snapshot(snapshot(capable))
    assert "Possible speed downgrade" in diagnosed.nodes["a"].diagnostics
    assert any("480Mbps or below" in item for item in diagnosed.nodes["a"].diagnostics)
    assert all("USB 2.0-or-lower" not in item for item in diagnosed.nodes["a"].diagnostics)
    ordinary = replace(capable, bcd_usb=0x0200)
    diagnosed = diagnose_snapshot(snapshot(ordinary))
    assert "Possible speed downgrade" not in diagnosed.nodes["a"].diagnostics


def test_authoritative_controller_without_link_speed_is_ok():
    controller = UsbNode(
        "controller",
        "XHCI",
        NodeKind.CONTROLLER,
        "authoritative",
        None,
    )
    diagnosed = diagnose_snapshot(snapshot(controller))
    assert diagnosed.nodes["controller"].status.value == "ok"
    assert "Operational link speed is unknown" not in diagnosed.nodes["controller"].diagnostics


def test_device_without_link_speed_remains_unknown():
    diagnosed = diagnose_snapshot(snapshot(node(speed=None)))
    assert diagnosed.nodes["a"].status.value == "unknown"
    assert "Operational link speed is unknown" in diagnosed.nodes["a"].diagnostics
