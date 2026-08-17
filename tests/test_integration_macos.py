import platform

import pytest

from cluesb.collectors.ioreg import IoregCollector
from cluesb.collectors.system_profiler import SystemProfilerCollector
from cluesb.collectors.display_topology import DisplayTopologyCollector
from cluesb.model import NodeKind


pytestmark = [pytest.mark.integration, pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")]


async def test_live_collectors_produce_consistent_usb_topology_without_sudo():
    snapshot = await IoregCollector().snapshot()
    assert snapshot.roots
    assert any(snapshot.nodes[root].kind is NodeKind.CONTROLLER for root in snapshot.roots)
    for node in snapshot.nodes.values():
        assert node.parent_id is None or node.parent_id in snapshot.nodes
        assert all(child in snapshot.nodes for child in node.children)
    records = await SystemProfilerCollector().records()
    assert isinstance(records, list)


async def test_live_display_collector_includes_integrated_display_and_consistent_transport():
    snapshot = await DisplayTopologyCollector(IoregCollector()).snapshot()
    assert snapshot.display_controllers
    assert snapshot.displays
    assert any(display.connection_type.value == "Internal" for display in snapshot.displays.values())
    assert all(display.controller_id in snapshot.display_controllers for display in snapshot.displays.values())
    assert all(node.parent_id is None or node.parent_id in snapshot.thunderbolt_nodes
               for node in snapshot.thunderbolt_nodes.values())
