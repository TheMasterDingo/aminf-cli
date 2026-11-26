"""Byte-level tests for the .97 / .100R2 protocol. Replies are real captures
from the owner's .100R2 over its dongle (2026-11-27)."""
import pytest

from aminf.errors import AmError, Refused
from aminf.race.codec import (FIELDS, Frame, FrameReader, frame, parse_battery,
                              parse_dpi, parse_key, parse_nv, read_nv,
                              save_dpi_colour, set_dpi_stage, sleep_record_r2)

H = bytes.fromhex


def test_frame_get_profile():
    assert frame(0x80, 12489) == H("0680055a0200c930").ljust(61, b"\0")


def test_frame_with_data():
    assert frame(0x00, 12492, b"\x02")[:9] == H("0700055a0300cc3002")


@pytest.mark.parametrize("cmd", [12494, 12395, 12394, 4353, 11442, 11408, 7170,
                                 1028, 12490, 99999])
def test_dangerous_and_unknown_ids_refused(cmd):
    with pytest.raises(Refused):
        frame(0x80, cmd)


def test_nv_writes_are_pinned():
    frame(0x80, 2573, H("0062") + bytes(10))            # light pattern: allowed
    frame(0x80, 2573, H("2862") + bytes(10))            # last DPI colour: allowed
    with pytest.raises(Refused):
        frame(0x80, 2573, H("0a60") + bytes(10))
    with pytest.raises(Refused):
        frame(0x80, 2573, H("2962") + bytes(10))
    with pytest.raises(Refused):
        frame(0x80, 2561, H("0160") + bytes(16))
    with pytest.raises(Refused):
        frame(0x80, 2561, H("0060") + bytes(15))
    frame(0x80, 2561, H("0060") + bytes(16))


def test_oversized_payload_is_an_error():
    with pytest.raises(AmError):
        frame(0x80, 12482, bytes(60))


PROFILE = H("140880055b0400c9300001")
BATTERY = H("140d80055b0900cf3000003e64011801")


def test_reader_skips_stale_reply():
    r = FrameReader()
    assert [f.cmd for f in r.feed(BATTERY) + r.feed(PROFILE)] == [12495, 12489]


def test_reader_joins_split_frame():
    body = PROFILE[3:]                       # the 8 frame bytes
    a = bytes([0x14, 5, 0x80]) + body[:5]
    b = bytes([0x14, 3, 0x80]) + body[5:]
    r = FrameReader()
    assert r.feed(a) == []
    assert r.feed(b) == [Frame(12489, 0x5B, H("0001"))]


def test_reader_splits_stacked_frames():
    stacked = bytes([0x14, 16, 0x80]) + PROFILE[3:] + PROFILE[3:]
    assert len(FrameReader().feed(stacked)) == 2


def test_reader_ignores_empty_report():
    assert FrameReader().feed(bytes(62)) == []


def test_reader_resyncs_after_garbage():
    junk = bytes([0x14, 10, 0x80, 0xAA, 0xBB]) + PROFILE[3:]
    assert FrameReader().feed(junk) == [Frame(12489, 0x5B, H("0001"))]


def test_parse_dpi():
    d = parse_dpi(H("000803" + "90012003b0044006800c001910273075" * 2))
    assert (d["count"], d["active"]) == (8, 3)
    assert d["x"] == d["y"] == [400, 800, 1200, 1600, 3200, 6400, 10000, 30000]


def test_set_dpi_stage():
    assert set_dpi_stage(2, 1600, 1600) == H("0240064006")


def test_sleep_roundtrip():
    f = FIELDS["sleep"]
    assert f.status is False
    assert f.decode(H("3c003c003c0000003c003c003c0000003c001e003c001e00")) == (180, 180)
    assert f.encode((180, 60)) == H("3c003c003c000000" "1400140014000000" "3c001e003c001e00")
    assert f.decode(f.encode((-1, 60)))[0] == -1
    assert f.decode(b"\x00" + f.encode((180, 60))) == (180, 60)      # 25 bytes = leading status byte


def test_simple_fields():
    assert FIELDS["polling_rate"].decode(H("00401f")) == 8000
    assert FIELDS["polling_rate"].encode(4000) == H("a00f")
    assert FIELDS["lod"].decode(H("0001")) == 1 and FIELDS["lod"].encode(3) == H("03")
    assert FIELDS["motion_sync"].decode(H("0001")) is True
    assert FIELDS["dpi_button"].decode(H("0018")) is True
    assert FIELDS["dpi_button"].encode(False) == H("00") and FIELDS["dpi_button"].encode(True) == H("18")
    assert FIELDS["debounce"].decode(H("00010101")) == (1, 1, 1)
    assert FIELDS["debounce"].encode((1, 4, 1)) == H("010401")
    assert FIELDS["rotate"].decode(H("0001fb")) == (True, -5)
    assert FIELDS["rotate"].encode((True, -5)) == H("01fb")
    assert FIELDS["hw_debounce_press"].get_args == b"\x00" and FIELDS["hw_debounce_press"].set is None
    assert FIELDS["hw_debounce_release"].get_args == b"\x01"
    assert FIELDS["hw_debounce_press"].decode(H("000a")) == 10
    assert FIELDS["fps_mode"].decode(H("00")) is False and FIELDS["fps_mode"].decode(H("0001")) is True
    assert FIELDS["profile_key"].decode(H("01")) is True


def test_parse_key():
    assert parse_key(H("00060c")) == (6, 12, b"")
    assert parse_key(H("0004060000" "7300")) == (4, 6, H("00007300"))
    assert parse_key(H("0104")) is None


def test_parse_battery():
    assert parse_battery(H("00003e64011801")) == {"percent": 62, "charging": False, "present": True}
    assert parse_battery(H("01")) is None


def test_nv_read_and_parse():
    assert read_nv(24586) == H("0a60e803")
    assert parse_nv(H("000a600300010000"), 24586) == H("010000")
    assert parse_nv(H("000a600300010000"), 25088) is None


def test_dpi_colour_nv():
    data = save_dpi_colour(0, (255, 0, 0))
    assert data[:6] == H("216221240010") and data[6:9] == H("ff0000") and len(data) == 42


def test_r2_sleep_record():
    current = H("3c003c003c0000003c003c003c000000")
    out = sleep_record_r2(current, None, 300)
    assert out == H("0060" "3c003c003c000000" "6400640064000000")
