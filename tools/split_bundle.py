"""Split long lines of the configurator's minified bundle into readable files.

The bundle is Angry Miao's code and is never committed; this only makes it
possible to read the parts aminf's protocol tables were transcribed from.

usage: python tools/split_bundle.py <bundle.js> <outdir> <line> [<line> ...]

Writes <outdir>/L<line>.js for each 1-based bundle line: a newline is put
before every function / const / class / let / var that follows ';' or '}',
then anything still longer than 1400 characters is wrapped at the last
';', '}' or ','.
"""
import pathlib
import re
import sys

MAX = 1400
BOUNDARY = re.compile(
    r"(?<=[;}])(?=(?:async function |function |const |class |let |var ))")


def wrap(text):
    out = []
    while len(text) > MAX:
        cut = -1
        for ch in (";", "}", ","):
            cut = text.rfind(ch, 200, MAX)
            if cut != -1:
                break
        if cut == -1:
            cut = MAX - 1
        out.append(text[:cut + 1])
        text = text[cut + 1:]
    out.append(text)
    return out


def split_line(text):
    pieces = []
    for stmt in BOUNDARY.split(text):
        pieces.extend(wrap(stmt))
    return pieces


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    src, outdir = pathlib.Path(argv[0]), pathlib.Path(argv[1])
    lines = src.read_text(encoding="utf-8").split("\n")
    outdir.mkdir(parents=True, exist_ok=True)
    for n in (int(x) for x in argv[2:]):
        pieces = split_line(lines[n - 1])
        (outdir / f"L{n}.js").write_text("\n".join(pieces), encoding="utf-8")
        print(f"L{n}: {len(lines[n - 1])} chars -> {len(pieces)} lines")


if __name__ == "__main__":
    main(sys.argv[1:])
