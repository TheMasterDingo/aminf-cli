"""Build aminf as a standalone Windows program.

    pip install pyinstaller
    python tools/build_exe.py            ->  dist/aminf.exe

The result needs nothing installed: Python and hidapi are inside it. Run it
from a terminal, e.g. `aminf.exe status`.
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def build():
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--onefile", "--console",
        "--name", "aminf", "--clean", "--noconfirm",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
        "--hidden-import", "hid",
        str(ROOT / "aminf.py"),
    ], check=True, cwd=ROOT)
    return ROOT / "dist" / "aminf.exe"


if __name__ == "__main__":
    print(build())
