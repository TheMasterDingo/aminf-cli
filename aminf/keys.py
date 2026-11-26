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


# ------------------------------------------------------------- full tables
#
# Lower-case names for the action grammar (`aminf remap back ctrl+shift+a`).
# Values are USB HID keyboard usages; any usage 0x04-0xE7 can also be given
# as a number. Arrow keys are `arrow-left` etc. because `left` and `right`
# already mean the mouse buttons.

MODS = ("ctrl", "shift", "alt", "win")

KEY_USAGES = {}
KEY_USAGES.update({chr(ord("a") + i): 0x04 + i for i in range(26)})
KEY_USAGES.update({str(i): 0x1D + i for i in range(1, 10)})
KEY_USAGES["0"] = 0x27
KEY_USAGES.update({
    "enter": 0x28, "esc": 0x29, "backspace": 0x2A, "tab": 0x2B, "space": 0x2C,
    "minus": 0x2D, "equal": 0x2E, "lbracket": 0x2F, "rbracket": 0x30,
    "backslash": 0x31, "semicolon": 0x33, "quote": 0x34, "grave": 0x35,
    "comma": 0x36, "period": 0x37, "slash": 0x38, "capslock": 0x39,
    "printscreen": 0x46, "scrolllock": 0x47, "pause": 0x48, "insert": 0x49,
    "home": 0x4A, "pageup": 0x4B, "delete": 0x4C, "end": 0x4D,
    "pagedown": 0x4E, "arrow-right": 0x4F, "arrow-left": 0x50,
    "arrow-down": 0x51, "arrow-up": 0x52, "numlock": 0x53, "menu": 0x65,
})
KEY_USAGES.update({k.lower(): v for k, v in FKEYS.items()})
USAGE_NAMES = {v: k for k, v in KEY_USAGES.items()}

# Consumer-page usages the configurator offers as "multimedia" keys.
MEDIA = {
    "play": 0x00CD, "stop": 0x00B7, "next": 0x00B5, "prev": 0x00B6,
    "mute": 0x00E2, "vol-up": 0x00E9, "vol-down": 0x00EA, "player": 0x0183,
    "calculator": 0x0192, "email": 0x018A, "computer": 0x0194,
    "refresh": 0x0227, "search": 0x0221, "home": 0x0223,
    "brightness-up": 0x006F, "brightness-down": 0x0070,
}
MEDIA_NAMES = {v: k for k, v in MEDIA.items()}
