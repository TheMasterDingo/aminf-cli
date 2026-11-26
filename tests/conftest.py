import json
import pathlib

import pytest

from aminf.cli import main

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    """A captured-replies fixture by short name, e.g. '100r2-dongle'."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def set_path(doc, path, value):
    """Set doc[path[0]][path[1]]... = value."""
    for key in path[:-1]:
        doc = doc[key]
    doc[path[-1]] = value


@pytest.fixture
def run(capsys):
    """run(args, backend) -> (exit code, stdout, stderr)."""
    def _run(args, backend):
        capsys.readouterr()
        code = main(list(args), backend=backend)
        out, err = capsys.readouterr()
        return code, out, err
    return _run


@pytest.fixture
def write_tmp(tmp_path):
    """write_tmp(doc) -> path of a JSON file holding doc."""
    def _write(doc):
        p = tmp_path / "doc.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return str(p)
    return _write


@pytest.fixture(autouse=True)
def fast_timing(monkeypatch):
    """Simulated mice answer at once; don't wait like real hardware."""
    try:
        from aminf.race.device import RaceMouse
    except ImportError:
        return
    monkeypatch.setattr(RaceMouse, "TIMEOUT", 0.02)
    monkeypatch.setattr(RaceMouse, "RELAY_TIMEOUT", 0.02)
    monkeypatch.setattr(RaceMouse, "ACK_TIMEOUT", 0.02)
    monkeypatch.setattr(RaceMouse, "VERIFY_DELAY", 0.0)
    monkeypatch.setattr(RaceMouse, "POLL", 0.0)


@pytest.fixture
def r2():
    """(mouse, fake): a .100R2 on its dongle, as captured from the owner's."""
    from aminf.discover import open_mice
    from tests.fakes import FakeRace
    fake = FakeRace(load("100r2-dongle"), "100r2", dongle=True)
    mouse = open_mice(fake)[0][0]
    yield mouse, fake
    mouse.close()


@pytest.fixture
def r2fake():
    from tests.fakes import FakeRace
    return FakeRace(load("100r2-dongle"), "100r2", dongle=True)
