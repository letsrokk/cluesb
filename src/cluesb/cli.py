from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Sequence

from cluesb.collectors.ioreg import IoregCollector
from cluesb.collectors.system_profiler import EnrichedCollector
from cluesb.collectors.display_topology import DisplayTopologyCollector
from cluesb.diagnostics import diagnose_snapshot
from cluesb.export import snapshot_json
from cluesb.model import UsbSnapshot
from cluesb.presentation import usb_tier_suffix


def interval_value(value: str) -> float:
    try:
        interval = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("interval must be a number") from None
    if not 0.1 <= interval <= 10.0:
        raise argparse.ArgumentTypeError("interval must be between 0.1 and 10 seconds")
    return interval


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cluesb", description="Live macOS USB and display topology diagnostics"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help="print a machine-readable snapshot")
    mode.add_argument("--once", action="store_true", help="print a readable topology once")
    parser.add_argument("--interval", type=interval_value, default=0.5, help="refresh interval (0.1-10 seconds)")
    parser.add_argument("--redact", action="store_true", help="hash serial numbers in exports")
    parser.add_argument("--debug", action="store_true", help="show collector diagnostics on stderr")
    return parser


async def collect_snapshot() -> UsbSnapshot:
    collector = DisplayTopologyCollector(EnrichedCollector(IoregCollector()))
    return diagnose_snapshot(await collector.snapshot())


def readable_tree(snapshot: UsbSnapshot) -> str:
    lines: list[str] = ["USB Controllers"]

    def add(node_id: str, prefix: str, final: bool, root: bool = False) -> None:
        node = snapshot.nodes[node_id]
        branch = "" if root else ("└── " if final else "├── ")
        lines.append(
            f"  {prefix}{branch}{node.name} [{node.speed_name}]"
            f"{usb_tier_suffix(node)}"
        )
        children = [key for key in node.children if key in snapshot.nodes]
        child_prefix = prefix + ("" if root else ("    " if final else "│   "))
        for index, child_id in enumerate(children):
            add(child_id, child_prefix, index == len(children) - 1)

    for index, root_id in enumerate(snapshot.roots):
        add(root_id, "", index == len(snapshot.roots) - 1, root=True)
    lines.extend(("", "Displays"))
    for controller in snapshot.display_controllers.values():
        lines.append(f"  {controller.name} [Display controller]")
        children = [display_id for display_id in controller.children if display_id in snapshot.displays]
        for index, display_id in enumerate(children):
            display = snapshot.displays[display_id]
            mode = (f"{display.mode_width} x {display.mode_height}" if display.mode_width is not None else "Mode unknown")
            if display.refresh_hz is not None:
                mode += f" @ {display.refresh_hz:g}Hz"
            if display.pixel_width is not None:
                mode += f"; {display.pixel_width} x {display.pixel_height} pixels"
            depth = "N/A" if display.connection_type.value == "Internal" else (
                str(display.transport_depth) if display.transport_depth is not None else "Unresolved"
            )
            branch = "└──" if index == len(children) - 1 else "├──"
            lines.append(f"    {branch} {display.name} [{mode}]")
            lines.append(f"        {display.connection_type.value} · depth {depth} · link {display.link_speed}")
    lines.extend(("", "Thunderbolt / USB4"))

    def add_thunderbolt(node_id: str, prefix: str, final: bool) -> None:
        node = snapshot.thunderbolt_nodes[node_id]
        branch = "└── " if final else "├── "
        if node.parent_id is None:
            if node.children:
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
                description = f"{node.speed_text or 'Speed not reported'}"
                if node.speed_is_capability and node.speed_text:
                    description += " capability"
                description += f" · {connected} connected"
            else:
                description = (
                    f"capability {node.speed_text}"
                    if node.speed_text else "Capability not reported"
                )
            if node.receptacle_id:
                description += f" · receptacle {node.receptacle_id}"
        else:
            description = f"{node.speed_text or 'Speed not reported'} · depth {node.depth}"
        lines.append(f"  {prefix}{branch}{node.name} [{description}]")
        children = [child_id for child_id in node.children if child_id in snapshot.thunderbolt_nodes]
        child_prefix = prefix + ("    " if final else "│   ")
        for index, child_id in enumerate(children):
            add_thunderbolt(child_id, child_prefix, index == len(children) - 1)

    for index, root_id in enumerate(snapshot.thunderbolt_roots):
        if root_id in snapshot.thunderbolt_nodes:
            add_thunderbolt(root_id, "", index == len(snapshot.thunderbolt_roots) - 1)
    return "\n".join(lines)


async def _run_once(args: argparse.Namespace) -> int:
    try:
        snapshot = await collect_snapshot()
    except Exception as error:
        print(f"cluesb: {error}", file=sys.stderr)
        return 1
    if args.debug:
        print(f"command: {' '.join(snapshot.metadata.command)}", file=sys.stderr)
        print(f"duration: {snapshot.metadata.duration_seconds!r}s", file=sys.stderr)
        for warning in snapshot.metadata.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        for metadata in (snapshot.display_metadata, snapshot.thunderbolt_metadata):
            print(f"command: {' '.join(metadata.command)}", file=sys.stderr)
            print(f"duration: {metadata.duration_seconds!r}s", file=sys.stderr)
            for warning in metadata.warnings:
                print(f"warning: {warning}", file=sys.stderr)
    print(snapshot_json(snapshot, redact=args.redact) if args.json else readable_tree(snapshot))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.json or args.once:
        return asyncio.run(_run_once(args))
    from cluesb.tui.app import CluesbApp
    CluesbApp(interval=args.interval, redact=args.redact, debug=args.debug).run()
    return 0
