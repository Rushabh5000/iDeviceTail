import pytest

from idevicetail.bus import LogBus
from idevicetail.config import Config
from idevicetail.engine_device import _parse_device_list
from idevicetail.manager import DeviceManager
from idevicetail.models import Device


def test_parse_device_list_json():
    txt = """[
      {"Identifier": "00008110-ABC", "DeviceName": "My iPhone", "ProductType": "iPhone16,1",
       "ProductVersion": "18.5", "ConnectionType": "Network", "NetworkAddress": "192.168.1.9"},
      {"Identifier": "00008110-ABC", "ConnectionType": "USB"}
    ]"""
    devs = _parse_device_list(txt)
    assert len(devs) == 1  # deduped by UDID
    d = devs[0]
    assert d.udid == "00008110-ABC" and d.name == "My iPhone"
    # a device seen on both transports -> USB wins for streaming, but the Wi-Fi
    # address is retained
    assert d.connection_type == "USB" and not d.wireless
    assert d.address == "192.168.1.9"


def test_parse_device_list_bonjour_mobdev2_shape():
    # `pymobiledevice3 bonjour mobdev2`: Identifier is the IP, real UDID is
    # UniqueDeviceID, plus an `ip` field, and NO ConnectionType.
    txt = """[
      {"BuildVersion": "22H355", "DeviceClass": "iPhone", "DeviceName": "Wireless iPhone",
       "Identifier": "192.168.1.110", "ProductType": "iPhone11,8", "ProductVersion": "18.7.9",
       "UniqueDeviceID": "00008020-DEADBEEF", "ip": "192.168.1.110"}
    ]"""
    devs = _parse_device_list(txt)
    assert len(devs) == 1
    d = devs[0]
    assert d.udid == "00008020-DEADBEEF"        # NOT the IP
    assert d.address == "192.168.1.110"
    assert d.name == "Wireless iPhone" and d.model == "iPhone11,8" and d.build == "22H355"


def test_parse_device_list_pymd11_real_shape():
    # exact shape from pymobiledevice3 11.x `usbmux list`
    txt = """[
        {
            "BuildVersion": "22H355",
            "ConnectionType": "USB",
            "DeviceClass": "iPhone",
            "DeviceName": "Rushabh\\u2019s iPhone",
            "Identifier": "00008020-001529DA3422402E",
            "ProductType": "iPhone11,8",
            "ProductVersion": "18.7.9",
            "UniqueDeviceID": "00008020-001529DA3422402E"
        }
    ]"""
    devs = _parse_device_list(txt)
    assert len(devs) == 1
    d = devs[0]
    assert d.udid == "00008020-001529DA3422402E"
    assert d.name == "Rushabh’s iPhone"
    assert d.model == "iPhone11,8"
    assert d.os_version == "18.7.9" and d.build == "22H355"
    assert d.connection_type == "USB" and not d.wireless


def test_parse_device_list_ignores_leading_banner():
    txt = "some warning text\nmore noise\n[{\"Identifier\": \"UDID1\", \"ConnectionType\": \"Network\"}]"
    devs = _parse_device_list(txt)
    assert devs and devs[0].udid == "UDID1" and devs[0].wireless


def test_parse_device_list_table_fallback():
    txt = (
        "Identifier: 00008030-XYZ\n"
        "DeviceName: iPad\n"
        "ProductVersion: 17.6\n"
        "ConnectionType: USB\n"
    )
    devs = _parse_device_list(txt)
    assert devs and devs[0].udid == "00008030-XYZ" and devs[0].name == "iPad"


def test_manager_merges_bonjour_into_paired_device_by_ip():
    m = DeviceManager(Config(), LogBus(), store=None)
    paired = Device(id="udidA", udid="udidA", name="iPhone", address="10.0.0.5")
    paired.transports.add("pymd-network")
    m.merge_device(paired)

    bonj = Device(id="_apple-mobdev2._tcp.local.|x", address="10.0.0.5")
    bonj.transports.add("bonjour-apple")
    merged = m.merge_device(bonj)

    assert merged.udid == "udidA"
    assert "bonjour-apple" in m.devices["udidA"].transports
    assert "pymd-network" in m.devices["udidA"].transports
    assert len(m.devices) == 1


def test_manager_folds_bonjour_when_pymd_arrives_second():
    m = DeviceManager(Config(), LogBus(), store=None)
    bonj = Device(id="_apple-mobdev2._tcp.local.|y", address="10.0.0.7")
    bonj.transports.add("bonjour-apple")
    m.merge_device(bonj)
    assert len(m.devices) == 1

    paired = Device(id="udidB", udid="udidB", name="iPad", address="10.0.0.7")
    paired.transports.add("pymd-network")
    m.merge_device(paired)

    assert len(m.devices) == 1
    dev = m.devices["udidB"]
    assert {"bonjour-apple", "pymd-network"} <= dev.transports


@pytest.mark.asyncio
async def test_start_capture_rejects_agent_only_device():
    m = DeviceManager(Config(), LogBus(), store=None)
    d = Device(id="agent-1", name="agent")
    d.transports.add("agent")
    m.merge_device(d)
    with pytest.raises(ValueError):
        await m.start_capture("agent-1", mode="syslog")


@pytest.mark.asyncio
async def test_start_capture_rejects_unpaired_bonjour_device():
    m = DeviceManager(Config(), LogBus(), store=None)
    d = Device(id="bonj-only", name="Apple device", address="10.0.0.9")
    d.transports.add("bonjour-apple")
    m.merge_device(d)
    with pytest.raises(ValueError, match="not paired"):
        await m.start_capture("bonj-only", mode="syslog")


@pytest.mark.asyncio
async def test_start_capture_wireless_device_uses_mobdev2(monkeypatch):
    m = DeviceManager(Config(), LogBus(), store=None)
    d = Device(id="udidW", udid="udidW", name="Wi-Fi iPhone", address="10.0.0.10")
    d.transports.add("pymd-network")
    m.merge_device(d)

    seen = {}

    async def fake_run(self):  # don't actually spawn pymobiledevice3
        seen["mobdev2"] = self.mobdev2
        self.desired = False

    monkeypatch.setattr("idevicetail.manager.CaptureTask._run", fake_run)
    ct = await m.start_capture("udidW", mode="syslog")
    await ct.task
    assert ct.mobdev2 is True and seen["mobdev2"] is True

    d.transports.add("pymd-usb")           # now also on USB -> prefer USB (no mobdev2)
    await m.stop_capture("udidW")
    ct2 = await m.start_capture("udidW", mode="oslog")
    await ct2.task
    assert ct2.mobdev2 is False
