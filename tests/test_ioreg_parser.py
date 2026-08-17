import plistlib

from cluesb.collectors.ioreg import parse_ioreg
from cluesb.model import ClassificationConfidence, NodeKind


def archive(*, speed: int = 5_000_000_000) -> bytes:
    root = {
        "IORegistryEntryName": "Root",
        "IORegistryPlanes": {"IOUSB": "IOUSB"},
        "IORegistryEntryChildren": [{
            "IORegistryEntryName": "XHCI",
            "IOObjectClass": "AppleUSBXHCI",
            "IORegistryEntryID": 100,
            "IOMatchCategory": "usb-host",
            "locationID": 0,
            "IORegistryEntryChildren": [{
                "IORegistryEntryName": "Fast Hub",
                "IOObjectClass": "IOUSBHostDevice",
                "IORegistryEntryID": 101,
                "locationID": 0x01000000,
                "idVendor": 0x1234,
                "idProduct": 0x5678,
                "bDeviceClass": 9,
                "UsbLinkSpeed": speed,
                "IORegistryEntryChildren": [{
                    "IORegistryEntryName": "Partial Device",
                    "IOObjectClass": "IOUSBHostDevice",
                    "IORegistryEntryID": 102,
                }],
            }],
        }],
    }
    return plistlib.dumps(root)


def test_parser_retains_partial_nodes_and_classifies_hub_authoritatively():
    snapshot = parse_ioreg(archive())
    hub = next(node for node in snapshot.nodes.values() if node.name == "Fast Hub")
    partial = next(node for node in snapshot.nodes.values() if node.name == "Partial Device")
    assert hub.kind is NodeKind.HUB
    assert hub.classification is ClassificationConfidence.AUTHORITATIVE
    assert hub.speed_bps == 5_000_000_000
    assert partial.vendor_id is None
    assert partial.kind is NodeKind.DEVICE


def test_parser_prefers_negotiated_link_speed_over_legacy_speed_fields():
    payload = plistlib.loads(archive())
    hub = payload["IORegistryEntryChildren"][0]["IORegistryEntryChildren"][0]
    hub["Device Speed"] = 3
    hub["USBSpeed"] = 4
    hub["UsbLinkSpeed"] = 5_000_000_000

    snapshot = parse_ioreg(plistlib.dumps(payload))
    parsed_hub = next(node for node in snapshot.nodes.values() if node.name == "Fast Hub")

    assert parsed_hub.speed_bps == 5_000_000_000
    assert parsed_hub.speed_name == "USB 5Gbps"
    assert parsed_hub.raw_speed == 5_000_000_000


def test_parser_ignores_legacy_device_speed_when_no_link_speed_is_reported():
    payload = plistlib.loads(archive())
    hub = payload["IORegistryEntryChildren"][0]["IORegistryEntryChildren"][0]
    hub.pop("UsbLinkSpeed")
    hub["Device Speed"] = 3

    snapshot = parse_ioreg(plistlib.dumps(payload))
    parsed_hub = next(node for node in snapshot.nodes.values() if node.name == "Fast Hub")

    assert parsed_hub.speed_bps is None
    assert parsed_hub.raw_speed is None


def test_parser_rejects_archive_without_usb_plane():
    data = plistlib.dumps({"IORegistryEntryName": "Root"})
    try:
        parse_ioreg(data)
    except ValueError as error:
        assert "IOUSB" in str(error)
    else:
        raise AssertionError("missing IOUSB plane was accepted")
