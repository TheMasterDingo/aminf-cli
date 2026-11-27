"""Build aminf as one file.

    python tools/build_pyz.py [outdir]        ->  <outdir>/aminf.pyz   (default: dist)

The result runs with `python aminf.pyz status`. It contains the aminf
package only; hidapi is still installed separately (`pip install hidapi`),
because it ships a compiled library per platform.
"""
import pathlib
import shutil
import sys
import tempfile
import zipapp

ROOT = pathlib.Path(__file__).resolve().parent.parent


def build(outdir=None):
    outdir = pathlib.Path(outdir) if outdir else ROOT / "dist"
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / "aminf.pyz"
    with tempfile.TemporaryDirectory() as tmp:
        stage = pathlib.Path(tmp) / "app"
        shutil.copytree(ROOT / "aminf", stage / "aminf",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (stage / "__main__.py").write_text(
            "import sys\n\nfrom aminf.cli import main\n\nsys.exit(main())\n",
            encoding="utf-8")
        zipapp.create_archive(stage, target, interpreter="/usr/bin/env python3",
                              compressed=True)
    return target


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else None))
