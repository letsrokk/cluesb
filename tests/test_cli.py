import pytest

from cluesb.cli import build_parser, readable_tree
from cluesb.collectors.ioreg import parse_ioreg

from .test_ioreg_parser import archive
from .test_displays import display_plist, thunderbolt_plist
from cluesb.collectors.displays import parse_displays
from cluesb.collectors.thunderbolt import parse_thunderbolt
from dataclasses import replace


def test_cli_rejects_out_of_range_interval_and_conflicting_modes():
    parser = build_parser()
    assert parser.description == "Live macOS USB and display topology diagnostics"
    with pytest.raises(SystemExit):
        parser.parse_args(["--interval", "0.01"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--json", "--once"])


def test_readable_tree_shows_usb_tier_route_and_speed():
    output = readable_tree(parse_ioreg(archive()))
    assert "XHCI [Unknown] — Tier 1" in output
    assert "Fast Hub [USB 5Gbps] — Tier 2" in output
    assert "[01000000]" not in output
    assert "Partial Device [Unknown] — Tier 3" in output
    assert " H1" not in output
    assert "-- Depth" not in output


def test_readable_tree_uses_rate_label_for_480mbps_link():
    output = readable_tree(parse_ioreg(archive(speed=480_000_000)))
    assert "Fast Hub [USB 480Mbps] — Tier 2" in output
    assert "USB 2.0" not in output


def test_readable_tree_has_separate_display_section_and_unreported_link():
    snapshot = parse_ioreg(archive())
    controllers, displays = parse_displays(display_plist())
    output = readable_tree(replace(snapshot, display_controllers=controllers, displays=displays))

    assert "USB Controllers" in output
    assert "Displays" in output
    assert "Color LCD [1512 x 982 @ 120Hz; 3024 x 1964 pixels]" in output
    assert "Internal · depth N/A · link Not reported" in output


def test_readable_tree_has_thunderbolt_section_with_capability_and_depth():
    snapshot = parse_ioreg(archive())
    nodes, roots = parse_thunderbolt(thunderbolt_plist())
    output = readable_tree(replace(snapshot, thunderbolt_nodes=nodes, thunderbolt_roots=roots))

    assert "Thunderbolt / USB4" in output
    assert "Thunderbolt/USB4 Bus 0 [Up to 40 Gb/s capability · 2 connected · receptacle 1]" in output
    assert "Example Dock [40 Gb/s · depth 1]" in output
    assert "Display Adapter [20 Gb/s · depth 2]" in output
