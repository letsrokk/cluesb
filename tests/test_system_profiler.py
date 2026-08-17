import plistlib
import pytest

from cluesb.collectors.display_topology import DisplayTopologyCollector
from cluesb.collectors.system_profiler import parse_system_profiler
from cluesb.collectors.system_profiler import EnrichedCollector
from cluesb.model import UsbSnapshot
from datetime import datetime, timezone
from .test_displays import display_plist, thunderbolt_plist


def test_parses_nested_usb_profiler_devices_as_enrichment_records():
    data = plistlib.dumps([{"_dataType": "SPUSBHostDataType", "_items": [{
        "_name": "USB Bus", "_items": [{"_name": "Camera", "vendor_id": "0x1234", "product_id": "0x5678", "serial_num": "abc", "current_available": "500 mA"}]
    }]}])
    records = parse_system_profiler(data)
    assert records[0].name == "Camera"
    assert records[0].vendor_id == 0x1234
    assert records[0].power["current_available"] == "500 mA"


class FakeTopology:
    async def snapshot(self):
        return UsbSnapshot(datetime.now(timezone.utc), {}, ())


class FailingProfiler:
    async def records(self):
        raise RuntimeError("profiler unavailable")


async def test_enrichment_failure_does_not_block_primary_snapshot():
    snapshot = await EnrichedCollector(FakeTopology(), FailingProfiler()).snapshot()
    assert snapshot.nodes == {}
    assert "System Information enrichment failed" in snapshot.metadata.warnings[0]


class FakeProfilerData:
    def __init__(self):
        self.calls = []
        self.fail = set()

    async def collect(self, data_type):
        self.calls.append(data_type)
        if data_type in self.fail:
            raise RuntimeError("unavailable")
        return display_plist() if data_type == "SPDisplaysDataType" else thunderbolt_plist()


@pytest.mark.asyncio
async def test_display_sources_cache_independently_and_retain_stale_data():
    profiler = FakeProfilerData()
    now = [0.0]
    collector = DisplayTopologyCollector(FakeTopology(), profiler=profiler, clock=lambda: now[0])
    first = await collector.snapshot()
    assert len(first.displays) == 1
    assert len(first.thunderbolt_nodes) == 3
    await collector.snapshot()
    assert len(profiler.calls) == 2

    now[0] = 6.0
    profiler.fail.add("SPDisplaysDataType")
    stale = await collector.snapshot()
    assert len(stale.displays) == 1
    assert "stale" in stale.display_metadata.warnings[0].casefold()
