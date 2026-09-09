from idevicetail.device_names import device_kind, human_capacity, marketing_name


def test_marketing_name_known_and_unknown():
    assert marketing_name("iPhone11,8") == "iPhone XR"
    assert marketing_name("iPhone16,2") == "iPhone 15 Pro Max"
    assert marketing_name("iPad14,1") == "iPad mini (6th gen)"
    assert marketing_name("iPhone99,9") == "iPhone99,9"   # passthrough
    assert marketing_name("") == ""


def test_device_kind():
    assert device_kind("iPhone11,8") == "iphone"
    assert device_kind("iPad14,1") == "ipad"
    assert device_kind("iPod9,1") == "ipod"
    assert device_kind("Watch6,1") == "device"


def test_human_capacity():
    assert human_capacity(63_500_000_000) == "64 GB"
    assert human_capacity(250_000_000_000) == "256 GB"
    assert human_capacity(1_000_000_000_000) == "1 TB"
    assert human_capacity(None) == ""
    assert human_capacity("nope") == ""
