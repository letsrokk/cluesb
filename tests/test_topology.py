import plistlib

from cluesb.collectors.ioreg import parse_ioreg

from .test_ioreg_parser import archive


def test_calculates_tree_and_hub_depth_and_path_ceiling():
    snapshot = parse_ioreg(archive())
    hub = next(node for node in snapshot.nodes.values() if node.name == "Fast Hub")
    endpoint = next(node for node in snapshot.nodes.values() if node.name == "Partial Device")
    assert hub.tree_depth == 1
    assert hub.hub_depth == 1
    assert endpoint.tree_depth == 2
    assert endpoint.hub_depth == 1
    assert endpoint.path_ceiling_bps == 5_000_000_000
    assert snapshot.nodes[endpoint.controller_id].tree_depth == 0


def test_keeps_logical_companion_hubs_separate():
    root = plistlib.loads(archive())
    controller = root["IORegistryEntryChildren"][0]
    companion = dict(controller["IORegistryEntryChildren"][0])
    companion["IORegistryEntryID"] = 201
    companion["UsbLinkSpeed"] = 480_000_000
    companion["IORegistryEntryLocation"] = "USB2"
    companion["IORegistryEntryChildren"] = []
    controller["IORegistryEntryChildren"].append(companion)
    snapshot = parse_ioreg(plistlib.dumps(root))
    hubs = [node for node in snapshot.nodes.values() if node.name == "Fast Hub"]
    assert len(hubs) == 2
    assert len({node.id for node in hubs}) == 2
