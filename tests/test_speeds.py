import pytest

from cluesb.speeds import normalize_speed


def test_normalizes_known_macos_speed_codes_and_text():
    assert normalize_speed(3).bps == 480_000_000
    assert normalize_speed("Up to 10 Gb/s").label == "USB 10Gbps"
    assert normalize_speed(20_000_000_000).label == "USB 20Gbps"


def test_unknown_speed_preserves_raw_value():
    speed = normalize_speed("mystery")
    assert speed.bps is None
    assert speed.label == "Unknown"
    assert speed.raw == "mystery"


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        (1, "USB 1.5Mbps"),
        (2, "USB 12Mbps"),
        (3, "USB 480Mbps"),
        (4, "USB 5Gbps"),
        (5, "USB 10Gbps"),
        (6, "USB 20Gbps"),
        ("480 Mbps", "USB 480Mbps"),
        ("5 Gbps", "USB 5Gbps"),
    ],
)
def test_known_speeds_use_rate_only_labels(raw, label):
    assert normalize_speed(raw).label == label
