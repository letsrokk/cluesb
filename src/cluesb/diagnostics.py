from __future__ import annotations

from dataclasses import replace

from cluesb.model import HealthStatus, NodeKind, UsbSnapshot


def diagnose_snapshot(snapshot: UsbSnapshot) -> UsbSnapshot:
    nodes = dict(snapshot.nodes)
    for node_id, node in snapshot.nodes.items():
        diagnostics: list[str] = []
        status = HealthStatus.OK
        if node.speed_bps is None and node.kind is not NodeKind.CONTROLLER:
            diagnostics.append("Operational link speed is unknown")
            status = HealthStatus.UNKNOWN
        if node.bcd_usb is not None and node.bcd_usb >= 0x0300 and node.speed_bps is not None and node.speed_bps <= 480_000_000:
            diagnostics.extend((
                "Possible speed downgrade",
                "Observed link at 480Mbps or below with USB 3.x descriptor capability; possible causes include an upstream USB 2-only path, cable or hub/KVM limitation, fallback enumeration, or device-specific behavior.",
            ))
            status = HealthStatus.WARNING
        if node.kind is NodeKind.UNKNOWN:
            diagnostics.append("Node type is incompletely described by macOS")
            if status is HealthStatus.OK:
                status = HealthStatus.INFO
        nodes[node_id] = replace(node, diagnostics=tuple(diagnostics), status=status)
    return replace(snapshot, nodes=nodes)
