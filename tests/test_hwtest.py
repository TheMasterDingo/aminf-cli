"""The hardware test tool, run against a simulated mouse."""
import json

from aminf.race.codec import WRITES
from tools import hwtest


def test_hwtest_restores_and_reports(r2fake, tmp_path):
    assert hwtest.run("100r2", backend=r2fake, out_dir=tmp_path, yes=True) == 0
    report = json.loads((tmp_path / "100r2-dongle.json").read_text())
    assert {c["result"] for c in report["checks"]} <= {"verified", "unsupported"}
    assert report["restored"] is True and report["model"] == ".100R2"
    names = {c["check"] for c in report["checks"]}
    assert {"polling_rate", "lod", "sleep_bt", "debounce_24g", "dpi stage 8", "dpi stage 8 colour",
            "dpi count", "dpi use", "angle", "angle on/off", "remap forward", "remap wheel-down",
            "light on/off", "dongle light on/off", "dongle light colour", "dongle light brightness",
            "dongle light speed", "dongle light effect", "profile switch"} <= names
    assert report["checks"][0]["check"] == "profile switch"      # first, while nothing else is changed
    assert "light colour" not in names                           # .100R2: not verified against the bundle yet
    assert (tmp_path / "100r2-dongle-snapshot.json").exists()


def test_hwtest_never_touches_left_or_right(r2fake, tmp_path):
    hwtest.run("100r2", backend=r2fake, out_dir=tmp_path, yes=True)
    remapped = {x[0] for _, c, x in r2fake.sent if c == 12482}
    assert remapped and not remapped & {1, 2}


def test_hwtest_never_restores_into_the_wrong_profile(r2fake, tmp_path):
    r2fake.stick_profile_after = 1            # the switch away works, the switch back is ignored
    assert hwtest.run("100r2", backend=r2fake, out_dir=tmp_path, yes=True) == 1
    assert not [c for c in r2fake.sent_cmds() if c in WRITES and c != 12488]   # nothing else was touched
    report = json.loads((tmp_path / "100r2-dongle.json").read_text())
    assert report["restored"] is False


def test_hwtest_fails_when_a_write_is_dropped(r2fake, tmp_path):
    r2fake.drop_writes = {12492}
    assert hwtest.run("100r2", backend=r2fake, out_dir=tmp_path, yes=True) == 1
    report = json.loads((tmp_path / "100r2-dongle.json").read_text())
    assert [c["result"] for c in report["checks"] if c["check"] == "lod"] == ["failed"]
    assert report["restored"] is True               # everything else was put back


def test_hwtest_stops_if_the_mouse_is_asleep(r2fake, tmp_path):
    r2fake.offline = True
    assert hwtest.run("100r2", backend=r2fake, out_dir=tmp_path, yes=True) == 1
    assert not [c for c in r2fake.sent_cmds() if c in WRITES]


def test_hwtest_without_the_mouse(tmp_path):
    from tests.fakes import EmptyBackend
    assert hwtest.run("100r2", backend=EmptyBackend(), out_dir=tmp_path, yes=True) == 1


def test_hwtest_restores_after_the_mouse_dozes_off_mid_run(r2fake, tmp_path):
    r2fake.sleep_after_writes = 8
    woken = []

    def wake():
        woken.append(True)
        r2fake.offline = False

    assert hwtest.run("100r2", backend=r2fake, out_dir=tmp_path, yes=True, wake=wake) == 1
    report = json.loads((tmp_path / "100r2-dongle.json").read_text())
    assert woken and report["restored"] is True
    assert "not run" in {c["result"] for c in report["checks"]}


def test_hwtest_gives_up_politely_if_the_mouse_stays_asleep(r2fake, tmp_path, capsys):
    r2fake.sleep_after_writes = 8
    assert hwtest.run("100r2", backend=r2fake, out_dir=tmp_path, yes=True, wake=lambda: None) == 1
    out = capsys.readouterr().out
    assert "NOT RESTORED" in out and "import" in out and "asleep" in out


def test_97_gets_the_mouse_light_colour_check(tmp_path):
    from tests.conftest import load
    from tests.fakes import FakeRace
    fake = FakeRace(load("97-dongle"), "97")
    assert hwtest.run("97", backend=fake, out_dir=tmp_path, yes=True) == 0
    report = json.loads((tmp_path / "97-dongle.json").read_text())
    assert "light colour" in {c["check"] for c in report["checks"]}
