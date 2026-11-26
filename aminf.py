#!/usr/bin/env python3
"""aminf -- command-line control for Angry Miao AM Infinity mice.

Supports the AM Infinity .97 and the AM Infinity .100. Talks to each mouse
over the same vendor-defined HID channel its official web configurator uses,
so no driver and no browser is needed. Useful for scripting: killing the
lighting along with the rest of your desk, switching polling rate per game,
reading battery into a status bar, and so on.

The protocols were transcribed from the configurator's own JavaScript bundle,
so the frames sent here are the frames the official tool sends. The two mice
speak completely different protocols; the script picks the right one.

    aminf status
    aminf light off
    aminf light color '#FF4C00'
    aminf mouse-light off
    aminf all off                     # dongle + mouse
    aminf rate 4000
    aminf remap                       # what the side buttons send
    aminf remap forward F24           # side button -> F24
    aminf remap forward default       # back to a normal Forward button

MIT licensed. Not affiliated with or endorsed by Angry Miao. Use at your own
risk -- see the safety notes in the README.
"""
import sys

from aminf.cli import main

if __name__ == "__main__":
    sys.exit(main())
