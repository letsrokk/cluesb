from dataclasses import replace
from datetime import datetime, timezone

import pytest
from rich.style import Style
from textual.containers import VerticalScroll
from textual.widgets import RichLog

from cluesb.collectors.ioreg import parse_ioreg
from cluesb.model import ClassificationConfidence, HealthStatus, NodeKind, UsbSnapshot
from cluesb.tui.app import (
    CluesbApp, DisplayTree, TopologyTree, format_display_details,
    format_node_details, format_thunderbolt_details,
)
from cluesb.collectors.displays import parse_displays
from .test_displays import display_plist
from .test_displays import thunderbolt_plist
from cluesb.collectors.thunderbolt import parse_thunderbolt
from cluesb.model import CollectorMetadata
from cluesb.diff import EventKind, TopologyEvent

from .test_ioreg_parser import archive


class StaticMonitor:
    def __init__(self, snapshot):
        self.displayed = snapshot
        self.history = []
        self.last_error = None

    def pause(self):
        pass

    def resume(self):
        pass


def tree_click_offset(tree, branch, *, toggle):
    line_number = next(
        index for index, line in enumerate(tree._tree_lines) if line.node is branch
    )
    visible_line = line_number - int(tree.scroll_y)
    cell = 0
    for segment in tree.render_line(visible_line):
        metadata = segment.style.meta if segment.style else {}
        matches = (
            bool(metadata.get("toggle"))
            if toggle
            else "node" in metadata and not metadata.get("toggle")
        )
        if matches:
            content_x = tree.content_region.x - tree.region.x
            content_y = tree.content_region.y - tree.region.y
            return content_x + cell, content_y + visible_line
        cell += segment.cell_length
    raise AssertionError("click target was not rendered")


def test_app_uses_warm_periwinkle_theme():
    app = CluesbApp(interval=10)

    assert app.theme == "cluesb-warm-periwinkle"
    theme = app.get_theme(app.theme)
    assert theme is not None
    assert theme.primary == "#8B7CF6"
    assert theme.accent == "#8B7CF6"
    assert theme.secondary == "#6252CF"
    assert theme.background == "#121212"
    assert theme.warning == "#F2C94C"
    assert theme.error == "#FF5C5C"
    assert theme.success == "#4EBF71"
    assert theme.variables["footer-key-foreground"] == "#B9AFFF"


def test_empty_controller_uses_muted_idle_symbol_without_idle_text():
    controller = next(
        node
        for node in parse_ioreg(archive()).nodes.values()
        if node.kind is NodeKind.CONTROLLER
    )
    controller = replace(controller, children=())

    label = TopologyTree._label(controller)
    assert label.plain == f"○ {controller.name} — Host controller — Tier 1"
    assert label.spans[0].style == "#8B909A"


def test_populated_controller_label_describes_role_without_idle_state():
    controller = next(
        node
        for node in parse_ioreg(archive()).nodes.values()
        if node.kind is NodeKind.CONTROLLER and node.children
    )

    label = TopologyTree._label(controller).plain
    assert label.endswith(f"{controller.name} — Host controller — Tier 1")
    assert "Idle" not in label


def test_usb_labels_use_original_names_and_one_based_tiers():
    snapshot = parse_ioreg(archive())
    hub = next(node for node in snapshot.nodes.values() if node.kind is NodeKind.HUB)
    device = next(node for node in snapshot.nodes.values() if node.kind is NodeKind.DEVICE)

    assert TopologyTree._label(hub).plain == "✓ Fast Hub — USB 5Gbps — Tier 2"
    assert TopologyTree._label(device).plain == "? Partial Device — Unknown — Tier 3"
    assert "01000000" not in TopologyTree._label(hub).plain


@pytest.mark.parametrize(
    ("status", "marker", "color"),
    [
        (HealthStatus.OK, "✓", "#4EBF71"),
        (HealthStatus.WARNING, "⚠", "#F2C94C"),
        (HealthStatus.ERROR, "✗", "#FF5C5C"),
        (HealthStatus.INFO, "i", "#6CB6FF"),
        (HealthStatus.UNKNOWN, "?", "#8B909A"),
        (HealthStatus.DISCONNECTED, "○", "#8B909A"),
    ],
)
def test_tree_status_markers_have_semantic_symbols_and_colors(status, marker, color):
    node = next(iter(parse_ioreg(archive()).nodes.values()))

    label = TopologyTree._label(replace(node, status=status))

    assert label.plain.startswith(f"{marker} ")
    assert label.spans[0].style == color


def test_controller_details_show_only_controller_fields():
    snapshot = parse_ioreg(archive())
    controller = next(
        node for node in snapshot.nodes.values() if node.kind is NodeKind.CONTROLLER
    )

    details = format_node_details(controller, snapshot)

    assert "Controller:\n" in details
    assert "  Driver: AppleUSBXHCI" in details
    assert "  Connected nodes: 2" in details
    assert "  Active protocols: USB 3.x" in details
    assert "  Link speed: USB 5Gbps" in details
    assert "  Location ID: 0" in details
    assert "Link:" not in details
    assert "Topology:" not in details
    assert "Identity:" not in details
    assert "USB descriptor:" not in details
    assert "Unknown" not in details


def test_idle_controller_details_mark_zero_connected_nodes():
    snapshot = parse_ioreg(archive())
    controller = next(
        node for node in snapshot.nodes.values() if node.kind is NodeKind.CONTROLLER
    )
    controller = replace(controller, children=())

    details = format_node_details(controller, snapshot)
    assert "  Connected nodes: 0" in details
    assert "Idle" not in details


def test_active_controller_details_summarize_recursive_mixed_links():
    snapshot = parse_ioreg(archive())
    controller = next(
        node for node in snapshot.nodes.values() if node.kind is NodeKind.CONTROLLER
    )
    hub = snapshot.nodes[controller.children[0]]
    device = snapshot.nodes[hub.children[0]]
    nodes = dict(snapshot.nodes)
    nodes[hub.id] = replace(hub, speed_bps=480_000_000, speed_name="USB 480Mbps")
    nodes[device.id] = replace(
        device,
        speed_bps=10_000_000_000,
        speed_name="USB 10Gbps",
        bcd_usb=0x0310,
    )
    snapshot = replace(snapshot, nodes=nodes)

    details = format_node_details(controller, snapshot)

    assert "  Connected nodes: 2" in details
    assert "  Active protocols: USB 2.0, USB 3.x" in details
    assert "  Link speed: USB 10Gbps" in details
    assert "bcdUSB" not in details
    assert "  bcdUSB: 784" in format_node_details(nodes[device.id], snapshot)


def test_480mbps_rate_label_is_consistent_in_tree_details_and_controller_summary():
    snapshot = parse_ioreg(archive(speed=480_000_000))
    controller = next(
        node for node in snapshot.nodes.values() if node.kind is NodeKind.CONTROLLER
    )
    hub = next(node for node in snapshot.nodes.values() if node.kind is NodeKind.HUB)

    assert "USB 480Mbps" in TopologyTree._label(hub).plain
    assert "Link:\n  Speed: USB 480Mbps" in format_node_details(hub, snapshot)
    assert "  Link speed: USB 480Mbps" in format_node_details(controller, snapshot)


def test_active_controller_omits_link_summary_when_descendant_speeds_unknown():
    snapshot = parse_ioreg(archive())
    controller = next(
        node for node in snapshot.nodes.values() if node.kind is NodeKind.CONTROLLER
    )
    nodes = {
        node_id: replace(node, speed_bps=None, speed_name="Unknown")
        for node_id, node in snapshot.nodes.items()
    }
    snapshot = replace(snapshot, nodes=nodes)

    details = format_node_details(nodes[controller.id], snapshot)

    assert "  Connected nodes: 2" in details
    assert "Active protocols:" not in details
    assert "Link speed:" not in details


def test_hub_details_resolve_names_and_omit_missing_identity_fields():
    snapshot = parse_ioreg(archive())
    hub = next(node for node in snapshot.nodes.values() if node.kind is NodeKind.HUB)

    details = format_node_details(hub, snapshot)

    assert "Link:\n  Speed: USB 5Gbps" in details
    assert "  Controller: XHCI" in details
    assert "  Parent: XHCI" in details
    assert "Identity:\n  VID: 1234\n  PID: 5678" in details
    assert "Manufacturer:" not in details
    assert "Serial:" not in details
    assert "USB descriptor:\n  Class: 9" in details


def test_unknown_details_show_confidence_populated_evidence_and_raw_toggle():
    snapshot = parse_ioreg(archive())
    partial = next(
        node for node in snapshot.nodes.values() if node.name == "Partial Device"
    )
    unknown = replace(
        partial,
        kind=NodeKind.UNKNOWN,
        classification=ClassificationConfidence.UNKNOWN,
        manufacturer="Example",
        raw_properties={"Observed": True},
    )

    details = format_node_details(unknown, snapshot, show_raw=True)

    assert "Classification: unknown" in details
    assert "Identity:\n  Manufacturer: Example" in details
    assert "VID:" not in details
    assert "Raw IORegistry properties:" in details
    assert '"Observed": true' in details


def test_status_details_appear_immediately_after_state():
    snapshot = parse_ioreg(archive())
    hub = next(node for node in snapshot.nodes.values() if node.kind is NodeKind.HUB)
    warning = replace(
        hub,
        status=HealthStatus.WARNING,
        diagnostics=("Possible speed downgrade", "Observed evidence"),
    )

    details = format_node_details(warning, snapshot)

    assert details.index("State: warning") < details.index("Status details:")
    assert details.index("Status details:") < details.index("Link:")
    assert "  ⚠ Possible speed downgrade" in details
    assert "  ⚠ Observed evidence" in details


def test_warning_without_diagnostics_has_honest_fallback():
    snapshot = parse_ioreg(archive())
    hub = next(node for node in snapshot.nodes.values() if node.kind is NodeKind.HUB)
    warning = replace(hub, status=HealthStatus.WARNING, diagnostics=())

    details = format_node_details(warning, snapshot)

    assert "Status details:\n  ⚠ No diagnostic details were reported" in details


async def test_tui_has_expected_primary_widgets_and_pause_binding():
    app = CluesbApp(interval=10)
    async with app.run_test() as pilot:
        assert app.query_one("#topology")
        assert len(app.query("Tree")) == 1
        assert app.query_one("#details")
        assert app.query_one("#events")
        await pilot.press("space")
        assert app.paused is True


async def test_usb_and_displays_are_peer_roots_in_one_tree():
    snapshot = parse_ioreg(archive())
    controllers, displays = parse_displays(display_plist())
    monitor = StaticMonitor(replace(snapshot, display_controllers=controllers, displays=displays))
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test() as pilot:
        app._render_snapshot()
        await pilot.pause()
        tree = app.query_one("#topology", TopologyTree)

        assert [node.label.plain for node in tree.root.children] == [
            "Displays", "Thunderbolt / USB4", "USB Controllers",
        ]
        assert tree.root.children[0].children
        assert tree.root.children[2].children


def test_thunderbolt_labels_and_details_distinguish_capability_from_link_speed():
    snapshot = parse_ioreg(archive())
    nodes, roots = parse_thunderbolt(thunderbolt_plist())
    snapshot = replace(snapshot, thunderbolt_nodes=nodes, thunderbolt_roots=roots)
    bus = nodes[roots[0]]
    dock = nodes[bus.children[0]]

    assert TopologyTree._thunderbolt_label(bus, snapshot).plain == (
        "✓ Thunderbolt/USB4 Bus 0 — Up to 40 Gb/s capability · 2 connected"
    )
    assert TopologyTree._thunderbolt_label(dock, snapshot).plain == "✓ Example Dock — 40 Gb/s · depth 1"
    details = format_thunderbolt_details(dock, snapshot)
    assert "Role: device" in details
    assert "Parent: Thunderbolt/USB4 Bus 0" in details
    assert "Depth: 1" in details
    assert "Reported link speed: 40 Gb/s" in details
    assert "Capability value: No" in details


def test_idle_thunderbolt_bus_uses_muted_symbol_without_idle_text():
    nodes, roots = parse_thunderbolt(thunderbolt_plist())
    bus = replace(nodes[roots[0]], children=())

    label = TopologyTree._thunderbolt_label(bus)

    assert label.plain == "○ Thunderbolt/USB4 Bus 0 — capability Up to 40 Gb/s"
    assert label.spans[0].style == "#8B909A"

    snapshot = UsbSnapshot(
        datetime.now(timezone.utc), {}, (),
        thunderbolt_nodes={bus.id: bus}, thunderbolt_roots=(bus.id,),
    )
    details = format_thunderbolt_details(bus, snapshot)
    assert "State: ok" in details
    assert "Idle" not in details


async def test_thunderbolt_tree_reconciles_incrementally_and_marks_stale_root():
    snapshot = parse_ioreg(archive())
    nodes, roots = parse_thunderbolt(thunderbolt_plist())
    monitor = StaticMonitor(replace(snapshot, thunderbolt_nodes=nodes, thunderbolt_roots=roots))
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test() as pilot:
        app._render_snapshot()
        await pilot.pause()
        tree = app.query_one("#topology", TopologyTree)
        bus = tree.root.children[1].children[0]
        dock = bus.children[0]
        tree.move_cursor(dock)

        monitor.displayed = replace(
            monitor.displayed,
            thunderbolt_metadata=CollectorMetadata(warnings=("Stale cached data",)),
        )
        app._render_snapshot()
        await pilot.pause()

        assert tree.root.children[1].children[0] is bus
        assert bus.children[0] is dock
        assert tree.cursor_node is dock
        assert tree.root.children[1].label.plain == "⚠ Thunderbolt / USB4 — stale"
        assert tree.root.children[1].label.spans[0].style == "#F2C94C"


async def test_collapsed_top_level_roots_stay_collapsed_after_refresh():
    snapshot = parse_ioreg(archive())
    controllers, displays = parse_displays(display_plist())
    thunderbolt_nodes, thunderbolt_roots = parse_thunderbolt(thunderbolt_plist())
    monitor = StaticMonitor(replace(
        snapshot,
        display_controllers=controllers,
        displays=displays,
        thunderbolt_nodes=thunderbolt_nodes,
        thunderbolt_roots=thunderbolt_roots,
    ))
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test() as pilot:
        app._render_snapshot()
        await pilot.pause()
        tree = app.query_one("#topology", TopologyTree)
        displays_root, thunderbolt_root, usb_root = tree.root.children
        displays_root.collapse()
        thunderbolt_root.collapse()
        usb_root.collapse()

        app._render_snapshot()
        await pilot.pause()

        assert displays_root.is_expanded is False
        assert thunderbolt_root.is_expanded is False
        assert usb_root.is_expanded is False


async def test_events_match_topology_width_and_details_span_full_content_height():
    app = CluesbApp(interval=10)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        topology = app.query_one("#topology")
        events = app.query_one("#events")
        details = app.query_one("#details")

        assert topology.region.width == events.region.width
        assert details.region.y == topology.region.y
        assert details.region.bottom == events.region.bottom


async def test_scrollbars_are_automatic_for_all_scrollable_panels():
    app = CluesbApp(interval=10)
    async with app.run_test(size=(160, 40)):
        for selector in ("#topology", "#events", "#details"):
            assert app.query_one(selector).styles.overflow_y == "auto"


async def test_event_log_appends_without_flicker_and_preserves_manual_scroll():
    snapshot = parse_ioreg(archive())
    monitor = StaticMonitor(snapshot)
    monitor.history = [
        TopologyEvent(
            EventKind.CONNECTED,
            datetime(2026, 1, 1, 0, 0, index, tzinfo=timezone.utc),
            f"node-{index}",
            f"Device {index}",
            {},
        )
        for index in range(30)
    ]
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test(size=(100, 24)) as pilot:
        app._render_snapshot()
        await pilot.pause()
        events = app.query_one("#events", RichLog)
        assert events.max_scroll_y > 0
        events.scroll_to(y=0, animate=False, immediate=True)
        first_line = events.lines[0]

        monitor.history.append(TopologyEvent(
            EventKind.CONNECTED,
            datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
            "new-node",
            "New Device",
            {},
        ))
        app._render_snapshot()
        await pilot.pause()

        assert events.scroll_y == 0
        assert events.lines[0] is first_line
        assert any("New Device" in line.text for line in events.lines)


async def test_event_log_follows_new_events_only_when_already_at_bottom():
    snapshot = parse_ioreg(archive())
    monitor = StaticMonitor(snapshot)
    monitor.history = [
        TopologyEvent(
            EventKind.CONNECTED,
            datetime(2026, 1, 1, 0, 0, index, tzinfo=timezone.utc),
            f"node-{index}",
            f"Device {index}",
            {},
        )
        for index in range(30)
    ]
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test(size=(100, 24)) as pilot:
        app._render_snapshot()
        await pilot.pause()
        events = app.query_one("#events", RichLog)
        events.scroll_end(animate=False, immediate=True)

        monitor.history.append(TopologyEvent(
            EventKind.CONNECTED,
            datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
            "new-node",
            "New Device",
            {},
        ))
        app._render_snapshot()
        await pilot.pause()

        assert events.scroll_y == events.max_scroll_y


async def test_event_log_renders_captured_usb_connect_context_without_current_node():
    monitor = StaticMonitor(UsbSnapshot.empty())
    monitor.history = [TopologyEvent(
        EventKind.CONNECTED,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        "gone-device",
        "Logitech BRIO",
        {},
        connect_speed="USB 5Gbps",
        connect_tier=6,
    )]
    app = CluesbApp(interval=10, monitor=monitor)

    async with app.run_test() as pilot:
        app._render_snapshot()
        await pilot.pause()
        events = app.query_one("#events", RichLog)

        assert any(
            "CONNECTED    [usb] Logitech BRIO — Link: USB 5Gbps · Tier 6" in line.text
            for line in events.lines
        )


def test_display_label_and_details_keep_protocol_and_link_rate_separate():
    snapshot = parse_ioreg(archive())
    controllers, displays = parse_displays(display_plist())
    snapshot = replace(snapshot, display_controllers=controllers, displays=displays)
    display = next(iter(displays.values()))

    assert DisplayTree.display_label(display).plain == "✓ Color LCD — 1512 x 982 @ 120Hz · 3024 x 1964 px"
    details = format_display_details(display, snapshot)
    assert "Connection: Internal" in details
    assert "Display link speed: Not reported" in details
    assert "Transport depth: Not applicable" in details
    assert "Current mode: 1512 x 982 @ 120Hz" in details
    assert "Pixel dimensions: 3024 x 1964" in details
    assert "Bit depth: 30-bit" in details


async def test_display_tree_is_incremental_and_display_is_leaf():
    snapshot = parse_ioreg(archive())
    controllers, displays = parse_displays(display_plist())
    monitor = StaticMonitor(replace(snapshot, display_controllers=controllers, displays=displays))
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test() as pilot:
        app._render_snapshot()
        await pilot.pause()
        tree = app.query_one("#topology", TopologyTree)
        controller = tree.root.children[0].children[0]
        display = controller.children[0]
        tree.move_cursor(display)
        app._render_snapshot()
        await pilot.pause()

        assert tree.root.children[0].children[0] is controller
        assert controller.children[0] is display
        assert display.allow_expand is False
        assert tree.cursor_node is display


async def test_tree_uses_neutral_leaf_dot_and_triangles_only_for_expandable_nodes():
    monitor = StaticMonitor(parse_ioreg(archive()))
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test() as pilot:
        app._render_snapshot()
        await pilot.pause()
        tree = app.query_one("#topology", TopologyTree)
        controller = tree.root.children[2].children[0]
        hub = controller.children[0]
        device = hub.children[0]

        assert controller.allow_expand is True
        assert hub.allow_expand is True
        assert device.allow_expand is False
        rendered = tree.render_label(device, Style(), Style())
        assert rendered.plain.startswith("• ? ")


async def test_clicking_branch_label_selects_without_toggling_expansion():
    monitor = StaticMonitor(parse_ioreg(archive()))
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test() as pilot:
        app._render_snapshot()
        await pilot.pause()
        tree = app.query_one("#topology", TopologyTree)
        hub = tree.root.children[2].children[0].children[0]
        assert hub.is_expanded

        offset = tree_click_offset(tree, hub, toggle=False)
        clicked = await pilot.click(tree, offset=offset)
        await pilot.pause()

        assert clicked, (offset, tree.region, tree.content_region)
        assert tree.cursor_node is hub
        assert app._selected_id == hub.data
        assert hub.is_expanded

        app._render_snapshot()
        await pilot.pause()
        assert tree.cursor_node is hub
        assert hub.is_expanded


async def test_clicking_triangle_toggles_without_selecting_node():
    monitor = StaticMonitor(parse_ioreg(archive()))
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test() as pilot:
        app._render_snapshot()
        await pilot.pause()
        tree = app.query_one("#topology", TopologyTree)
        hub = tree.root.children[2].children[0].children[0]

        await pilot.click(tree, offset=tree_click_offset(tree, hub, toggle=True))
        await pilot.pause()

        assert not hub.is_expanded
        assert tree.cursor_node is None
        assert app._selected_id is None


async def test_clicking_leaf_selects_it_for_details():
    monitor = StaticMonitor(parse_ioreg(archive()))
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test() as pilot:
        app._render_snapshot()
        await pilot.pause()
        tree = app.query_one("#topology", TopologyTree)
        device = tree.root.children[2].children[0].children[0].children[0]
        tree.scroll_to_node(device, animate=False)
        await pilot.pause()

        await pilot.click(tree, offset=tree_click_offset(tree, device, toggle=False))
        await pilot.pause()

        assert tree.cursor_node is device
        assert app._selected_id == device.data
        assert not device.allow_expand


async def test_selecting_equivalent_usb_hubs_keeps_rows_visually_independent():
    snapshot = parse_ioreg(archive())
    controller = next(node for node in snapshot.nodes.values() if node.kind is NodeKind.CONTROLLER)
    source = next(node for node in snapshot.nodes.values() if node.kind is NodeKind.HUB)
    usb3 = replace(
        source, id="usb3-hub", name="Dual Hub USB 3", children=(),
        speed_bps=5_000_000_000, speed_name="USB 5Gbps",
        physical_identity="strong-hub-identity",
    )
    usb2 = replace(
        source, id="usb2-hub", name="Dual Hub USB 2", children=(),
        speed_bps=480_000_000, speed_name="USB 480Mbps",
        physical_identity="strong-hub-identity",
    )
    controller = replace(controller, children=(usb3.id, usb2.id))
    snapshot = replace(snapshot, nodes={controller.id: controller, usb3.id: usb3, usb2.id: usb2})
    monitor = StaticMonitor(snapshot)
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test() as pilot:
        app._render_snapshot()
        await pilot.pause()
        tree = app.query_one("#topology", TopologyTree)
        usb3_branch = tree._nodes_by_usb_id[usb3.id]
        usb2_branch = tree._nodes_by_usb_id[usb2.id]
        tree.move_cursor(usb3_branch)
        await pilot.pause()

        assert tree.cursor_node is usb3_branch
        assert tree.render_label(usb2_branch, Style(), Style()).plain.startswith("• ✓ ")
        assert "Name: Dual Hub USB 3" in str(app.query_one("#details-content").content)

        app._render_snapshot()
        await pilot.pause()

        assert tree._nodes_by_usb_id[usb3.id] is usb3_branch
        assert tree._nodes_by_usb_id[usb2.id] is usb2_branch
        assert tree.cursor_node is usb3_branch

        tree.move_cursor(usb2_branch)
        await pilot.pause()
        assert tree.cursor_node is usb2_branch
        assert tree.render_label(usb3_branch, Style(), Style()).plain.startswith("• ✓ ")
        assert "Name: Dual Hub USB 2" in str(app.query_one("#details-content").content)


async def test_enter_selects_and_toggles_highlighted_branch():
    monitor = StaticMonitor(parse_ioreg(archive()))
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test() as pilot:
        app._render_snapshot()
        await pilot.pause()
        tree = app.query_one("#topology", TopologyTree)
        hub = tree.root.children[2].children[0].children[0]
        tree.focus()
        tree.move_cursor(hub)

        await pilot.press("enter")
        await pilot.pause()

        assert not hub.is_expanded
        assert app._selected_id == hub.data


async def test_filter_leaf_transition_preserves_hub_and_cursor_then_restores_branch():
    monitor = StaticMonitor(parse_ioreg(archive()))
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test() as pilot:
        app._render_snapshot()
        await pilot.pause()
        tree = app.query_one("#topology", TopologyTree)
        hub = tree.root.children[2].children[0].children[0]
        tree.move_cursor(hub)

        app.filter_text = "Fast Hub"
        app._render_snapshot()
        await pilot.pause()

        assert tree.root.children[2].children[0].children[0] is hub
        assert tree.cursor_node is hub
        assert hub.allow_expand is False
        assert tree.render_label(hub, Style(), Style()).plain.startswith("• ✓ ")

        app.filter_text = ""
        app._render_snapshot()
        await pilot.pause()

        assert tree.root.children[2].children[0].children[0] is hub
        assert tree.cursor_node is hub
        assert hub.allow_expand is True


async def test_details_panel_scrolls_long_raw_content_with_keyboard():
    snapshot = parse_ioreg(archive())
    hub = next(node for node in snapshot.nodes.values() if node.kind is NodeKind.HUB)
    nodes = dict(snapshot.nodes)
    nodes[hub.id] = replace(
        hub,
        raw_properties={f"Property {index}": "value" * 8 for index in range(80)},
    )
    monitor = StaticMonitor(replace(snapshot, nodes=nodes))
    app = CluesbApp(interval=10, monitor=monitor)

    async with app.run_test(size=(80, 24)) as pilot:
        app._selected_id = hub.id
        app.show_raw = True
        app._render_details()
        await pilot.pause()
        details = app.query_one("#details", VerticalScroll)

        assert app.query_one("#details-content")
        assert details.max_scroll_y > 0
        details.focus()
        await pilot.press("end")
        await pilot.pause()
        assert details.scroll_y == details.max_scroll_y


async def test_details_scroll_is_preserved_for_refresh_and_raw_toggle_but_resets_for_selection():
    snapshot = parse_ioreg(archive())
    hub = next(node for node in snapshot.nodes.values() if node.kind is NodeKind.HUB)
    device = next(node for node in snapshot.nodes.values() if node.kind is NodeKind.DEVICE)
    nodes = dict(snapshot.nodes)
    nodes[hub.id] = replace(
        hub,
        status=HealthStatus.WARNING,
        diagnostics=tuple(f"Evidence line {index}" for index in range(80)),
        raw_properties={"Extra": "raw"},
    )
    monitor = StaticMonitor(replace(snapshot, nodes=nodes))
    app = CluesbApp(interval=10, monitor=monitor)

    async with app.run_test(size=(80, 24)) as pilot:
        app._selected_id = hub.id
        app._render_details()
        await pilot.pause()
        details = app.query_one("#details", VerticalScroll)
        details.scroll_to(y=5, animate=False, immediate=True)
        await pilot.pause()
        assert details.scroll_y == 5

        app._render_details()
        await pilot.pause()
        assert details.scroll_y == 5

        app.action_raw()
        await pilot.pause()
        assert details.scroll_y == 5

        tree = app.query_one("#topology", TopologyTree)
        app._render_snapshot()
        await pilot.pause()
        device_branch = tree._nodes_by_usb_id[device.id]
        tree.move_cursor(device_branch)
        await pilot.pause()
        assert details.scroll_y == 0


async def test_refresh_reuses_nodes_and_preserves_cursor_for_unchanged_topology():
    monitor = StaticMonitor(parse_ioreg(archive()))
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test() as pilot:
        app._render_snapshot()
        await pilot.pause()
        tree = app.query_one("#topology")
        hub = tree.root.children[2].children[0].children[0]
        hub.collapse()
        tree.move_cursor(hub)
        cursor_line = tree.cursor_line

        app._render_snapshot()
        await pilot.pause()

        assert tree.root.children[2].children[0].children[0] is hub
        assert tree.cursor_node is hub
        assert tree.cursor_line == cursor_line
        assert hub.is_expanded is False


async def test_label_only_change_updates_existing_tree_node():
    snapshot = parse_ioreg(archive())
    monitor = StaticMonitor(snapshot)
    app = CluesbApp(interval=10, monitor=monitor)
    async with app.run_test() as pilot:
        app._render_snapshot()
        await pilot.pause()
        tree = app.query_one("#topology")
        hub = tree.root.children[2].children[0].children[0]
        tree.move_cursor(hub)
        hub_id = hub.data
        nodes = dict(snapshot.nodes)
        nodes[hub_id] = replace(nodes[hub_id], speed_name="USB 10Gbps")
        monitor.displayed = replace(snapshot, nodes=nodes)

        app._render_snapshot()
        await pilot.pause()

        assert tree.root.children[2].children[0].children[0] is hub
        assert tree.cursor_node is hub
        assert "USB 10Gbps" in hub.label.plain
