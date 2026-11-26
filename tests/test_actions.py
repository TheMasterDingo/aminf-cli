"""The action grammar shared by every model, and its .97/.100R2 encoding."""
import pytest

from aminf.actions import LEFT, Action, check_left_click, format_action, parse_action
from aminf.errors import AmError, Refused
from aminf.keys import KEY_USAGES, MEDIA
from aminf.models import MODELS
from aminf.race.codec import decode_action, encode_action


def test_parse():
    assert parse_action("F13") == Action("key", 0x68)
    assert parse_action("ctrl+shift+a") == Action("key", 0x04, ("ctrl", "shift"))
    assert parse_action("Shift+Ctrl+A") == Action("key", 0x04, ("ctrl", "shift"))
    assert parse_action("0x47") == Action("key", 0x47)
    assert parse_action("media:play") == Action("media", 0x00CD)
    assert parse_action("wheel-up") == Action("mouse", "wheel-up")
    assert parse_action("dpi-cycle") == Action("dpi", "cycle")
    assert parse_action("default") is None
    assert parse_action("left") == LEFT


@pytest.mark.parametrize("bad", ["", "f25", "ctrl+", "media:nope", "0x03", "0xE8",
                                 "hyper+a", "ctrl+ctrl+a", "opaque:zz", "media:0x1ffff"])
def test_parse_rejects(bad):
    with pytest.raises(AmError):
        parse_action(bad)


@pytest.mark.parametrize("text", ["f24", "ctrl+shift+a", "media:vol-up", "left", "dpi-down",
                                  "disabled", "profile-switch", "opaque:0801ff", "space",
                                  "win+f13", "media:0x00b8"])
def test_format_roundtrip(text):
    assert format_action(parse_action(text)) == text


def test_key_table_keeps_the_2_2_0_function_keys():
    assert KEY_USAGES["f1"] == 0x3A and KEY_USAGES["f12"] == 0x45
    assert KEY_USAGES["f13"] == 0x68 and KEY_USAGES["f24"] == 0x73
    assert KEY_USAGES["a"] == 0x04 and KEY_USAGES["0"] == 0x27 and KEY_USAGES["enter"] == 0x28
    assert all(0x04 <= u <= 0xE7 for u in KEY_USAGES.values())
    assert MEDIA["play"] == 0x00CD and MEDIA["vol-up"] == 0x00E9 and MEDIA["mute"] == 0x00E2


@pytest.mark.parametrize("text,kind,params", [
    ("f24", 6, "00007300"), ("ctrl+shift+a", 6, "00000403"), ("media:play", 6, "cd000000"),
    ("win+alt+f13", 6, "0000680c"), ("media:player", 6, "83010000"),
    ("left", 1, ""), ("back", 5, ""), ("wheel-up", 12, ""), ("wheel-down", 13, ""),
    ("dpi-up", 11, "000000"), ("dpi-down", 11, "010000"), ("dpi-cycle", 11, "030000"),
    ("profile-switch", 14, ""), ("disabled", 0, "")])
def test_race_encode_decode(text, kind, params):
    a = parse_action(text)
    assert encode_action(a) == (kind, bytes.fromhex(params))
    assert decode_action(kind, bytes.fromhex(params)) == a


def test_unknown_data_never_breaks_a_read():
    assert decode_action(8, b"\x01\x02").kind == "opaque"          # macro
    assert decode_action(7, b"").kind == "opaque"                   # report-rate switch
    assert decode_action(99, b"\x01").kind == "opaque"              # anything unlisted
    assert decode_action(11, b"\x02\x40\x06").kind == "opaque"      # DPI lock: not offered
    assert decode_action(6, b"").kind == "opaque"                   # a key with no key
    assert format_action(decode_action(6, bytes.fromhex("b8000000"))) == "media:0x00b8"
    assert parse_action("media:0x00b8") == Action("media", 0x00B8)
    assert format_action(decode_action(8, b"\x01\x02")) == "opaque:080102"


def test_opaque_is_never_encoded():
    with pytest.raises(AmError):
        encode_action(parse_action("opaque:0801"))


def test_defaults():
    assert MODELS["97"].default_action("wheel-up") == Action("mouse", "wheel-up")
    assert MODELS["100r2"].default_action("left") == LEFT
    # the configurator's own "restore default" writes type 14 to key 8
    assert MODELS["100r2"].default_action("bottom") == Action("profile-switch")


def test_left_click_guard():
    check_left_click({"left": parse_action("f13"), "back": LEFT})
    with pytest.raises(Refused, match="left click"):
        check_left_click({"left": parse_action("f13"), "back": parse_action("back")})
