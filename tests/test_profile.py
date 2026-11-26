"""Export and import of the active profile."""
import copy
import json

import pytest

from aminf.device import SECTIONS
from aminf.errors import AmError
from aminf.profile import apply, diff, load, snapshot, validate
from aminf.race.codec import WRITES
from tests.conftest import FIXTURES, set_path


def writes(fake):
    return [c for c in fake.sent_cmds() if c in WRITES]


def statuses(results):
    return {s: st for s, st, _ in results}


def test_snapshot_shape(r2):
    doc = snapshot(r2[0])
    assert (doc["format"], doc["version"], doc["model"], doc["profile"]) == ("aminf-profile", 1, ".100R2", 1)
    assert set(doc["sections"]) == set(SECTIONS)
    json.dumps(doc)                                  # plain JSON all the way down


def test_restore_after_changes_sends_only_differences(r2):
    doc = snapshot(r2[0])
    r2[0].write("sensor", {"lod": 3})
    r2[0].write("buttons", {"forward": "f24"})
    r2[1].sent.clear()
    results = apply(r2[0], doc)
    assert sorted(set(writes(r2[1]))) == [12482, 12492]
    assert snapshot(r2[0]) == doc
    assert statuses(results)["sensor"] == "applied" and statuses(results)["dpi"] == "unchanged"


def test_unchanged_import_writes_nothing(r2):
    doc = snapshot(r2[0])
    r2[1].sent.clear()
    assert set(statuses(apply(r2[0], doc)).values()) == {"unchanged"}
    assert writes(r2[1]) == []


def test_only_limits_sections(r2):
    doc = snapshot(r2[0])
    doc["sections"]["sensor"]["lod"] = 2
    doc["sections"]["dpi"]["count"] = 4
    results = statuses(apply(r2[0], doc, only=["sensor"]))
    assert r2[0].read("dpi")["count"] == 8 and r2[0].read("sensor")["lod"] == 2
    assert "dpi" not in results


def test_dry_run_reports_and_writes_nothing(r2):
    doc = snapshot(r2[0])
    doc["sections"]["sensor"]["lod"] = 2
    r2[1].sent.clear()
    results = apply(r2[0], doc, dry_run=True)
    assert statuses(results)["sensor"] == "would change" and writes(r2[1]) == []
    assert "lod" in [d for s, _, d in results if s == "sensor"][0]


def test_dpi_restore(r2):
    doc = snapshot(r2[0])
    r2[0].write("dpi", {"stages": {7: {"x": 1000, "colour": "#00FF00"}}, "count": 4, "active": 1, "button": False})
    apply(r2[0], doc, only=["dpi"])
    assert r2[0].read("dpi") == doc["sections"]["dpi"]


def test_opaque_buttons_are_never_written(r2):
    doc = snapshot(r2[0])
    doc["sections"]["buttons"]["back"] = "opaque:0801"
    r2[1].sent.clear()
    apply(r2[0], doc, only=["buttons"])
    assert 12482 not in r2[1].sent_cmds()


def test_read_only_keys_are_ignored(r2):
    doc = snapshot(r2[0])
    doc["sections"]["power"]["hw_debounce_press"] = 3
    assert statuses(apply(r2[0], doc))["power"] == "unchanged"


@pytest.mark.parametrize("path,value", [                                # Review Focus 2
    (("sections", "sensor", "lod"), "high"), (("sections", "buttons", "sideways"), "f13"),
    (("sections", "dpi", "stages", 0, "x"), 70000), (("sections", "buttons", "back"), "nonsense"),
    (("sections", "sensor", "motion_sync"), 1), (("sections", "power", "sleep_bt"), "sometimes"),
    (("sections", "sensor", "wobble"), 3), (("sections", "light", "colour"), "red")])
def test_bad_file_is_refused_before_any_write(r2, path, value):
    doc = snapshot(r2[0])
    doc["sections"]["sensor"]["lod"] = 3             # a valid change that must NOT be applied
    set_path(doc, path, value)
    r2[1].sent.clear()
    with pytest.raises(AmError, match=str(path[-1])):
        apply(r2[0], doc)
    assert writes(r2[1]) == []


@pytest.mark.parametrize("doc", [[], {}, {"format": "aminf-profile", "version": 99, "sections": {}},
                                 {"format": "aminf-profile", "version": 1, "sections": {"nope": {}}}])
def test_not_a_profile(r2, doc):
    with pytest.raises(AmError):
        validate(doc, r2[0].model)


def test_import_is_looser_than_the_command_line(r2):
    with pytest.raises(AmError):
        r2[0].write("dpi", {"stages": {7: {"x": 31000}}})
    doc = snapshot(r2[0])
    doc["sections"]["dpi"]["stages"][7].update(x=31000, y=31000)
    assert any("31000" in w for w in validate(doc, r2[0].model))
    apply(r2[0], doc)
    assert r2[0].read("dpi")["stages"][7]["x"] == 31000


def test_other_model_is_refused_without_only(r2):
    doc = snapshot(r2[0])
    doc["model"] = ".100"
    with pytest.raises(AmError, match="--only"):
        apply(r2[0], doc)
    apply(r2[0], doc, only=["sensor"])


def test_failure_midway_is_reported(r2):                                # Review Focus 5
    doc = snapshot(r2[0])
    doc["sections"]["sensor"]["lod"] = 3
    doc["sections"]["dpi"]["count"] = 4
    r2[1].drop_writes = {12492}
    results = statuses(apply(r2[0], doc))
    assert results["sensor"] == "failed" and results["dpi"] == "skipped"
    assert r2[0].read("dpi")["count"] == 8


def test_diff_is_minimal():
    cur = {"sensor": {"lod": 1, "motion_sync": True}, "dpi": {
        "stages": [{"x": 400, "y": 400, "colour": "#FFFFFF"}, {"x": 800, "y": 800, "colour": None}],
        "count": 2, "active": 0, "button": True}}
    want = copy.deepcopy(cur)
    want["sensor"]["lod"] = 2
    want["dpi"]["stages"][1]["y"] = 900
    assert diff(cur["sensor"], want["sensor"], "sensor") == {"lod": 2}
    assert diff(cur["dpi"], want["dpi"], "dpi") == {"stages": {1: {"x": 800, "y": 900}}}


def test_configurator_97_file():
    doc = load(FIXTURES / "configurator-97-a.json")
    s = doc["sections"]
    assert s["sensor"]["polling_rate"] == 4000 and s["sensor"]["motion_sync"] is True
    assert s["sensor"]["profile_key"] is True and s["sensor"]["angle_enabled"] is False
    assert s["power"] == {"sleep_24g": 180, "sleep_bt": 60, "debounce_wired": 0, "debounce_24g": 0, "debounce_bt": 0}
    assert s["dpi"]["count"] == 1 and s["dpi"]["stages"][0] == {"x": 1800, "y": 1800, "colour": "#FFFFFF"}
    assert s["dpi"]["button"] is False and s["dpi"]["active"] == 0 and len(s["dpi"]["stages"]) == 8
    assert s["buttons"]["wheel-up"] == "wheel-up" and s["buttons"]["left"] == "left"
    assert s["light"] == {"on": True, "effect": "solid", "speed": 1, "colour": "#FF823C"}
    assert s["dongle_light"]["sat1"] == 75 and s["dongle_light"]["brightness"] == 73
    assert s["dongle_light"]["effect"] == "solid" and s["dongle_light"]["on"] is True


def test_older_configurator_file_loads_too():
    doc = load(FIXTURES / "configurator-97-b.json")
    assert doc["sections"]["dongle_light"]["brightness"] == 120
    assert "profile_key" not in doc["sections"]["sensor"]


def test_configurator_file_applies_to_a_97():
    from aminf.discover import open_mice
    from tests.conftest import load as fixture
    from tests.fakes import FakeRace
    fake = FakeRace(fixture("97-dongle"), "97")
    mouse = open_mice(fake)[0][0]
    doc = load(FIXTURES / "configurator-97-a.json")
    validate(doc, mouse.model)
    results = statuses(apply(mouse, doc))
    assert "failed" not in results.values()
    assert mouse.read("dongle_light")["brightness"] == 73      # the one value that differed


def test_configurator_keymap_with_a_key_and_a_macro(tmp_path):
    data = json.loads((FIXTURES / "configurator-97-a.json").read_text())
    data["KeyMatrix"]["0x04"] = [6, "", "0x73", "1100"]            # ctrl+shift+F24
    data["KeyMatrix"]["0x05"] = [6, "0x00CD", "", "0000"]          # play/pause
    data["KeyMatrix"]["0x03"] = [8, 0, 1, "M_1", ["0x04", "0x000000", "0x000032"]]
    p = tmp_path / "c.json"
    p.write_text(json.dumps(data))
    doc = load(p)
    b = doc["sections"]["buttons"]
    assert b["forward"] == "ctrl+shift+f24" and b["back"] == "media:play"
    assert "middle" not in b and any("macro" in n for n in doc["notes"])


def test_export_then_import_from_the_command_line(run, r2fake, tmp_path):
    path = str(tmp_path / "p.json")
    code, out, _ = run(["export", path], r2fake)
    assert code == 0 and ".100R2" in out
    doc = json.loads(open(path).read())
    assert doc["format"] == "aminf-profile" and doc["sections"]["sensor"]["lod"] == 1
    run(["lod", "3"], r2fake)
    code, out, _ = run(["import", path], r2fake)
    assert code == 0 and "sensor" in out and "applied" in out
    assert r2fake.sent_data(12492) == bytes([1])


def test_import_dry_run_from_the_command_line(run, r2fake, tmp_path):
    path = str(tmp_path / "p.json")
    run(["export", path], r2fake)
    run(["lod", "3"], r2fake)
    r2fake.sent.clear()
    code, out, _ = run(["--dry-run", "import", path], r2fake)
    assert code == 0 and "would change" in out and writes(r2fake) == []


def test_import_notice_when_profile_differs(run, r2, write_tmp):
    doc = snapshot(r2[0])
    doc["profile"] = 0
    code, out, _ = run(["import", write_tmp(doc)], r2[1])
    assert code == 0 and "profile 0" in out


def test_import_failure_exits_2(run, r2fake, tmp_path, write_tmp):
    from aminf.discover import open_mice
    mouse = open_mice(r2fake)[0][0]
    doc = snapshot(mouse)
    mouse.close()
    doc["sections"]["sensor"]["lod"] = 3
    r2fake.drop_writes = {12492}
    code, out, _ = run(["import", write_tmp(doc)], r2fake)
    assert code == 2 and "failed" in out


def test_import_bad_file_exits_2(run, r2fake, tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{not json")
    assert run(["import", str(p)], r2fake)[0] == 2
    assert run(["import", str(tmp_path / "missing.json")], r2fake)[0] == 2


def test_export_needs_one_mouse(run, two_mice_fake, tmp_path):
    code, _, err = run(["export", str(tmp_path / "p.json")], two_mice_fake)
    assert code == 2 and "--mouse" in err
