#!/usr/bin/env python3
"""aminf -- command-line control for Angry Miao AM Infinity mice.

Supports the AM Infinity .97, .100 and .100R2. Talks to each mouse over the
same vendor-defined HID channel its official web configurator uses, so no
driver and no browser is needed. Useful for scripting: killing the lighting
along with the rest of your desk, switching polling rate or DPI per game,
reading battery into a status bar, and so on.

This file only starts the program; the code is in the aminf/ folder beside
it. The protocols were transcribed from the configurator's own JavaScript
bundle, so the frames sent are the frames the official tool sends.

    aminf status
    aminf light off
    aminf rate 4000
    aminf dpi set 2 1600
    aminf remap forward F24           # any button -> a key
    aminf export my-mouse.json

See the README for every command. MIT licensed. Not affiliated with or endorsed by Angry Miao. Use at your own
risk -- see the safety notes in the README.
"""
import sys

from aminf.cli import main

if __name__ == "__main__":
    sys.exit(main())
