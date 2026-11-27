"""What a release must have: a README that matches the program, a single-file
build that runs, and sources an older Python can still parse."""
import ast
import pathlib
import subprocess
import sys

from aminf.cli import build_parser
from aminf.models import MODELS
from tools import build_pyz

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_pyz_runs(tmp_path):
    out = build_pyz.build(tmp_path)
    r = subprocess.run([sys.executable, str(out), "--version"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "3.0.0" in r.stdout


def test_readme_documents_every_command():
    commands = build_parser()._subparsers._group_actions[0].choices
    for name in commands:
        assert f"aminf {name}" in README, name


def test_readme_lists_udev_ids_for_every_model():
    for model in MODELS.values():
        for _vid, pid in model.usb_ids[:1] + model.dongle_ids:
            assert f"{pid:04x}" in README, (model.name, hex(pid))


def test_readme_names_every_model_and_the_real_dpi_limit():
    for model in MODELS.values():
        assert model.name in README
    assert "26000" in README and "3.0.0" in README


def test_sources_parse_as_python_3_9():       # only 3.13 is installed here, so pin the syntax floor
    for f in list((ROOT / "aminf").rglob("*.py")) + [ROOT / "aminf.py"]:
        ast.parse(f.read_text(encoding="utf-8"), feature_version=(3, 9))


def test_pyproject_version_is_static_and_matches():
    # `pip install .` must not import aminf to learn the version: next to the
    # package sits the launcher aminf.py, and setuptools picks that up instead.
    import aminf
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{aminf.__version__}"' in text
    assert "dynamic" not in text


def test_pyproject_lists_every_package():
    # found by a clean `pip install .`: the sub-packages were left out
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for init in (ROOT / "aminf").rglob("__init__.py"):
        name = ".".join(init.parent.relative_to(ROOT).parts)
        assert f'"{name}"' in text, name
