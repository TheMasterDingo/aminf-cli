"""Every button on the .97 / .100R2, against the simulated .100R2."""
import pytest

from aminf.discover import open_mice
from aminf.errors import AmError, NotConfirmed, NotInFirmware, Refused, Unsupported
from tests.conftest import load
from tests.fakes import FakeRace


def test_read_buttons(r2):
    assert r2[0].read("buttons") == {"left": "left", "right": "right", "middle": "middle",
        "forward": "forward", "back": "back", "wheel-up": "wheel-up", "wheel-down": "wheel-down",
        "bottom": "disabled"}


def test_remap_and_confirm(r2):
    assert r2[0].write("buttons", {"forward": "f24"}) == {"forward": "f24"}
    assert r2[1].sent_data(12482) == bytes([4, 6, 0, 0, 0x73, 0])
    assert r2[0].read("buttons")["forward"] == "f24"


def test_default_restores_own_function(r2):
    r2[0].write("buttons", {"wheel-up": "f13"})
    assert r2[0].write("buttons", {"wheel-up": "default"}) == {"wheel-up": "wheel-up"}
    assert r2[1].sent_data(12482) == bytes([6, 12])


def test_bottom_default_is_the_layer_switch(r2):
    assert r2[0].write("buttons", {"bottom": "default"}) == {"bottom": "profile-switch"}
    assert r2[1].sent_data(12482) == bytes([8, 14])


def test_last_left_click_is_protected(r2):
    with pytest.raises(Refused):
        r2[0].write("buttons", {"left": "f13"})
    assert 12482 not in r2[1].sent_cmds()
    r2[0].write("buttons", {"back": "left"})
    r2[0].write("buttons", {"left": "f13"})        # now allowed


def test_several_buttons_in_one_write(r2):
    assert r2[0].write("buttons", {"left": "right", "right": "left"}) == {"left": "right", "right": "left"}


def test_profile_switch_only_on_r2():
    mouse = open_mice(FakeRace(load("100r2-dongle"), "97", dongle=True))[0][0]
    with pytest.raises(Unsupported):
        mouse.write("buttons", {"back": "profile-switch"})


def test_97_has_seven_buttons():
    mouse = open_mice(FakeRace(load("100r2-dongle"), "97", dongle=True))[0][0]
    assert list(mouse.read("buttons")) == ["left", "right", "middle", "forward", "back", "wheel-up", "wheel-down"]
    with pytest.raises(Unsupported):
        mouse.write("buttons", {"bottom": "f13"})


def test_seven_button_firmware(r2):                                    # Review Focus 3
    r2[1].missing = {"12483:08"}
    assert "bottom" not in r2[0].read("buttons")
    with pytest.raises(NotInFirmware, match="not supported by this firmware"):
        r2[0].write("buttons", {"bottom": "f13"})
    assert 12482 not in r2[1].sent_cmds()


def test_opaque_cannot_be_written(r2):
    with pytest.raises(AmError):
        r2[0].write("buttons", {"back": "opaque:0801"})


def test_unknown_button_and_bad_action(r2):
    with pytest.raises(AmError, match="sideways"):
        r2[0].write("buttons", {"sideways": "f13"})
    with pytest.raises(AmError):
        r2[0].write("buttons", {"back": "nonsense"})


def test_macro_bound_button_reads_as_opaque(r2):
    r2[1].replies["12483:05"] = bytes.fromhex("000508") + bytes(20)
    assert r2[0].read("buttons")["back"].startswith("opaque:08")


def test_unconfirmed_remap(r2):
    r2[1].drop_writes = {12482}
    with pytest.raises(NotConfirmed):
        r2[0].write("buttons", {"forward": "f24"})
