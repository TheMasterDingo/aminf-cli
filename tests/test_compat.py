"""3.0 must print exactly what 2.2.0 printed for the commands it already had."""
import json
import pathlib

import pytest

import aminf
from aminf.cli import main
from tests.fakes import EmptyBackend

GOLDEN = sorted(pathlib.Path(__file__).parent.glob("golden/*.txt"))


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.stem)
def test_dry_run_output_unchanged(path, capsys):
    argv = json.loads(path.with_suffix(".args").read_text())
    assert main(argv, backend=EmptyBackend()) == 0
    assert capsys.readouterr().out == path.read_text()


def test_no_device_exits_1(capsys):
    assert main(["status"], backend=EmptyBackend()) == 1


def test_version():
    assert aminf.__version__ == "3.0.0"
