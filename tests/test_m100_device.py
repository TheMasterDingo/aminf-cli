"""M100Mouse against a simulated .100 seeded with a real capture."""
import json

import pytest

from aminf.device import SECTIONS
from aminf.discover import open_mice
from aminf.errors import AmError, MouseOffline, NotConfirmed, Refused, Unsupported
from aminf.m100 import codec
from tests.conftest import load
from tests.fake_m100 import FakeM100

WRITES = (83, 84, 80, 5, 7, 8, 98, 99, 100, 101)


@pytest.fixture
def m100():
    fake = FakeM100(load("100-dongle"))
    mouse = open_mice(fake, which="100")[0][0]
    yield mouse, fake
    mouse.close()


def test_found_and_named(m100):
    assert m100[0].model.name == ".100" and m100[0].connection == "dongle"


def test_sections_exist(m100):
    assert all(m100[0].read(s) is not None for s in SECTIONS)


def test_read_values_match_the_capture(m100):
    s, p, d = m100[0].read("sensor"), m100[0].read("power"), m100[0].read("dpi")
    assert s == {"polling_rate": 4000, "lod": 0, "motion_sync": True, "angle_snap": False,
                 "ripple": False, "fps_mode": False, "angle": 0, "profile_key": True}
    assert p == {"sleep_24g": 180, "sleep_bt": 60, "deep_sleep_24g": 180, "deep_sleep_bt": 300,
                 "debounce_wired": 0, "debounce_24g": 0, "debounce_bt": 0}
    assert (d["count"], d["active"], d["button"]) == (1, 0, False)
    assert d["stages"][0] == {"x": 1800, "y": 1800, "colour": "#FFFFFF"} and d["stages"][7]["x"] == 30000
    assert m100[0].read("light") == {"on": True, "colour": "#FF823C", "colour_charged": "#FF823C"}
    assert m100[0].read("dongle_light") == {"on": True, "effect": "solid", "speed": 0,
                                            "brightness": 4, "colour": "#FFB955", "preset": False}
    assert m100[0].profile() == 0
    assert m100[0].battery() == {"percent": 100, "charging": False, "present": True}
    assert m100[0].hub_battery()["percent"] == 36


def test_param_write_is_read_modify_write(m100):
    before = m100[1].block(211)
    assert m100[0].write("sensor", {"lod": 1}) == {"lod": 1}
    sent = m100[1].sent_packet(83)
    assert [i for i in range(8, 64) if sent[i] != before[i]] == [52]
    assert sent[7] == codec.checksum(sent[:7])


def test_param_write_refused_without_a_full_read(m100):
    m100[1].truncate = {211}
    with pytest.raises(AmError, match="nothing written"):
        m100[0].write("sensor", {"lod": 1})
    assert 83 not in m100[1].sent_cmds()


def test_unconfirmed_block_write(m100):
    m100[1].drop_writes = {83}
    with pytest.raises(NotConfirmed):
        m100[0].write("power", {"debounce_wired": 5})


def test_several_params_in_one_block_write(m100):
    m100[0].write("sensor", {"polling_rate": 1000, "motion_sync": False})
    assert m100[1].sent_cmds().count(83) == 1
    assert m100[0].read("sensor")["polling_rate"] == 1000


def test_ranges(m100):
    for section, change in (("sensor", {"lod": 3}), ("sensor", {"polling_rate": 3000}),
                            ("sensor", {"angle": 91}), ("power", {"sleep_bt": 10}),
                            ("power", {"debounce_bt": 256}), ("dpi", {"count": 9}),
                            ("dpi", {"active": 1}), ("dongle_light", {"brightness": 5}),
                            ("dongle_light", {"effect": "disco"}), ("light", {"colour": "red"})):
        with pytest.raises(AmError):
            m100[0].write(section, change)
    assert not [c for c in m100[1].sent_cmds() if c in WRITES]


def test_strict_false_keeps_only_the_field_bounds(m100):
    assert m100[0].write("power", {"sleep_bt": 10}, strict=False) == {"sleep_bt": 10}
    assert m100[0].write("dpi", {"stages": {7: {"x": 30000}}}, strict=False)
    with pytest.raises(AmError):
        m100[0].write("dpi", {"stages": {7: {"x": 70000}}}, strict=False)


def test_sleep_never(m100):
    assert m100[0].write("power", {"sleep_24g": "never"}) == {"sleep_24g": "never"}
    assert m100[1].sent_packet(83)[44:46] == b"\xff\xff"


def test_keys_of_the_other_family_are_unsupported(m100):
    with pytest.raises(Unsupported):
        m100[0].write("sensor", {"angle_enabled": True})
    with pytest.raises(AmError, match="sideways"):
        m100[0].write("sensor", {"sideways": 1})


def test_toggle_fields(m100):
    assert m100[0].write("sensor", {"angle": -3}) == {"angle": -3}
    assert m100[1].sent_packet(101)[1] == 0xFD
    assert m100[0].write("sensor", {"fps_mode": True, "profile_key": False}) == {"fps_mode": True, "profile_key": False}
    assert m100[1].sent_packet(98)[1] == 1 and m100[1].sent_packet(100)[1] == 0


def test_dpi_write_touches_only_named_stage(m100):
    before = m100[1].block(212)
    assert m100[0].write("dpi", {"stages": {7: {"x": 1000}}}) == {"stages": {7: {"x": 1000, "y": 1000}}}
    sent = m100[1].sent_packet(84)
    assert {i for i in range(8, 64) if sent[i] != before[i]} == {22, 23, 38, 39}
    assert sent[1:4] == before[1:4]


def test_dpi_count_active_colour_button(m100):
    done = m100[0].write("dpi", {"count": 4, "active": 2, "button": True,
                                 "stages": {1: {"colour": "#00FF00"}}})
    assert done == {"count": 4, "active": 2, "button": True, "stages": {1: {"colour": "#00FF00"}}}
    d = m100[0].read("dpi")
    assert (d["count"], d["active"], d["button"], d["stages"][1]["colour"]) == (4, 2, True, "#00FF00")
    assert m100[1].sent_packet(99)[1] == 1


def test_dpi_max_is_26000(m100):
    with pytest.raises(AmError):
        m100[0].write("dpi", {"stages": {0: {"x": 26050}}})
    assert 84 not in m100[1].sent_cmds()


def test_buttons_all_seven(m100):
    b = m100[0].read("buttons")
    assert list(b) == ["left", "right", "middle", "forward", "back", "wheel-up", "wheel-down"]
    assert b == {k: k for k in b}                  # the capture is all defaults


def test_remap(m100):
    assert m100[0].write("buttons", {"forward": "f24"}) == {"forward": "f24"}
    p = m100[1].sent_packet(80)
    assert p[:8] == bytes([80, 0, 3, 0, 0, 0, 0, 172]) and p[8:12] == bytes([0, 0x73, 0, 0])
    assert m100[0].write("buttons", {"forward": "default"}) == {"forward": "forward"}
    assert m100[1].sent_packet(80)[8:12] == bytes([1, 0, 244, 0])


def test_remap_retry_schedule(m100):
    m100[1].drop_first = {80}
    assert m100[0].write("buttons", {"back": "f23"}) == {"back": "f23"}
    assert m100[1].sent_cmds().count(80) == 2


def test_remap_gives_up_after_one_retry(m100):
    m100[1].drop_writes = {80}
    with pytest.raises(NotConfirmed):
        m100[0].write("buttons", {"back": "f23"})
    assert m100[1].sent_cmds().count(80) == 2


def test_left_click_guard(m100):
    with pytest.raises(Refused):
        m100[0].write("buttons", {"left": "disabled"})
    assert 80 not in m100[1].sent_cmds()


def test_no_bottom_button_or_layer_switch(m100):
    with pytest.raises(Unsupported):
        m100[0].write("buttons", {"bottom": "f13"})
    with pytest.raises(Unsupported):
        m100[0].write("buttons", {"back": "profile-switch"})


def test_profile_switch_then_performance_off(m100):
    assert m100[0].set_profile(1) == 1
    cmds = [c for c in m100[1].sent_cmds() if c in (5, 98)]
    assert cmds == [5, 98] and m100[1].sent_packet(5)[1:3] == bytes([1, 2])
    assert m100[1].sent_packet(98)[1] == 0
    with pytest.raises(AmError):
        m100[0].set_profile(2)


def test_profile_switch_not_confirmed(m100):
    m100[1].drop_writes = {5}
    with pytest.raises(NotConfirmed):
        m100[0].set_profile(1)
    assert 98 not in m100[1].sent_cmds()


def test_dongle_light_gaps_closed(m100):
    m100[0].write("dongle_light", {"brightness": 2, "effect": "breathing", "speed": 3})
    assert m100[1].sent_packet(8)[1:4] == bytes([2, 3, 2])
    assert m100[1].sent_packet(8)[4:8] == bytes([7, 0xFF, 0xB9, 0x55])      # colour kept
    assert m100[0].read("dongle_light")["effect"] == "breathing"


def test_dongle_light_colour_and_switch(m100):
    assert m100[0].write("dongle_light", {"colour": "#102030"}) == {"colour": "#102030"}
    p = m100[1].sent_packet(8)
    assert p[:9] == bytes([8, 1, 0, 4, 7, 0x10, 0x20, 0x30]) + bytes([codec.checksum(p[:8])])
    assert m100[0].write("dongle_light", {"on": False}) == {"on": False}
    assert m100[1].sent_packet(7)[1] == 0 and m100[0].read("dongle_light")["on"] is False


def test_solid_and_off_send_speed_zero(m100):
    m100[0].write("dongle_light", {"effect": "breathing", "speed": 3})
    m100[0].write("dongle_light", {"effect": "solid"})
    assert m100[1].sent_packet(8)[1:3] == bytes([1, 0])


def test_mouse_light_colour(m100):
    assert m100[0].write("light", {"colour": "#00FF00"}) == {"colour": "#00FF00"}
    sent = m100[1].sent_packet(83)
    assert sent[54:57] == bytes([0, 0xFF, 0]) and sent[57:60] == bytes([0xFF, 0x82, 0x3C])
    m100[0].write("light", {"colour_charged": "#0000FF", "on": False})
    assert m100[1].sent_packet(83)[57:61] == bytes([0, 0, 0xFF, 0])


def test_offline_mouse(m100):                                            # Review Focus 1
    m100[1].offline = True
    with pytest.raises(MouseOffline):
        m100[0].write("sensor", {"lod": 1})
    assert not [c for c in m100[1].sent_cmds() if c in (83, 84, 80, 5)]
    assert m100[0].offline is True
    assert m100[0].read("dongle_light")["on"] is True            # the dongle still answers
    m100[1].offline = False
    m100[0].forget_offline()
    assert m100[0].read("sensor")["lod"] == 0


def test_on_a_cable():
    fake = FakeM100(load("100-dongle"), dongle=False)
    mouse = open_mice(fake, which="100")[0][0]
    assert mouse.connection == "usb" and mouse.read("dongle_light") is None
    assert mouse.write("sensor", {"lod": 1}) == {"lod": 1}
    assert mouse.hub_battery() is None
    with pytest.raises(AmError, match="dongle"):
        mouse.write("dongle_light", {"on": False})


def test_plain_status_costs_one_block_read(m100, run):
    code, out, _ = run(["--mouse", "100", "status"], m100[1])
    assert code == 0 and "model          .100" in out and "polling rate   4000 Hz" in out
    assert "mouse light    on  #FF823C" in out and "dongle light   on  solid  #FFB955  bright=4/4" in out
    assert set(m100[1].sent_cmds()) <= {214, 133, 211, 135, 136}
    assert m100[1].sent_cmds().count(211) == 1


def test_status_json_keeps_the_2_2_0_keys(m100, run):
    s = json.loads(run(["--mouse", "100", "--json", "status"], m100[1])[1])[0]
    assert set(s) >= {"model", "connection", "battery", "hub_battery", "profile", "polling_rate",
                      "mouse_light", "dongle_light", "mouse_offline"}
    assert s["dongle_light"]["effect"] == 1 and s["dongle_light"]["effect_name"] == "solid"
    assert s["mouse_light"] == {"on": True, "colour": "#FF823C"}


def test_cli_gaps_closed_and_status_keys_kept(m100, run):
    assert run(["light", "brightness", "2"], m100[1])[0] == 0
    assert run(["lod", "1"], m100[1])[0] == 0 and run(["profile"], m100[1])[0] == 0
    assert run(["dpi"], m100[1])[0] == 0 and run(["remap"], m100[1])[0] == 0
    assert run(["mouse-light", "color", "#00FF00", "--charged"], m100[1])[0] == 0
    assert m100[1].sent_packet(83)[57:60] == bytes([0, 0xFF, 0])
    assert run(["angle", "off"], m100[1])[0] == 0 and run(["sleep", "bt", "never"], m100[1])[0] == 0
    s = json.loads(run(["--json", "status", "--full"], m100[1])[1])[0]
    assert {"model", "connection", "battery", "hub_battery", "profile", "polling_rate",
            "mouse_light", "dongle_light", "mouse_offline", "sensor", "power", "dpi", "buttons"} <= set(s)


def test_raw_is_not_offered(m100, run):
    code, out, _ = run(["raw", "133"], m100[1])
    assert code == 0 and "skipped" in out


def test_export_import_roundtrip(m100, run, tmp_path):
    path = str(tmp_path / "p.json")
    assert run(["export", path], m100[1])[0] == 0
    run(["lod", "1"], m100[1])
    run(["remap", "back", "f23"], m100[1])
    code, out, _ = run(["import", path], m100[1])
    assert code == 0 and m100[0].read("sensor")["lod"] == 0 and m100[0].read("buttons")["back"] == "back"


# Seen on the owner's .100 while it was in use: one parameter-block write was
# silently dropped (the same write worked every time it was repeated).

def test_a_dropped_block_write_is_sent_once_more(m100):
    m100[1].drop_first = {83}
    assert m100[0].write("power", {"sleep_24g": 210}) == {"sleep_24g": 210}
    assert m100[1].sent_cmds().count(83) == 2
    assert m100[0].read("power")["sleep_24g"] == 210


def test_a_block_write_is_not_retried_forever(m100):
    m100[1].drop_writes = {83}
    with pytest.raises(NotConfirmed):
        m100[0].write("power", {"sleep_24g": 210})
    assert m100[1].sent_cmds().count(83) == 2


def test_dropped_dpi_and_toggle_writes_are_retried_too(m100):
    m100[1].drop_first = {84, 101}
    assert m100[0].write("dpi", {"stages": {6: {"x": 1000}}})
    assert m100[0].write("sensor", {"angle": 5}) == {"angle": 5}
    assert m100[1].sent_cmds().count(84) == 2 and m100[1].sent_cmds().count(101) == 2
