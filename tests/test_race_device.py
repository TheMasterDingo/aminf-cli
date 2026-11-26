"""RaceMouse against a simulated .100R2 seeded with real replies."""
import pytest

from aminf.discover import open_mice
from aminf.errors import AmError, MouseOffline, NotConfirmed, NotInFirmware
from aminf.race.codec import WRITES
from tests.conftest import load
from tests.fakes import FakeRace


def mouse_for(model_key, dongle=True, fixture="100r2-dongle"):
    fake = FakeRace(load(fixture), model_key, dongle=dongle)
    return open_mice(fake)[0][0], fake


def test_read_sensor(r2):
    s = r2[0].read("sensor")
    assert s["polling_rate"] == 8000 and s["lod"] == 1 and s["motion_sync"] is True
    assert s["angle"] == 0 and s["angle_enabled"] is False and s["profile_key"] is True
    assert s["angle_snap"] is False and s["ripple"] is False and s["fps_mode"] is False


def test_read_power(r2):
    p = r2[0].read("power")
    assert (p["sleep_24g"], p["sleep_bt"]) == (180, 180)
    assert (p["debounce_wired"], p["debounce_24g"], p["debounce_bt"]) == (1, 1, 1)
    assert (p["hw_debounce_press"], p["hw_debounce_release"]) == (10, 10)


def test_basics(r2):
    assert r2[0].profile() == 1
    assert r2[0].battery() == {"percent": 58, "charging": False, "present": True}
    assert r2[0].hub_battery()["percent"] == 100


def test_stale_reply_does_not_hide_the_answer(r2):
    r2[1]._reply(0x80, 12495, bytes.fromhex("00003d64011d01"))   # left over from earlier
    assert r2[0].hub_battery()["percent"] == 100


def test_write_is_confirmed(r2):
    assert r2[0].write("sensor", {"lod": 3}) == {"lod": 3}
    assert r2[0].read("sensor")["lod"] == 3


def test_unconfirmed_write_raises(r2):
    r2[1].drop_writes = {12492}
    with pytest.raises(NotConfirmed):
        r2[0].write("sensor", {"lod": 3})


def test_range_comes_from_model(r2):
    with pytest.raises(AmError, match="0-4"):
        r2[0].write("sensor", {"lod": 5})


def test_strict_false_skips_the_model_range_only(r2):
    assert r2[0].write("sensor", {"lod": 9}, strict=False) == {"lod": 9}
    with pytest.raises(AmError):
        r2[0].write("sensor", {"lod": 300}, strict=False)


def test_unknown_key_is_an_error(r2):
    with pytest.raises(AmError, match="sideways"):
        r2[0].write("sensor", {"sideways": 1})


def test_polling_rate_must_be_a_real_rate(r2):
    with pytest.raises(AmError):
        r2[0].write("sensor", {"polling_rate": 3000})
    assert r2[0].write("sensor", {"polling_rate": 1000}) == {"polling_rate": 1000}


def test_angle_keeps_the_enable_flag_unless_given(r2):
    r2[0].write("sensor", {"angle": -5})
    assert r2[1].sent_data(12504) == bytes([0, 0xFB])
    r2[0].write("sensor", {"angle_enabled": True})
    assert r2[1].sent_data(12504) == bytes([1, 0xFB])


def test_partial_debounce_keeps_the_others(r2):
    r2[0].write("power", {"debounce_24g": 4})
    assert r2[1].sent_data(12520) == bytes([1, 4, 1])


def test_hw_debounce_is_read_only(r2):
    with pytest.raises(AmError, match="read-only"):
        r2[0].write("power", {"hw_debounce_press": 5})


def test_sleep_minimum(r2):
    with pytest.raises(AmError):
        r2[0].write("power", {"sleep_24g": 10})


def test_sleep_never(r2):
    assert r2[0].write("power", {"sleep_bt": "never"}) == {"sleep_bt": "never"}
    assert r2[0].read("power")["sleep_24g"] == 180


def test_r2_cable_sleep_uses_nv_record():
    mouse, fake = mouse_for("100r2", dongle=False)
    assert mouse.write("power", {"sleep_bt": 300}) == {"sleep_bt": 300}
    assert fake.sent_cmds()[-2:] == [2561, 2572] and 12306 not in fake.sent_cmds()
    assert mouse.read("power")["sleep_24g"] == 180


def test_r2_dongle_profile_switch_goes_to_both(r2):
    assert r2[0].set_profile(2) == 2
    assert [(d, c) for d, c in r2[1].sent_pairs() if c == 12488] == [(0x00, 12488), (0x80, 12488)]


def test_97_profile_switch_goes_to_the_mouse_only():
    mouse, fake = mouse_for("97")
    assert mouse.set_profile(0) == 0
    assert [d for d, c in fake.sent_pairs() if c == 12488] == [0x80]


def test_profile_out_of_range(r2):
    with pytest.raises(AmError):
        r2[0].set_profile(3)


def test_profile_switch_not_confirmed(r2):
    r2[1].drop_writes = {12488}
    with pytest.raises(NotConfirmed):
        r2[0].set_profile(2)


def test_offline_mouse_reports_once_and_writes_nothing(r2):           # Review Focus 1
    r2[1].offline = True
    with pytest.raises(MouseOffline):
        r2[0].write("sensor", {"lod": 2})
    assert not [c for c in r2[1].sent_cmds() if c in WRITES]
    n = len(r2[1].sent)
    with pytest.raises(MouseOffline):
        r2[0].read("power")
    assert len(r2[1].sent) == n                  # no second round of timeouts
    assert r2[0].offline is True
    assert r2[0].hub_battery()["percent"] == 100  # the dongle itself still answers


def test_missing_profile_key_is_absent(r2):                           # Review Focus 3
    r2[1].missing = {"12525:"}
    assert "profile_key" not in r2[0].read("sensor")
    with pytest.raises(NotInFirmware, match="not supported by this firmware"):
        r2[0].write("sensor", {"profile_key": False})
    assert 12524 not in r2[1].sent_cmds()


def test_97_lod_range():
    mouse, _ = mouse_for("97")
    with pytest.raises(AmError, match="0-2"):
        mouse.write("sensor", {"lod": 3})


def test_97_has_no_hardware_debounce():
    mouse, fake = mouse_for("97")
    assert "hw_debounce_press" not in mouse.read("power")
    assert 12491 not in fake.sent_cmds()


def test_light_write_is_confirmed(r2):
    assert r2[0].write("dongle_light", {"on": False}) == {"on": False}
    assert r2[0].read("dongle_light")["on"] is False
    r2[1].drop_writes = {12301}
    with pytest.raises(NotConfirmed):
        r2[0].write("dongle_light", {"on": True})


def test_dongle_light_write_keeps_the_other_fields(r2):
    before = r2[1].replies["local/12302:"]
    r2[0].write("dongle_light", {"brightness": 100})
    sent = r2[1].sent_data(12301)
    assert sent[12] == 100 and sent[:12] == before[:12]


def test_light_section_matches_legacy_keys(r2):
    assert set(r2[0].read("dongle_light")) >= {"on", "effect", "hue1", "sat1", "speed", "brightness", "colour"}
    assert r2[0].read("light") == {"on": True, "effect": "solid", "speed": 0, "colour": "#FFFFFF"}


def test_mouse_light_switch(r2):
    assert r2[0].write("light", {"on": False}) == {"on": False}
    assert r2[1].sent_data(12467)[0] == 0


def test_no_dongle_light_on_cable():
    mouse, _ = mouse_for("100r2", dongle=False)
    assert mouse.read("dongle_light") is None
    with pytest.raises(AmError, match="dongle"):
        mouse.write("dongle_light", {"on": False})
