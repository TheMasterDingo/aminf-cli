"""The command line, driven end to end against simulated mice."""
import json

from aminf.race.codec import READS


def test_dpi_show(run, r2fake):
    code, out, _ = run(["dpi"], r2fake)
    assert code == 0 and "stage 4" in out and "1600" in out and "of 8" in out


def test_dpi_show_json(run, r2fake):
    d = json.loads(run(["--json", "dpi"], r2fake)[1])
    assert d["model"] == ".100R2" and d["active"] == 3 and d["stages"][3]["x"] == 1600


def test_dpi_set_is_one_based(run, r2fake):
    code, out, _ = run(["dpi", "set", "8", "26000"], r2fake)
    assert code == 0 and "26000" in out
    assert r2fake.sent_data(12484)[0] == 7


def test_dpi_set_with_y_and_colour(run, r2fake):
    assert run(["dpi", "set", "8", "2000", "--y", "1000", "--color", "#00FF00"], r2fake)[0] == 0
    assert r2fake.sent_data(12484) == bytes([7]) + (2000).to_bytes(2, "little") + (1000).to_bytes(2, "little")
    assert r2fake.sent_data(2573)[:2] == (25128).to_bytes(2, "little")


def test_dpi_use_stages_button(run, r2fake):
    assert run(["dpi", "use", "2"], r2fake)[0] == 0 and r2fake.sent_data(12487) == bytes([1])
    assert run(["dpi", "stages", "4"], r2fake)[0] == 0 and r2fake.sent_data(12486) == bytes([4])
    assert run(["dpi", "button", "off"], r2fake)[0] == 0 and r2fake.sent_data(12522) == bytes([0])


def test_dpi_out_of_range_exits_2(run, r2fake):
    code, _, err = run(["dpi", "set", "9", "800"], r2fake)
    assert code == 2 and ".100R2" in err


def test_remap_lists_every_button(run, r2fake):
    out = run(["remap"], r2fake)[1]
    assert all(b in out for b in ("left", "wheel-down", "bottom"))
    assert "(default)" in out


def test_remap_one_button(run, r2fake):
    out = run(["remap", "bottom"], r2fake)[1]
    assert out.split() == ["bottom", "disabled"]


def test_remap_json(run, r2fake):
    assert json.loads(run(["--json", "remap"], r2fake)[1])["forward"] == "forward"


def test_old_remap_spelling_still_works(run, r2fake):
    code, out, _ = run(["remap", "forward", "F24"], r2fake)
    assert code == 0 and out.strip() == "forward -> F24"
    assert run(["remap", "forward", "default"], r2fake)[1].strip() == "forward -> forward (default)"


def test_remap_refusal_exits_2(run, r2fake):
    code, _, err = run(["remap", "left", "f13"], r2fake)
    assert code == 2 and "left click" in err


def test_sleep_show_and_set(run, r2fake):
    assert "180" in run(["sleep"], r2fake)[1]
    assert run(["sleep", "bt", "never"], r2fake)[0] == 0
    assert "never" in run(["sleep"], r2fake)[1]
    assert run(["sleep", "2.4g", "300"], r2fake)[0] == 0


def test_debounce_all_three(run, r2fake):
    run(["debounce", "4"], r2fake)
    assert r2fake.sent_data(12520) == bytes([4, 4, 4])


def test_debounce_one(run, r2fake):
    run(["debounce", "2.4g", "6"], r2fake)
    assert r2fake.sent_data(12520) == bytes([1, 6, 1])
    assert "10" in run(["debounce"], r2fake)[1]          # hardware debounce is shown


def test_angle(run, r2fake):
    run(["angle", "7"], r2fake)
    assert r2fake.sent_data(12504) == bytes([1, 7])
    run(["angle", "off"], r2fake)
    assert r2fake.sent_data(12504)[0] == 0


def test_profile_show_and_switch(run, r2fake):
    assert run(["profile"], r2fake)[1].split()[-1] == "1"
    code, out, _ = run(["profile", "2"], r2fake)
    assert code == 0 and "profile -> 2" in out


def test_toggle_new_features(run, r2fake):
    code, out, _ = run(["toggle", "fpsmode", "on"], r2fake)
    assert code == 0 and "battery" in out               # the configurator's own warning
    assert run(["toggle", "profilekey", "off"], r2fake)[0] == 0
    assert r2fake.sent_data(12524) == bytes([0])


def test_lod_uses_the_models_range(run, r2fake):
    assert run(["lod", "4"], r2fake)[0] == 0
    assert run(["lod", "5"], r2fake)[0] == 2


def test_unconfirmed_write_exits_2(run, r2fake):
    r2fake.drop_writes = {12492}
    code, _, err = run(["lod", "3"], r2fake)
    assert code == 2 and ".100R2" in err


def test_plain_status_reads_no_more_than_2_2_0(run, r2fake):
    code, out, _ = run(["status"], r2fake)
    assert code == 0 and "model          .100R2" in out
    assert set(r2fake.sent_cmds()) <= {12495, 12303, 12489, 12305, 12493, 12499, 12501, 12503, 2572, 12302}


def test_status_json_keeps_the_2_2_0_keys(run, r2fake):
    s = json.loads(run(["--json", "status"], r2fake)[1])[0]
    assert {"model", "connection", "battery", "hub_battery", "profile", "polling_rate", "lod",
            "motion_sync", "angle_snapping", "ripple_control", "mouse_light", "mouse_colour",
            "dongle_light"} <= set(s)
    assert isinstance(s["mouse_light"]["effect"], int) and isinstance(s["dongle_light"]["effect"], int)
    assert "dpi" not in s


def test_status_full_json_is_a_superset(run, r2fake):
    s = json.loads(run(["--json", "status", "--full"], r2fake)[1])[0]
    assert {"model", "connection", "battery", "hub_battery", "profile", "polling_rate", "lod",
            "motion_sync", "angle_snapping", "ripple_control", "mouse_light", "mouse_colour",
            "dongle_light", "sensor", "power", "dpi", "buttons"} <= set(s)


def test_status_full_text(run, r2fake):
    out = run(["status", "--full"], r2fake)[1]
    assert "dpi" in out and "sleep" in out and "debounce" in out


def test_status_when_the_mouse_is_asleep(run, r2fake):
    r2fake.offline = True
    code, out, _ = run(["status"], r2fake)
    assert code == 0 and "off or out of range" in out and "None" not in out


def test_write_when_the_mouse_is_asleep(run, r2fake):
    r2fake.offline = True
    code, _, err = run(["lod", "2"], r2fake)
    assert code == 2 and "off or out of range" in err


def test_light_status_keeps_its_2_2_0_shape(run, r2fake):
    d = json.loads(run(["light", "status"], r2fake)[1])
    assert d["model"] == ".100R2" and isinstance(d["effect"], int) and "hue1" in d
    m = json.loads(run(["mouse-light", "status"], r2fake)[1])
    assert set(m) == {"on", "effect", "speed", "colour", "model"}


def test_light_commands(run, r2fake):
    assert run(["light", "on"], r2fake)[1].strip() == "dongle light -> on"
    assert run(["light", "toggle"], r2fake)[1].strip() == "dongle light -> off"
    assert run(["light", "brightness", "120"], r2fake)[0] == 0 and r2fake.sent_data(12301)[12] == 120
    assert run(["light", "effect", "breathing"], r2fake)[0] == 0 and r2fake.sent_data(12301)[1] == 2
    code, _, err = run(["light", "effect", "disco"], r2fake)
    assert code == 2 and "breathing" in err
    assert run(["mouse-light", "color", "#FF823C"], r2fake)[1].strip() == "mouse colour -> #FF823C"
    assert run(["all", "off"], r2fake)[1].split("\n")[:2] == ["dongle light -> off", "mouse light -> off"]


def test_two_mice_unsupported_is_a_notice(run, two_mice_fake):               # Review Focus 4
    code, out, err = run(["remap", "back", "profile-switch"], two_mice_fake)
    assert code == 0 and "skipped" in out and err == ""
    assert "--- .97" in out and "--- .100R2" in out


def test_dry_run_sends_nothing(run, r2fake):
    code, out, _ = run(["--dry-run", "lod", "3"], r2fake)
    assert code == 0 and "would send" in out and r2fake.sent == []


def test_raw_is_read_only(run, r2fake):
    assert run(["raw", "12489"], r2fake)[0] == 0
    assert run(["raw", "12494"], r2fake)[0] == 2 and run(["raw", "12492"], r2fake)[0] == 2
    assert 12494 not in r2fake.sent_cmds() and 12492 not in r2fake.sent_cmds()
    assert all(c in READS for c in r2fake.sent_cmds())


def test_quiet(run, r2fake):
    assert run(["--quiet", "lod", "2"], r2fake)[1] == ""
