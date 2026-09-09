import time

import pytest

from idevicetail.models import Level
from idevicetail.normalize import (
    agent_payload_to_record,
    crash_to_record,
    oslog_json_to_record,
    parse_syslog_line,
    strip_ansi,
)

D = dict(device_id="udid1", device_name="iPhone")


def test_strip_ansi():
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"


@pytest.mark.parametrize(
    "line,proc,level,msg",
    [
        (
            "Jun 22 10:15:32 Johns-iPhone SpringBoard(FrontBoard)[63] <Notice>: hello world",
            "SpringBoard",
            Level.notice,
            "hello world",
        ),
        (
            "2026-06-22 10:15:32.123456 iPhone kernel[0] <Error>: something bad",
            "kernel",
            Level.error,
            "something bad",
        ),
        (
            "2026-06-22 10:15:32 iPhone MyApp[912] <Info>: [com.acme.net:HTTP] GET / -> 200",
            "MyApp",
            Level.info,
            "GET / -> 200",
        ),
    ],
)
def test_parse_syslog_line_variants(line, proc, level, msg):
    rec = parse_syslog_line(line, **D)
    assert rec is not None
    assert rec.process == proc
    assert rec.level == level
    assert rec.message == msg


def test_parse_syslog_pymobiledevice3_v11_braces():
    # pymobiledevice3 >= 11 text format: no host, {image} in braces
    line = "2026-09-09 00:54:19.046506 knowledgeconstructiond{CoreFoundation}[2123] <DEBUG>: looked up value"
    rec = parse_syslog_line(line, **D)
    assert rec is not None
    assert rec.process == "knowledgeconstructiond"
    assert rec.pid == 2123
    assert rec.level == Level.debug
    assert rec.message == "looked up value"


def test_oslog_json_record_from_pymd11():
    obj = {
        "pid": 68, "procid": 68, "thread_id": 198600,
        "timestamp": "2026-09-09T00:52:48.359607", "level": "DEBUG",
        "image_name": "/System/Library/PrivateFrameworks/CoreBrightness.framework/CoreBrightness",
        "filename": "/usr/libexec/backboardd",
        "message": "Display fade callback: updateBrightness = 0",
        "label": {"subsystem": "com.apple.CoreBrightness.Display", "category": "default"},
    }
    rec = oslog_json_to_record(obj, **D)
    assert rec.process == "backboardd"          # basename of filename
    assert rec.pid == 68
    assert rec.level == Level.debug
    assert rec.subsystem == "com.apple.CoreBrightness.Display"
    assert rec.category == "default"
    assert rec.message.startswith("Display fade")
    assert rec.source == "device-oslog"


def test_oslog_json_kernel_null_label():
    obj = {"pid": 0, "procid": 0, "timestamp": "2026-09-09T00:52:48.357515",
           "level": "NOTICE", "filename": "/kernel", "image_name": "",
           "message": "wlan0 stuff", "label": None}
    rec = oslog_json_to_record(obj, **D)
    assert rec.process == "kernel"
    assert rec.pid == 0
    assert rec.subsystem == "" and rec.category == ""
    assert rec.level == Level.notice


def test_parse_syslog_label_extracted():
    rec = parse_syslog_line(
        "2026-06-22 10:15:32 iPhone MyApp[912] <Info>: [com.acme.net:HTTP] body", **D
    )
    assert rec.subsystem == "com.acme.net"
    assert rec.category == "HTTP"


def test_parse_syslog_blank_returns_none():
    assert parse_syslog_line("   \n", **D) is None


def test_parse_syslog_garbage_is_kept_not_dropped():
    rec = parse_syslog_line("!!! not a real syslog line <Error> boom", **D)
    assert rec is not None
    assert rec.level == Level.error
    assert "boom" in rec.message
    assert rec.process in ("?", "")


def test_agent_payload_basic():
    obj = {
        "type": "log",
        "ts": 1_700_000_000.5,
        "level": "fault",
        "process": "IDeviceTailAgent",
        "pid": 42,
        "subsystem": "com.x",
        "category": "net",
        "message": "kaboom",
        "file": "Net.swift",
        "line": 10,
    }
    rec = agent_payload_to_record(obj, **D)
    assert rec.level == Level.fault
    assert rec.pid == 42
    assert rec.subsystem == "com.x"
    assert "kaboom" in rec.message and "Net.swift:10" in rec.message


def test_agent_payload_ms_timestamp_normalized():
    rec = agent_payload_to_record({"ts": 1_700_000_000_500, "message": "x"}, **D)
    assert 1_699_000_000 < rec.ts < 1_800_000_000


def test_agent_payload_bad_ts_falls_back_to_now():
    rec = agent_payload_to_record({"ts": "nope", "message": "x"}, **D)
    assert abs(rec.ts - time.time()) < 5


def test_crash_legacy():
    text = (
        "Incident Identifier: ABC\n"
        "Process:         MyApp [1234]\n"
        "Date/Time:       2026-06-22 10:15:32.000 +0000\n"
        "Exception Type:  EXC_BAD_ACCESS (SIGSEGV)\n"
    )
    rec = crash_to_record(text, filename="MyApp.crash", **D)
    assert rec.level == Level.fault
    assert rec.process == "MyApp"
    assert "EXC_BAD_ACCESS" in rec.message
    assert rec.subsystem == "crash"


def test_crash_ips_json_header():
    text = '{"app_name":"MyApp","bug_type":"309","captureTime":"2026-06-22 10:15:32.00 +0000"}\n{"exception":{"type":"EXC_CRASH"}}'
    rec = crash_to_record(text, filename="MyApp.ips", **D)
    assert rec.process == "MyApp"
    assert "EXC_CRASH" in rec.message
