from datetime import datetime, timezone
from pathlib import Path
import plistlib

from cluesb.collectors.displays import correlate_display_transports, parse_displays
from cluesb.collectors.thunderbolt import parse_thunderbolt
from cluesb.model import ConnectionType, CorrelationConfidence, UsbSnapshot


def display_plist() -> bytes:
    return (Path(__file__).parent / "fixtures" / "displays.plist").read_bytes()


def thunderbolt_plist() -> bytes:
    return (Path(__file__).parent / "fixtures" / "thunderbolt.plist").read_bytes()


def test_parse_integrated_display_preserves_mode_and_identity():
    controllers, displays = parse_displays(display_plist())

    display = next(iter(displays.values()))
    assert display.controller_id in controllers
    assert display.connection_type is ConnectionType.INTERNAL
    assert (display.pixel_width, display.pixel_height) == (3024, 1964)
    assert (display.mode_width, display.mode_height) == (1512, 982)
    assert display.refresh_hz == 120.0
    assert display.bit_depth == 30
    assert display.is_main is True
    assert display.transport_depth is None


def test_parse_thunderbolt_preserves_hierarchy_and_distinguishes_capability():
    nodes, roots = parse_thunderbolt(thunderbolt_plist())

    bus = nodes[roots[0]]
    dock = nodes[bus.children[0]]
    assert bus.speed_text == "Up to 40 Gb/s"
    assert bus.speed_is_capability is True
    assert dock.parent_id == bus.id
    assert dock.depth == 1
    adapter = nodes[dock.children[0]]
    assert adapter.parent_id == dock.id
    assert adapter.depth == 2


def test_thunderbolt_prefers_connected_negotiated_speed_over_idle_capability():
    data = plistlib.dumps([{
        "_dataType": "SPThunderboltDataType",
        "_items": [{
            "_name": "Example Dock",
            "receptacle_upstream_tag": {
                "current_speed_key": "40 Gb/s",
                "receptacle_status_key": "receptacle_connected",
            },
            "receptacle_downstream_tag": {
                "current_speed_key": "Up to 80 Gb/s",
                "receptacle_status_key": "receptacle_no_devices_connected",
            },
        }],
    }], sort_keys=False)

    nodes, roots = parse_thunderbolt(data)
    dock = nodes[roots[0]]

    assert dock.speed_text == "40 Gb/s"
    assert dock.speed_is_capability is False


def test_transport_correlation_requires_explicit_shared_evidence():
    controllers, displays = parse_displays(display_plist())
    nodes, _ = parse_thunderbolt(thunderbolt_plist())
    snapshot = UsbSnapshot(
        datetime.now(timezone.utc), {}, (),
        display_controllers=controllers, displays=displays, thunderbolt_nodes=nodes,
    )

    correlated = correlate_display_transports(snapshot)
    display = next(iter(correlated.displays.values()))
    assert display.correlation is CorrelationConfidence.NONE
    assert display.transport_path == ()
    assert display.link_speed == "Not reported"
