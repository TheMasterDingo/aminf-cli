"""Byte-level tests for the .100 protocol."""
import pytest

from aminf.actions import parse_action
from aminf.errors import AmError, Refused, Unsupported
from aminf.m100.codec import (ALLOWED, BUTTON_INDEX, KEY_OFFSET, LEN, TOGGLES, checksum, cmd,
                              decode_action, dongle_light_packet, dpi_changes, encode_action,
                              key_packet, param_changes, parse_dpi, patch_block, raw,
                              read_params, set_profile)


def test_cmd_checksum():
    assert cmd(133)[:8] == bytes([133, 0, 0, 0, 0, 0, 0, 122]) and len(cmd(133)) == LEN
    assert cmd(212, [1])[:8] == bytes([212, 1, 0, 0, 0, 0, 0, 42])


def test_set_profile():
    assert set_profile(1, 2)[:8] == bytes([5, 1, 2, 0, 0, 0, 0, 247])


@pytest.mark.parametrize("code", [2, 97, 127, 248, 64, 65, 186, 22, 87, 192, 193, 0])
def test_not_allowed(code):
    with pytest.raises(Refused):
        cmd(code)
    with pytest.raises(Refused):
        raw(code)
    with pytest.raises(Refused):
        patch_block(bytes(64), code, {})


def test_allow_list_is_exactly_what_the_design_names():
    assert ALLOWED == {211, 83, 214, 133, 135, 7, 136, 8, 247, 246, 254, 252, 208, 80,
                       5, 212, 84, 226, 98, 227, 99, 228, 100, 229, 101}


def test_raw():
    assert raw(246, 5) == bytes([246, 5]) + bytes(62)


def test_patch_block():
    src = bytes([211]) + bytes(range(1, 64))
    out = patch_block(src, 83, {9: 130})
    assert out[0] == 83 and out[9] == 130 and out[7] == checksum(out[:7]) and out[10:] == src[10:]
    assert out[1:7] == src[1:7] and out[8] == src[8]
    with pytest.raises(AmError, match="nothing written"):
        patch_block(src[:63], 83, {})
    with pytest.raises(AmError):
        patch_block(src, 83, {7: 1})                 # the checksum byte is not a setting
    with pytest.raises(AmError):
        patch_block(src, 83, {0: 1})


def test_params_roundtrip():
    block = bytearray(64)
    block[9] = 129; block[52] = 1; block[63] = 1; block[44:46] = (180).to_bytes(2, "little")
    block[54:57] = b"\xff\x82\x3c"; block[60] = 1; block[10] = 3
    p = read_params(bytes(block))
    assert p["polling_rate"] == 8000 and p["lod"] == 1 and p["motion_sync"] is True and p["sleep_24g"] == 180
    assert p["colour"] == "#FF823C" and p["light_on"] is True and p["debounce_wired"] == 3
    assert p["angle_snap"] is False and p["ripple"] is False
    assert param_changes({"polling_rate": 4000, "sleep_bt": 300, "motion_sync": False}) == {9: 130, 40: 44, 41: 1, 63: 0}
    assert param_changes({"colour": "#FF823C", "light_on": False, "debounce_24g": 5}) == {54: 255, 55: 130, 56: 60, 60: 0, 61: 5}


def test_param_changes_rejects_what_it_does_not_know():
    with pytest.raises(AmError):
        param_changes({"polling_rate": 3000})
    with pytest.raises(AmError):
        param_changes({"sideways": 1})


def test_unknown_rate_code_reads_as_none():
    assert read_params(bytes(64))["polling_rate"] is None


def test_toggles():
    assert TOGGLES == {"angle": (229, 101), "fps_mode": (226, 98), "dpi_button": (227, 99), "profile_key": (228, 100)}


def test_dpi_block():
    block = bytearray(64)
    block[2] = 1; block[3] = 4
    block[8:10] = (800).to_bytes(2, "little"); block[24:26] = (800).to_bytes(2, "little"); block[40:43] = b"\xff\x00\x00"
    d = parse_dpi(bytes(block))
    assert d["active"] == 1 and d["count"] == 4 and d["stages"][0] == {"x": 800, "y": 800, "colour": "#FF0000"}
    assert len(d["stages"]) == 8
    assert dpi_changes({"stages": {1: {"x": 1600}}}, d) == {10: 0x40, 11: 0x06, 26: 0x40, 27: 0x06}
    assert dpi_changes({"stages": {1: {"y": 1600}}}, d) == {26: 0x40, 27: 0x06}
    assert dpi_changes({"stages": {7: {"colour": "#00FF00"}}, "count": 2, "active": 0}, d) == {61: 0, 62: 255, 63: 0, 3: 2, 2: 0}


def test_key_packet():
    p = key_packet(0, 3, bytes([0, 0x73, 0, 0]))
    assert p[:8] == bytes([80, 0, 3, 0, 0, 0, 0, 172]) and p[8:12] == bytes([0, 0x73, 0, 0]) and len(p) == LEN


def test_dongle_light_packet():
    p = dongle_light_packet(1, 0, 4, 7, (255, 185, 85))
    assert p[:8] == bytes([8, 1, 0, 4, 7, 255, 185, 85]) and p[8] == checksum(p[:8]) and len(p) == LEN


def test_button_tables():
    assert BUTTON_INDEX == {"left": 0, "right": 1, "middle": 2, "forward": 3, "back": 4, "wheel-up": 14, "wheel-down": 15}
    assert KEY_OFFSET == {0: 0, 1: 4, 2: 8, 3: 12, 4: 16, 14: 56, 15: 60}


@pytest.mark.parametrize("text,b4", [
    ("f24", "00730000"), ("ctrl+a", "0000e004"), ("ctrl+shift+a", "00e0e104"), ("win+f13", "0000e368"),
    ("left", "0100f000"), ("right", "0100f100"), ("middle", "0100f200"), ("back", "0100f300"),
    ("forward", "0100f400"), ("wheel-up", "0100f501"), ("wheel-down", "0100f5ff"),
    ("media:play", "0300cd00"), ("media:player", "03008300"), ("dpi-up", "14000100"),
    ("dpi-down", "14000200"), ("dpi-cycle", "14000000"), ("disabled", "00000000")])
def test_m100_actions(text, b4):
    a = parse_action(text)
    assert encode_action(a) == bytes.fromhex(b4) and decode_action(bytes.fromhex(b4)) == a


def test_m100_action_limits():
    with pytest.raises(AmError, match="two modifiers"):
        encode_action(parse_action("ctrl+shift+alt+a"))
    with pytest.raises(Unsupported):
        encode_action(parse_action("profile-switch"))
    with pytest.raises(AmError):
        encode_action(parse_action("media:0x00b8"))      # the .100 only knows the configurator's 16
    with pytest.raises(AmError):
        encode_action(parse_action("opaque:09000100"))
    assert decode_action(bytes([9, 0, 1, 0])).kind == "opaque"
    assert decode_action(bytes([5, 1, 2, 3])).kind == "opaque"      # anything unlisted
    assert decode_action(bytes([11, 4, 20, 3])).kind == "opaque"    # rapid fire
    assert decode_action(bytes([1, 0, 249, 1])) == parse_action("wheel-up")
    assert decode_action(bytes([3, 0, 0x99, 0])).kind == "opaque"   # a media byte nobody named
