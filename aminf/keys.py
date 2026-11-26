"""Key names and HID usages.

Keys are HID keyboard usages. The web configurator's key picker stops at
F12, but both firmwares accept any usage from 0x04 to 0xE7, so F13-F24 can
be set from here.
"""
from __future__ import annotations

from aminf.errors import AmError

FKEYS = {f"F{i}": 0x39 + i for i in range(1, 13)}             # F1 0x3A..F12 0x45
FKEYS.update({f"F{i}": 0x5B + i for i in range(13, 25)})      # F13 0x68..F24 0x73
KEY_NAMES = {v: k for k, v in FKEYS.items()}
KEY_NAMES[0x47] = "ScrollLock"
MODIFIER_NAMES = ("Ctrl", "Shift", "Alt", "Win")


def parse_remap_key(text):
    """'default' -> None, 'F24' -> 0x73, '0x47' -> 0x47."""
    t = text.strip()
    if t.lower() == "default":
        return None
    if t.upper() in FKEYS:
        return FKEYS[t.upper()]
    try:
        v = int(t, 0)
    except ValueError:
        v = -1
    if not 0x04 <= v <= 0xE7:
        raise AmError(f"unknown key '{text}' -- use F1-F24, a HID usage "
                      f"like 0x47, or 'default'")
    return v


def key_name(usage, modifiers=()):
    return "+".join(list(modifiers) +
                    [KEY_NAMES.get(usage, f"key 0x{usage:02X}")])
