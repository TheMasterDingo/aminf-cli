"""Regression tests for the findings of the final whole-branch review."""
import pytest

from aminf.discover import open_mice
from aminf.errors import AmError, MouseOffline, NotInFirmware
from aminf.profile import apply, snapshot
from aminf.race.codec import WRITES
from tests.conftest import load
from tests.fake_m100 import FakeM100


def writes(fake):
    return [c for c in fake.sent_cmds() if c in WRITES]


def statuses(results):
    return {s: st for s, st, _ in results}


# 1 -- a profile switch must not be sent to a mouse that is asleep

def test_profile_switch_sends_nothing_when_the_mouse_is_asleep(r2):
    r2[1].offline = True
    with pytest.raises(MouseOffline):
        r2[0].set_profile(2)
    assert 12488 not in r2[1].sent_cmds()         # neither to the receiver nor to the mouse


# 2 -- unplugging a .97 / .100R2 mid-import still gives the per-section report

def test_unplugged_mid_import_still_reports_sections(r2):
    doc = snapshot(r2[0])
    doc["sections"]["sensor"]["lod"] = 3
    doc["sections"]["dpi"]["count"] = 4
    doc["sections"]["buttons"]["forward"] = "f13"
    r2[1].unplug_after_writes = 1
    results = statuses(apply(r2[0], doc))
    assert results["sensor"] == "failed" and results["dpi"] == "skipped" and results["buttons"] == "skipped"


def test_unplugged_mid_import_from_the_command_line(run, r2, write_tmp):
    doc = snapshot(r2[0])
    doc["sections"]["sensor"]["lod"] = 3
    doc["sections"]["dpi"]["count"] = 4
    r2[1].unplug_after_writes = 1
    code, out, err = run(["import", write_tmp(doc)], r2[1])
    assert code == 2 and "sensor" in out and "dpi" in out and "skipped" in out


def test_unplugged_device_is_an_aminf_error_not_a_crash(r2):
    r2[1].unplugged = True
    with pytest.raises(AmError):
        r2[0].read("sensor")


# 3 -- import refuses, before writing anything, what a write would refuse

def _doc_with_a_valid_change(mouse):
    doc = snapshot(mouse)
    doc["sections"]["sensor"]["lod"] = 3          # must NOT reach the mouse
    return doc


def test_import_checks_the_active_stage_against_the_count(r2):
    doc = _doc_with_a_valid_change(r2[0])
    doc["sections"]["dpi"].update(count=4, active=7)
    r2[1].sent.clear()
    with pytest.raises(AmError, match="active"):
        apply(r2[0], doc)
    assert writes(r2[1]) == []


@pytest.mark.parametrize("section,value", [("light", "rainbow"), ("dongle_light", "disco")])
def test_import_checks_effect_names(r2, section, value):
    doc = _doc_with_a_valid_change(r2[0])
    doc["sections"][section]["effect"] = value
    r2[1].sent.clear()
    with pytest.raises(AmError, match="effect"):
        apply(r2[0], doc)
    assert writes(r2[1]) == []


def test_import_checks_for_a_left_click_first(r2):
    doc = _doc_with_a_valid_change(r2[0])
    doc["sections"]["buttons"]["left"] = "f13"
    r2[1].sent.clear()
    with pytest.raises(AmError, match="left click"):
        apply(r2[0], doc)
    assert writes(r2[1]) == []


def test_a_partial_buttons_file_is_checked_against_the_mouse(r2):
    doc = _doc_with_a_valid_change(r2[0])
    doc["sections"]["buttons"] = {"left": "disabled"}
    with pytest.raises(AmError, match="left click"):
        apply(r2[0], doc)


# 4 -- import never overwrites a button that now holds a macro

def test_import_leaves_a_macro_bound_button_alone(r2):
    doc = snapshot(r2[0])                                   # the file says back = back
    r2[1].replies["12483:05"] = bytes.fromhex("000508") + bytes(20)   # since then: a macro on back
    r2[1].sent.clear()
    results = apply(r2[0], doc)
    assert 12482 not in r2[1].sent_cmds()
    assert statuses(results)["buttons"] == "unchanged"
    assert r2[0].read("buttons")["back"].startswith("opaque:08")


# 5 -- a .100 setting the firmware does not answer is left out, not fatal

def test_100_toggle_the_firmware_does_not_answer():
    fake = FakeM100(load("100-dongle"), dongle=False)
    fake.missing = {228}
    mouse = open_mice(fake, which="100")[0][0]
    s = mouse.read("sensor")
    assert "profile_key" not in s and s["lod"] == 0 and "fps_mode" in s
    with pytest.raises(NotInFirmware, match="not supported by this firmware"):
        mouse.write("sensor", {"profile_key": False})
    assert 100 not in fake.sent_cmds()


def test_100_export_survives_a_missing_toggle():
    fake = FakeM100(load("100-dongle"), dongle=False)
    fake.missing = {227}
    mouse = open_mice(fake, which="100")[0][0]
    doc = snapshot(mouse)
    assert "button" not in doc["sections"]["dpi"] and doc["sections"]["dpi"]["count"] == 1


# 6 -- one "this setting is missing" verdict must not hide a mouse that then goes away

def test_a_mouse_that_falls_asleep_after_a_missing_setting_is_still_noticed(r2):
    r2[1].missing = {"12525:"}
    assert "profile_key" not in r2[0].read("sensor")
    r2[1].offline = True
    with pytest.raises(MouseOffline):
        r2[0].read("power")


# 7 -- the .100 profile switch says what else it did, and a no-op is a no-op

def test_100_profile_switch_says_fps_mode_was_turned_off(run):
    fake = FakeM100(load("100-dongle"))
    code, out, _ = run(["--mouse", "100", "profile", "1"], fake)
    assert code == 0 and "profile -> 1" in out and "FPS" in out
    assert fake.sent_packet(98)[1] == 0


def test_100_profile_switch_to_the_active_profile_changes_nothing(run):
    fake = FakeM100(load("100-dongle"))
    mouse = open_mice(fake, which="100")[0][0]
    mouse.write("sensor", {"fps_mode": True})
    fake.sent.clear()
    assert mouse.set_profile(0) == 0
    assert not [c for c in fake.sent_cmds() if c in (5, 98)]
    assert mouse.read("sensor")["fps_mode"] is True


# 2.2.0 guarded the .100's key-map read against a reply left over from an
# earlier command; the rewrite dropped the guard.

def test_100_stale_reply_is_not_taken_for_the_key_map():
    fake = FakeM100(load("100-dongle"))
    fake.stale_keymap_once = True
    mouse = open_mice(fake, which="100")[0][0]
    assert mouse.read("buttons")["left"] == "left"
    assert fake.sent_cmds().count(208) == 2


def test_100_key_map_that_never_looks_right_is_an_error():
    fake = FakeM100(load("100-dongle"))
    fake.truncate = {208}
    fake.blocks[208] = bytes([133]) + bytes(63)
    mouse = open_mice(fake, which="100")[0][0]
    with pytest.raises(AmError, match="key map"):
        mouse.read("buttons")
    with pytest.raises(AmError, match="key map"):
        mouse.write("buttons", {"back": "f13"})
    assert 80 not in fake.sent_cmds()
