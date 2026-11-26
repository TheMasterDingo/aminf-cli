"""What a button can be made to do, independent of the mouse's protocol.

An action is written as a short string on the command line and in exported
files:

    disabled
    left right middle forward back wheel-up wheel-down     a mouse function
    a  f13  space  0x47  ctrl+shift+a  win+f13              a key, with modifiers
    media:play  media:vol-up  media:0x00b8                  a multimedia key
    dpi-up  dpi-down  dpi-cycle
    profile-switch                                          (.100R2)
    opaque:<hex>      a macro or other binding aminf shows but never writes
    default           (input only) the button's own function
"""
from __future__ import annotations

from dataclasses import dataclass

from aminf.errors import AmError, Refused
from aminf.keys import KEY_USAGES, MEDIA, MEDIA_NAMES, MODS, USAGE_NAMES

MOUSE_FUNCTIONS = ("left", "right", "middle", "forward", "back",
                   "wheel-up", "wheel-down")
DPI_ACTIONS = ("up", "down", "cycle")
BUTTON_NAMES = MOUSE_FUNCTIONS + ("bottom",)


@dataclass(frozen=True)
class Action:
    kind: str            # mouse | key | media | dpi | disabled | profile-switch | opaque
    value: object = None  # button name | HID usage | consumer usage | up/down/cycle | bytes
    mods: tuple = ()     # for kind "key": a subset of MODS, in that order


LEFT = Action("mouse", "left")


def _number(text):
    try:
        return int(text, 0)
    except ValueError:
        return None


def parse_action(text):
    """The Action a string names, or None for 'default'."""
    t = str(text).strip().lower()
    if t == "default":
        return None
    if t in ("disabled", "profile-switch"):
        return Action(t)
    if t in MOUSE_FUNCTIONS:
        return Action("mouse", t)
    if t.startswith("dpi-") and t[4:] in DPI_ACTIONS:
        return Action("dpi", t[4:])
    if t.startswith("media:"):
        name = t[6:]
        usage = MEDIA.get(name)
        if usage is None and name.startswith("0x"):
            usage = _number(name)
        if usage is None or not 0 < usage <= 0xFFFF:
            raise AmError(f"unknown media key '{text}' -- use one of "
                          f"{', '.join(MEDIA)} or media:0x<usage>")
        return Action("media", usage)
    if t.startswith("opaque:"):
        try:
            return Action("opaque", bytes.fromhex(t[7:]))
        except ValueError:
            raise AmError(f"'{text}' is not valid hex") from None
    *mods, key = t.split("+")
    usage = KEY_USAGES.get(key)
    if usage is None and key.startswith("0x"):
        usage = _number(key)
    if usage is None or not 0x04 <= usage <= 0xE7:
        raise AmError(f"unknown action '{text}' -- use a key (a, f13, space, "
                      f"0x47), optionally with ctrl+ shift+ alt+ win+, or one "
                      f"of: disabled, {', '.join(MOUSE_FUNCTIONS)}, "
                      f"media:<name>, dpi-up, dpi-down, dpi-cycle, "
                      f"profile-switch, default")
    if any(m not in MODS for m in mods) or len(set(mods)) != len(mods):
        raise AmError(f"unknown modifier in '{text}' -- use "
                      f"{', '.join(MODS)}, each at most once")
    return Action("key", usage, tuple(m for m in MODS if m in mods))


def format_action(action):
    k = action.kind
    if k in ("disabled", "profile-switch"):
        return k
    if k == "mouse":
        return action.value
    if k == "dpi":
        return f"dpi-{action.value}"
    if k == "media":
        name = MEDIA_NAMES.get(action.value)
        return f"media:{name}" if name else f"media:0x{action.value:04x}"
    if k == "opaque":
        return "opaque:" + bytes(action.value).hex()
    key = USAGE_NAMES.get(action.value, f"0x{action.value:02x}")
    return "+".join(list(action.mods) + [key])


def check_left_click(mapping):
    """The configurator refuses to leave a mouse with no left click; so does
    aminf. `mapping` is {button: Action} for every button."""
    if LEFT not in mapping.values():
        raise Refused("at least one button must stay mapped to left click")
