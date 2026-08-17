import asyncio
from datetime import datetime, timezone

from cluesb.model import ClassificationConfidence, HealthStatus, NodeKind, UsbNode, UsbSnapshot
from cluesb.monitoring import TopologyMonitor


class BlockingCollector:
    def __init__(self):
        self.calls = 0
        self.release = asyncio.Event()

    async def snapshot(self):
        self.calls += 1
        await self.release.wait()
        return UsbSnapshot(datetime.now(timezone.utc), {}, ())


async def test_monitor_coalesces_overlapping_refreshes():
    collector = BlockingCollector()
    monitor = TopologyMonitor(collector)
    first = asyncio.create_task(monitor.refresh())
    await asyncio.sleep(0)
    assert await monitor.refresh() is False
    collector.release.set()
    assert await first is True
    assert collector.calls == 1


class SequenceCollector:
    def __init__(self):
        self.index = 0

    async def snapshot(self):
        self.index += 1
        return UsbSnapshot(datetime(2026, 1, self.index, tzinfo=timezone.utc), {}, ())


async def test_pause_buffers_latest_snapshot_until_resume():
    monitor = TopologyMonitor(SequenceCollector())
    await monitor.refresh()
    monitor.pause()
    await monitor.refresh()
    assert monitor.displayed.timestamp.day == 1
    monitor.resume()
    assert monitor.displayed.timestamp.day == 2


class FailingCollector:
    async def snapshot(self):
        raise RuntimeError("collector unavailable")


async def test_failure_is_recorded_without_escaping_monitor():
    monitor = TopologyMonitor(FailingCollector())
    assert await monitor.refresh() is False
    assert monitor.last_error == "collector unavailable"


class PartialCollector:
    async def snapshot(self):
        node = UsbNode("partial", "Partial", NodeKind.DEVICE, ClassificationConfidence.AUTHORITATIVE, None)
        return UsbSnapshot(datetime.now(timezone.utc), {"partial": node}, ("partial",))


async def test_persistent_incomplete_node_warns_only_after_three_scans():
    monitor = TopologyMonitor(PartialCollector())
    await monitor.refresh()
    await monitor.refresh()
    assert monitor.current.nodes["partial"].status is not HealthStatus.WARNING
    await monitor.refresh()
    assert monitor.current.nodes["partial"].status is HealthStatus.WARNING
    assert "persisted for 3 scans" in monitor.current.nodes["partial"].diagnostics[-1]
