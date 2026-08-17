from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.theme import Theme
from textual.widgets import Footer, Header, Input, RichLog, Static, Tree

from cluesb.collectors.ioreg import IoregCollector
from cluesb.collectors.system_profiler import EnrichedCollector
from cluesb.collectors.display_topology import DisplayTopologyCollector
from cluesb.diff import EventKind
from cluesb.export import snapshot_json
from cluesb.filtering import matching_display_ids, matching_node_ids, matching_thunderbolt_ids
from cluesb.model import (
    ConnectionType, DisplayNode, HealthStatus, NodeKind, ThunderboltNode,
    UsbNode, UsbSnapshot,
)
from cluesb.monitoring import TopologyMonitor
from cluesb.presentation import usb_tier_suffix
from cluesb.speeds import format_speed


WARM_PERIWINKLE_THEME = Theme(
    name="cluesb-warm-periwinkle",
    primary="#8B7CF6",
    secondary="#6252CF",
    accent="#8B7CF6",
    warning="#F2C94C",
    error="#FF5C5C",
    success="#4EBF71",
    foreground="#E0E0E0",
    background="#121212",
    surface="#1E1E1E",
    panel="#242F38",
    variables={
        "block-cursor-background": "#8B7CF6",
        "block-cursor-foreground": "#121212",
        "block-cursor-text-style": "none",
        "footer-key-foreground": "#B9AFFF",
        "input-selection-background": "#8B7CF6 35%",
        "border": "#8B7CF6",
        "border-blurred": "#6252CF",
    },
)


class TopologyTree(Tree[str]):
    """Tree whose space key is reserved for the application's pause action."""

    BINDINGS = [
        Binding("enter", "select_and_toggle", "Select")
        if binding.key == "enter" else binding
        for binding in Tree.BINDINGS
        if binding.key != "space"
    ]

    def __init__(self, label: str, **kwargs: Any) -> None:
        super().__init__(label, **kwargs)
        self.auto_expand = False
        self.show_root = False
        self._nodes_by_usb_id: dict[str, Any] = {}
        self._expansion_by_usb_id: dict[str, bool] = {}
        self._display_controllers: dict[str, Any] = {}
        self._display_nodes: dict[str, Any] = {}
        self._thunderbolt_nodes: dict[str, Any] = {}
        self._thunderbolt_expansion: dict[str, bool] = {}
        self._display_root = self.root.add("Displays", expand=True)
        self._thunderbolt_root = self.root.add("Thunderbolt / USB4", expand=True)
        self._usb_root = self.root.add("USB Controllers", expand=True)

    def action_select_and_toggle(self) -> None:
        node = self.cursor_node
        if node is not None:
            self._toggle_node(node)
            self.post_message(Tree.NodeSelected(node))

    @staticmethod
    def _label(node: UsbNode) -> Text:
        if node.kind is NodeKind.CONTROLLER and not node.children:
            marker, color = "○", "#8B909A"
        else:
            marker, color = {
                HealthStatus.OK: ("✓", "#4EBF71"),
                HealthStatus.WARNING: ("⚠", "#F2C94C"),
                HealthStatus.ERROR: ("✗", "#FF5C5C"),
                HealthStatus.INFO: ("i", "#6CB6FF"),
                HealthStatus.UNKNOWN: ("?", "#8B909A"),
                HealthStatus.DISCONNECTED: ("○", "#8B909A"),
            }[node.status]
        if node.kind is NodeKind.CONTROLLER:
            description = f"Host controller{usb_tier_suffix(node)}"
        else:
            description = f"{node.speed_name}{usb_tier_suffix(node)}"
        return Text.assemble(Text(marker, style=color), f" {node.name} — {description}")

    @staticmethod
    def _thunderbolt_label(node: ThunderboltNode, snapshot: UsbSnapshot | None = None) -> Text:
        if node.parent_id is None and not node.children:
            marker, color = "○", "#8B909A"
            description = ""
            if node.speed_text:
                description = f"capability {node.speed_text}"
            else:
                description = "Capability not reported"
        else:
            marker, color = "✓", "#4EBF71"
            if node.parent_id is None:
                description = node.speed_text or "Speed not reported"
                if node.speed_is_capability and node.speed_text:
                    description += " capability"
                connected = len(node.children)
                if snapshot is not None:
                    seen = {node.id}
                    pending = list(node.children)
                    connected = 0
                    while pending:
                        child_id = pending.pop()
                        if child_id in seen or child_id not in snapshot.thunderbolt_nodes:
                            continue
                        seen.add(child_id)
                        connected += 1
                        pending.extend(snapshot.thunderbolt_nodes[child_id].children)
                description += f" · {connected} connected"
            else:
                description = f"{node.speed_text or 'Speed not reported'} · depth {node.depth}"
        return Text.assemble(Text(marker, style=color), f" {node.name} — {description}")

    def render_label(self, node: Any, base_style: Any, style: Any) -> Text:
        label = super().render_label(node, base_style, style)
        if not node.allow_expand:
            label = Text.assemble(("• ", base_style), label)
        return label

    def _remove_indexed_branch(self, branch: Any) -> None:
        for child in tuple(branch.children):
            self._remove_indexed_branch(child)
        if branch.data:
            self._expansion_by_usb_id[branch.data] = branch.is_expanded
            self._nodes_by_usb_id.pop(branch.data, None)
        branch.remove()

    def reconcile(
        self,
        snapshot: UsbSnapshot,
        visible: set[str],
        visible_displays: set[str] | None = None,
        visible_thunderbolt: set[str] | None = None,
    ) -> None:
        """Apply a normalized snapshot without replacing retained TreeNode objects."""
        desired = visible & snapshot.nodes.keys()
        cursor_id = self.cursor_node.data if self.cursor_node is not None else None

        # Remove missing nodes and roots of subtrees whose parent changed. Removing a
        # branch also removes its descendants from the index; traversal below recreates
        # only the affected branch in its new location.
        for node_id, branch in tuple(self._nodes_by_usb_id.items()):
            if node_id not in self._nodes_by_usb_id:
                continue
            expected_parent = snapshot.nodes[node_id].parent_id if node_id in desired else None
            if expected_parent not in desired:
                expected_parent = None
            actual_parent = branch.parent.data if branch.parent is not None else None
            if node_id not in desired or actual_parent != expected_parent:
                self._remove_indexed_branch(branch)

        def ensure(parent: Any, node_id: str) -> None:
            if node_id not in desired:
                return
            model = snapshot.nodes[node_id]
            visible_children = tuple(child for child in model.children if child in desired)
            expandable = bool(visible_children)
            label = self._label(model)
            branch = self._nodes_by_usb_id.get(node_id)
            if branch is None:
                expanded = self._expansion_by_usb_id.get(node_id, True) if expandable else False
                branch = parent.add(
                    label,
                    data=node_id,
                    expand=expanded,
                    allow_expand=expandable,
                )
                self._nodes_by_usb_id[node_id] = branch
            elif branch.label.plain != label.plain:
                branch.set_label(label)
            if branch.allow_expand != expandable:
                if expandable:
                    branch.allow_expand = True
                    if self._expansion_by_usb_id.get(node_id, True):
                        branch.expand()
                else:
                    self._expansion_by_usb_id[node_id] = branch.is_expanded
                    branch.allow_expand = False
                    branch.collapse()
            for child_id in visible_children:
                ensure(branch, child_id)

        for root_id in snapshot.roots:
            ensure(self._usb_root, root_id)
        self._reconcile_displays(snapshot, visible_displays or set())
        self._reconcile_thunderbolt(snapshot, visible_thunderbolt or set())
        self.root.expand()

        if cursor_id in self._nodes_by_usb_id and self.cursor_node is not self._nodes_by_usb_id[cursor_id]:
            self.call_after_refresh(self.move_cursor, self._nodes_by_usb_id[cursor_id])

    def _reconcile_displays(self, snapshot: UsbSnapshot, visible: set[str]) -> None:
        cursor_id = self.cursor_node.data if self.cursor_node else None
        desired_controllers = {
            display.controller_id for display_id, display in snapshot.displays.items() if display_id in visible
        }
        for display_id, branch in tuple(self._display_nodes.items()):
            if display_id not in visible or display_id not in snapshot.displays:
                branch.remove()
                self._display_nodes.pop(display_id, None)
        for controller_id, branch in tuple(self._display_controllers.items()):
            if controller_id not in desired_controllers or controller_id not in snapshot.display_controllers:
                branch.remove()
                self._display_controllers.pop(controller_id, None)
        for controller_id, controller in snapshot.display_controllers.items():
            if controller_id not in desired_controllers:
                continue
            branch = self._display_controllers.get(controller_id)
            label = Text.assemble(Text("✓", style="#4EBF71"), f" {controller.name} — Display controller")
            if branch is None:
                branch = self._display_root.add(label, data=controller_id, expand=True)
                self._display_controllers[controller_id] = branch
            elif branch.label.plain != label.plain:
                branch.set_label(label)
            for display_id in controller.children:
                if display_id not in visible or display_id not in snapshot.displays:
                    continue
                display = snapshot.displays[display_id]
                child = self._display_nodes.get(display_id)
                label = DisplayTree.display_label(display)
                if child is None:
                    child = branch.add(label, data=display_id, allow_expand=False)
                    self._display_nodes[display_id] = child
                elif child.label.plain != label.plain:
                    child.set_label(label)
        indexed = self._display_nodes | self._display_controllers
        if cursor_id in indexed and self.cursor_node is not indexed[cursor_id]:
            self.call_after_refresh(self.move_cursor, indexed[cursor_id])

    def _remove_thunderbolt_branch(self, branch: Any) -> None:
        for child in tuple(branch.children):
            self._remove_thunderbolt_branch(child)
        if branch.data:
            self._thunderbolt_expansion[branch.data] = branch.is_expanded
            self._thunderbolt_nodes.pop(branch.data, None)
        branch.remove()

    def _reconcile_thunderbolt(self, snapshot: UsbSnapshot, visible: set[str]) -> None:
        stale = bool(snapshot.thunderbolt_metadata.warnings)
        root_label = Text.assemble(
            Text("⚠ ", style="#F2C94C"), "Thunderbolt / USB4 — stale"
        ) if stale else Text("Thunderbolt / USB4")
        if self._thunderbolt_root.label.plain != root_label.plain:
            self._thunderbolt_root.set_label(root_label)
        desired = visible & snapshot.thunderbolt_nodes.keys()
        cursor_id = self.cursor_node.data if self.cursor_node else None
        for node_id, branch in tuple(self._thunderbolt_nodes.items()):
            if node_id not in self._thunderbolt_nodes:
                continue
            expected_parent = snapshot.thunderbolt_nodes[node_id].parent_id if node_id in desired else None
            actual_parent = branch.parent.data if branch.parent else None
            if node_id not in desired or actual_parent != expected_parent:
                self._remove_thunderbolt_branch(branch)

        def ensure(parent: Any, node_id: str) -> None:
            if node_id not in desired:
                return
            model = snapshot.thunderbolt_nodes[node_id]
            children = tuple(child for child in model.children if child in desired)
            branch = self._thunderbolt_nodes.get(node_id)
            label = self._thunderbolt_label(model, snapshot)
            if branch is None:
                expandable = bool(children)
                branch = parent.add(
                    label, data=node_id, allow_expand=expandable,
                    expand=self._thunderbolt_expansion.get(node_id, True) if expandable else False,
                )
                self._thunderbolt_nodes[node_id] = branch
            elif branch.label.plain != label.plain:
                branch.set_label(label)
            expandable = bool(children)
            if branch.allow_expand != expandable:
                branch.allow_expand = expandable
                branch.expand() if expandable else branch.collapse()
            for child_id in children:
                ensure(branch, child_id)

        for root_id in snapshot.thunderbolt_roots:
            ensure(self._thunderbolt_root, root_id)
        if cursor_id in self._thunderbolt_nodes and self.cursor_node is not self._thunderbolt_nodes[cursor_id]:
            self.call_after_refresh(self.move_cursor, self._thunderbolt_nodes[cursor_id])


class DisplayTree(Tree[str]):
    """Incrementally reconciled display-controller hierarchy."""

    BINDINGS = [binding for binding in Tree.BINDINGS if binding.key != "space"]

    def __init__(self, label: str, **kwargs: Any) -> None:
        super().__init__(label, **kwargs)
        self._controllers: dict[str, Any] = {}
        self._displays: dict[str, Any] = {}

    @staticmethod
    def display_label(display: DisplayNode) -> Text:
        marker, color = {
            HealthStatus.OK: ("✓", "#4EBF71"), HealthStatus.WARNING: ("⚠", "#F2C94C"),
            HealthStatus.ERROR: ("✗", "#FF5C5C"), HealthStatus.INFO: ("i", "#6CB6FF"),
            HealthStatus.UNKNOWN: ("?", "#8B909A"), HealthStatus.DISCONNECTED: ("○", "#8B909A"),
        }[display.status]
        mode = f"{display.mode_width} x {display.mode_height}" if display.mode_width is not None else "Mode unknown"
        if display.refresh_hz is not None:
            mode += f" @ {display.refresh_hz:g}Hz"
        pixels = (f" · {display.pixel_width} x {display.pixel_height} px"
                  if display.pixel_width is not None else "")
        return Text.assemble(Text(marker, style=color), f" {display.name} — {mode}{pixels}")

    def render_label(self, node: Any, base_style: Any, style: Any) -> Text:
        label = super().render_label(node, base_style, style)
        if not node.allow_expand:
            return Text.assemble(("• ", base_style), label)
        return label

    def reconcile(self, snapshot: UsbSnapshot, visible: set[str]) -> None:
        cursor_id = self.cursor_node.data if self.cursor_node else None
        desired_controllers = {
            display.controller_id for display_id, display in snapshot.displays.items() if display_id in visible
        }
        for display_id, branch in tuple(self._displays.items()):
            if display_id not in visible or display_id not in snapshot.displays:
                branch.remove()
                self._displays.pop(display_id, None)
        for controller_id, branch in tuple(self._controllers.items()):
            if controller_id not in desired_controllers or controller_id not in snapshot.display_controllers:
                branch.remove()
                self._controllers.pop(controller_id, None)
        for controller_id, controller in snapshot.display_controllers.items():
            if controller_id not in desired_controllers:
                continue
            branch = self._controllers.get(controller_id)
            label = Text.assemble(Text("✓", style="#4EBF71"), f" {controller.name} — Display controller")
            if branch is None:
                branch = self.root.add(label, data=controller_id, expand=True)
                self._controllers[controller_id] = branch
            elif branch.label.plain != label.plain:
                branch.set_label(label)
            for display_id in controller.children:
                if display_id not in visible or display_id not in snapshot.displays:
                    continue
                display = snapshot.displays[display_id]
                child = self._displays.get(display_id)
                label = self.display_label(display)
                if child is None:
                    child = branch.add(label, data=display_id, allow_expand=False)
                    self._displays[display_id] = child
                elif child.label.plain != label.plain:
                    child.set_label(label)
        self.root.expand()
        indexed = self._displays | self._controllers
        if cursor_id in indexed and self.cursor_node is not indexed[cursor_id]:
            self.call_after_refresh(self.move_cursor, indexed[cursor_id])


def format_node_details(
    node: UsbNode,
    snapshot: UsbSnapshot,
    *,
    show_raw: bool = False,
) -> str:
    """Format fields relevant to the selected node's category."""
    lines = [
        f"Name: {node.name}",
        f"Kind: {node.kind.value}",
        f"State: {node.status.value}",
    ]
    if node.kind is NodeKind.UNKNOWN:
        lines.append(f"Classification: {node.classification.value}")

    status_details = node.diagnostics
    if not status_details and node.status in (HealthStatus.WARNING, HealthStatus.ERROR):
        status_details = ("No diagnostic details were reported",)
    if status_details:
        lines.extend(("", "Status details:"))
        lines.extend(f"  ⚠ {item}" for item in status_details)

    def add_section(title: str, fields: list[tuple[str, Any]]) -> None:
        populated = [(label, value) for label, value in fields if value is not None]
        if not populated:
            return
        lines.extend(("", f"{title}:"))
        lines.extend(f"  {label}: {value}" for label, value in populated)

    if node.kind is NodeKind.CONTROLLER:
        connected_count, protocols, fastest_speed = controller_activity(node, snapshot)
        connected = str(connected_count)
        add_section(
            "Controller",
            [
                ("Driver", node.registry_class),
                ("Connected nodes", connected),
                ("Active protocols", ", ".join(protocols) if protocols else None),
                ("Link speed", fastest_speed),
                ("Location ID", node.location_id),
                ("Registry path", node.registry_path),
            ],
        )
    else:
        controller = snapshot.nodes.get(node.controller_id) if node.controller_id else None
        parent = snapshot.nodes.get(node.parent_id) if node.parent_id else None
        add_section(
            "Link",
            [
                ("Speed", node.speed_name if node.speed_bps is not None else None),
                ("Raw", node.raw_speed),
            ],
        )
        add_section(
            "Topology",
            [
                ("Controller", controller.name if controller else None),
                ("Parent", parent.name if parent else None),
                ("Tree depth", node.tree_depth),
                ("Hub depth", node.hub_depth),
                ("Port", node.port),
                ("Location ID", node.location_id),
                ("Registry path", node.registry_path),
            ],
        )
        add_section(
            "Identity",
            [
                ("Manufacturer", node.manufacturer),
                ("VID", f"{node.vendor_id:04x}" if node.vendor_id is not None else None),
                ("PID", f"{node.product_id:04x}" if node.product_id is not None else None),
                ("Serial", node.serial_number),
            ],
        )
        add_section(
            "USB descriptor",
            [
                ("bcdUSB", node.bcd_usb),
                ("Class", node.device_class),
                ("Subclass", node.device_subclass),
                ("Protocol", node.device_protocol),
            ],
        )

    if show_raw:
        lines.extend(
            (
                "",
                "Raw IORegistry properties:",
                json.dumps(node.raw_properties, indent=2, default=str, sort_keys=True),
            )
        )
    return "\n".join(lines)


def format_display_details(display: DisplayNode, snapshot: UsbSnapshot, *, show_raw: bool = False) -> str:
    controller = snapshot.display_controllers.get(display.controller_id)
    current_mode = (f"{display.mode_width} x {display.mode_height}" if display.mode_width is not None else "Not reported")
    if display.refresh_hz is not None:
        current_mode += f" @ {display.refresh_hz:g}Hz"
    pixels = (f"{display.pixel_width} x {display.pixel_height}"
              if display.pixel_width is not None else "Not reported")
    if display.connection_type is ConnectionType.INTERNAL:
        depth = "Not applicable"
    else:
        depth = str(display.transport_depth) if display.transport_depth is not None else "Unresolved"
    path_names = [snapshot.thunderbolt_nodes[node_id].name for node_id in display.transport_path
                  if node_id in snapshot.thunderbolt_nodes]
    lines = [
        f"Name: {display.name}", "Kind: display", f"State: {display.status.value}",
        "", "Display:", f"  Controller: {controller.name if controller else 'Unknown'}",
        f"  Connection: {display.connection_type.value}",
        f"  Display link speed: {display.link_speed}",
        f"  Current mode: {current_mode}", f"  Pixel dimensions: {pixels}",
    ]
    if display.bit_depth is not None:
        lines.append(f"  Bit depth: {display.bit_depth}-bit")
    lines.extend((
        "", "Configuration:", f"  Main display: {display.is_main if display.is_main is not None else 'Not reported'}",
        f"  Mirrored: {display.is_mirrored if display.is_mirrored is not None else 'Not reported'}",
        f"  Online: {display.is_online if display.is_online is not None else 'Not reported'}",
        "", "Transport:", f"  Transport depth: {depth}",
        f"  Transport path: {' → '.join(path_names) if path_names else ('Not applicable' if display.connection_type is ConnectionType.INTERNAL else 'Unresolved')}",
        f"  Correlation: {display.correlation.value}",
    ))
    if path_names:
        speeds = [snapshot.thunderbolt_nodes[node_id].speed_text for node_id in display.transport_path
                  if node_id in snapshot.thunderbolt_nodes and snapshot.thunderbolt_nodes[node_id].speed_text]
        lines.append(f"  Thunderbolt path speed: {' → '.join(speeds) if speeds else 'Not reported'}")
    if display.vendor_id is not None or display.product_id is not None or display.serial_number:
        lines.extend(("", "Identity:"))
        if display.vendor_id is not None:
            lines.append(f"  Vendor ID: {display.vendor_id:x}")
        if display.product_id is not None:
            lines.append(f"  Product ID: {display.product_id:x}")
        if display.serial_number:
            lines.append(f"  Serial: {display.serial_number}")
    if display.diagnostics:
        lines[3:3] = ["", "Status details:", *(f"  ⚠ {item}" for item in display.diagnostics)]
    if show_raw:
        lines.extend(("", "Raw System Information properties:",
                      json.dumps(display.raw_properties, indent=2, default=str, sort_keys=True)))
    return "\n".join(lines)


def format_thunderbolt_details(
    node: ThunderboltNode,
    snapshot: UsbSnapshot,
    *,
    show_raw: bool = False,
) -> str:
    parent = snapshot.thunderbolt_nodes.get(node.parent_id) if node.parent_id else None
    children = [snapshot.thunderbolt_nodes[child_id].name for child_id in node.children
                if child_id in snapshot.thunderbolt_nodes]
    role = "host bus" if node.parent_id is None else "device"
    lines = [
        f"Name: {node.name}", "Kind: thunderbolt", "State: ok",
        "", "Thunderbolt / USB4:", f"  Role: {role}",
        f"  Parent: {parent.name if parent else 'None'}",
        f"  Connected children: {', '.join(children) if children else 'None'}",
        f"  Depth: {node.depth}",
        f"  Receptacle: {node.receptacle_id or 'Not reported'}",
        f"  Route: {node.route or 'Not reported'}",
        f"  Domain: {node.domain_uuid or 'Not reported'}",
        f"  Device UID: {node.device_uid or 'Not reported'}",
        f"  Reported link speed: {node.speed_text or 'Not reported'}",
        f"  Capability value: {'Yes' if node.speed_is_capability else 'No'}",
    ]
    if show_raw:
        lines.extend(("", "Raw System Information properties:",
                      json.dumps(node.raw_properties, indent=2, default=str, sort_keys=True)))
    return "\n".join(lines)


def controller_activity(
    controller: UsbNode,
    snapshot: UsbSnapshot,
) -> tuple[int, tuple[str, ...], str | None]:
    """Summarize resolved descendants and their observed operational links."""
    seen = {controller.id}
    pending = list(controller.children)
    connected_count = 0
    protocols: set[str] = set()
    speeds: list[int] = []

    while pending:
        node_id = pending.pop()
        if node_id in seen:
            continue
        descendant = snapshot.nodes.get(node_id)
        if descendant is None:
            continue
        seen.add(node_id)
        connected_count += 1
        pending.extend(descendant.children)
        if descendant.speed_bps is None:
            continue
        speeds.append(descendant.speed_bps)
        if descendant.speed_bps in (1_500_000, 12_000_000):
            protocols.add("USB 1.x")
        elif descendant.speed_bps == 480_000_000:
            protocols.add("USB 2.0")
        elif descendant.speed_bps in (5_000_000_000, 10_000_000_000, 20_000_000_000):
            protocols.add("USB 3.x")

    ordered_protocols = tuple(
        protocol for protocol in ("USB 1.x", "USB 2.0", "USB 3.x") if protocol in protocols
    )
    fastest_speed = format_speed(max(speeds)) if speeds else None
    return connected_count, ordered_protocols, fastest_speed


class CluesbApp(App[None]):
    TITLE = "cluesb — USB, Thunderbolt & Display Topology"
    CSS = """
    #main { height: 1fr; }
    #left-pane { width: 3fr; }
    #topology { width: 1fr; height: 2fr; border: round $accent; overflow-y: auto; }
    #events { width: 1fr; height: 1fr; border: round $accent; overflow-y: auto; }
    #details { width: 2fr; height: 1fr; border: round $accent; padding: 0 1; overflow-y: auto; }
    #details-content { width: 1fr; height: auto; }
    #filter { display: none; dock: top; }
    .visible#filter { display: block; }
    Screen.narrow #main { layout: vertical; }
    Screen.narrow #left-pane, Screen.narrow #details { width: 1fr; height: 1fr; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("space", "toggle_pause", "Pause", priority=True),
        Binding("f", "filter", "Filter"),
        Binding("e", "export", "Export"),
        Binding("question_mark", "help", "Help"),
        Binding("x", "raw", "Raw"),
    ]

    def __init__(self, *, interval: float = 0.5, redact: bool = False, debug: bool = False,
                 monitor: TopologyMonitor | None = None) -> None:
        super().__init__()
        self.register_theme(WARM_PERIWINKLE_THEME)
        self.theme = WARM_PERIWINKLE_THEME.name
        self.interval = interval
        self.redact = redact
        self.debug_enabled = debug
        self.monitor = monitor or TopologyMonitor(DisplayTopologyCollector(EnrichedCollector(IoregCollector())))
        self.paused = False
        self.show_raw = False
        self.filter_text = ""
        self._selected_id: str | None = None
        self._event_log_initialized = False
        self._last_event_key: tuple[Any, ...] | None = None
        self._rendered_notice_keys: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Input(
            placeholder="Filter USB, displays, Thunderbolt, identity, route, or speed",
            id="filter",
        )
        with Horizontal(id="main"):
            with Vertical(id="left-pane"):
                yield TopologyTree("cluesb", id="topology")
                yield RichLog(
                    id="events", markup=True, wrap=True,
                    auto_scroll=False, max_lines=4000,
                )
            with VerticalScroll(id="details"):
                yield Static("Select a node to inspect it", id="details-content")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#topology", Tree).focus()
        self.set_interval(self.interval, self._scheduled_refresh)

    def on_resize(self, event: Any) -> None:
        self.screen.set_class(event.size.width < 90, "narrow")

    async def _scheduled_refresh(self) -> None:
        if await self.monitor.refresh():
            self._render_snapshot()
        elif self.monitor.last_error:
            events = self.query_one("#events", RichLog)
            at_end = events.max_scroll_y == 0 or events.scroll_y >= events.max_scroll_y
            events.write(f"[red]ERROR[/] {self.monitor.last_error}", scroll_end=at_end)

    @staticmethod
    def _event_key(event: Any) -> tuple[Any, ...]:
        return (
            event.timestamp, event.kind, event.resource_kind, event.node_id,
            event.previous_node_id, event.transition_count,
            event.connect_speed, event.connect_tier, repr(event.changes),
        )

    def _render_events(self, snapshot: UsbSnapshot) -> None:
        events = self.query_one("#events", RichLog)
        history = list(self.monitor.history)
        history_keys = [self._event_key(event) for event in history]
        at_end = events.max_scroll_y == 0 or events.scroll_y >= events.max_scroll_y
        start = 0

        if self._event_log_initialized and self._last_event_key is not None:
            try:
                last_index = max(
                    index for index, key in enumerate(history_keys)
                    if key == self._last_event_key
                )
            except ValueError:
                events.clear()
                self._rendered_notice_keys.clear()
            else:
                start = last_index + 1
        elif self._event_log_initialized and not history:
            start = 0

        notices: list[tuple[str, str]] = []
        if self.debug_enabled:
            command = " ".join(snapshot.metadata.command) or "unknown"
            notices.extend((
                (f"debug-command:{command}", f"[dim]DEBUG command: {command}[/]"),
                (
                    f"debug-duration:{snapshot.timestamp.isoformat()}",
                    f"[dim]DEBUG duration: {snapshot.metadata.duration_seconds!r}s[/]",
                ),
            ))
            for warning in snapshot.metadata.warnings:
                notices.append((f"debug-usb-warning:{warning}", f"[yellow]DEBUG warning: {warning}[/]"))
            for metadata in (snapshot.display_metadata, snapshot.thunderbolt_metadata):
                for warning in metadata.warnings:
                    notices.append((f"debug-warning:{metadata.source}:{warning}",
                                    f"[yellow]DEBUG warning: {warning}[/]"))
        else:
            for metadata in (snapshot.display_metadata, snapshot.thunderbolt_metadata):
                for warning in metadata.warnings:
                    notices.append((f"stale:{metadata.source}:{warning}", f"[yellow]STALE[/] {warning}"))

        wrote = False
        for key, line in notices:
            if key not in self._rendered_notice_keys:
                events.write(line, scroll_end=False)
                self._rendered_notice_keys.add(key)
                wrote = True
        for event in history[start:]:
            detail = ", ".join(
                f"{key}: {before!r} → {after!r}"
                for key, (before, after) in event.changes.items()
            )
            line = (
                f"{event.timestamp:%H:%M:%S.%f}"[:-3]
                + f" {event.kind.value.upper():12} [{event.resource_kind}] {event.name}"
                + (
                    f" — Link: {event.connect_speed} · Tier {event.connect_tier}"
                    if event.kind is EventKind.CONNECTED
                    and event.connect_speed is not None
                    and event.connect_tier is not None
                    else ""
                )
                + (f" — {detail}" if detail else "")
            )
            events.write(Text(line), scroll_end=False)
            wrote = True
        self._last_event_key = history_keys[-1] if history_keys else None
        self._event_log_initialized = True
        if wrote and at_end:
            events.scroll_end(animate=False)

    def _render_snapshot(self) -> None:
        snapshot = self.monitor.displayed
        visible = matching_node_ids(snapshot, self.filter_text)
        tree = self.query_one("#topology", TopologyTree)
        tree.reconcile(
            snapshot,
            visible,
            matching_display_ids(snapshot, self.filter_text),
            matching_thunderbolt_ids(snapshot, self.filter_text),
        )
        self._render_events(snapshot)
        if self.paused:
            self.sub_title = "PAUSED"
        elif snapshot.metadata.duration_seconds is not None:
            self.sub_title = f"scan {snapshot.metadata.duration_seconds:.3f}s"
        self._render_details()

    def on_tree_node_selected(self, event: Tree.NodeSelected[str]) -> None:
        if event.node.data:
            self._select_node(event.node.data)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[str]) -> None:
        if event.node.data:
            self._select_node(event.node.data)

    def _select_node(self, node_id: str) -> None:
        selection_changed = node_id != self._selected_id
        self._selected_id = node_id
        self._render_details()
        if selection_changed:
            details = self.query_one("#details", VerticalScroll)
            details.scroll_to(y=0, animate=False, immediate=True)
            self.call_after_refresh(details.scroll_home, animate=False)

    def _render_details(self) -> None:
        if not self._selected_id:
            return
        snapshot = self.monitor.displayed
        if self._selected_id in snapshot.nodes:
            content = format_node_details(snapshot.nodes[self._selected_id], snapshot, show_raw=self.show_raw)
        elif self._selected_id in snapshot.displays:
            content = format_display_details(snapshot.displays[self._selected_id], snapshot, show_raw=self.show_raw)
        elif self._selected_id in snapshot.display_controllers:
            controller = snapshot.display_controllers[self._selected_id]
            content = f"Name: {controller.name}\nKind: display controller\n\nController:\n  Bus: {controller.bus or 'Not reported'}\n  Connected displays: {len(controller.children)}"
            if self.show_raw:
                content += "\n\nRaw System Information properties:\n" + json.dumps(
                    controller.raw_properties, indent=2, default=str, sort_keys=True
                )
        elif self._selected_id in snapshot.thunderbolt_nodes:
            content = format_thunderbolt_details(
                snapshot.thunderbolt_nodes[self._selected_id], snapshot, show_raw=self.show_raw
            )
        else:
            return
        self.query_one("#details-content", Static).update(content)

    async def action_refresh(self) -> None:
        await self._scheduled_refresh()

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self.monitor.pause() if self.paused else self.monitor.resume()
        self.sub_title = "PAUSED" if self.paused else ""
        if not self.paused:
            self._render_snapshot()

    def action_filter(self) -> None:
        widget = self.query_one("#filter", Input)
        widget.add_class("visible")
        widget.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.filter_text = event.value
        event.input.remove_class("visible")
        self._render_snapshot()

    def action_raw(self) -> None:
        self.show_raw = not self.show_raw
        self._render_details()

    def action_export(self) -> None:
        path = Path.cwd() / f"cluesb-{datetime.now():%Y%m%d-%H%M%S}.json"
        try:
            path.write_text(snapshot_json(self.monitor.displayed, events=tuple(self.monitor.history), redact=self.redact) + "\n")
        except OSError as error:
            self.notify(f"Export failed: {error}", severity="error")
        else:
            self.notify(f"Exported {path.name}")

    def action_help(self) -> None:
        self.notify(
            "q quit · r refresh · space pause · f filter · e export · x raw · tab focus · pgup/pgdn scroll · ? help",
            timeout=8,
        )
