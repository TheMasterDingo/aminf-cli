"""The .100 wire format: packets in, values out. No I/O here.

A different protocol from the .97 and .100R2. Every packet is 64 bytes in a
HID feature report:

    [cmd][6 parameter bytes][checksum][data...]
    checksum = 255 - (sum of the first 7 bytes & 255)

Most settings are not set one at a time. They live in two 64-byte blocks
that are read whole, changed in place and written back whole:

    parameters   read 211, write 83    rate, debounce, lift-off, sleep, light
    DPI          read 212, write 84    eight stages, per profile

Every layout below was transcribed from the configurator's JavaScript
bundle. Nothing is guessed.
"""
from __future__ import annotations

from aminf.actions import Action
from aminf.colour import hex_to_rgb, rgb_to_hex
from aminf.errors import AmError, Refused, Unsupported
from aminf.keys import MEDIA_NAMES, MODS

LEN = 64

GET_PARAMS, SET_PARAMS = 211, 83
GET_DPI, SET_DPI = 212, 84
GET_BATTERY = 214
GET_PROFILE, SET_PROFILE = 133, 5
GET_DONGLE_SWITCH, SET_DONGLE_SWITCH = 135, 7
GET_DONGLE_LIGHT, SET_DONGLE_LIGHT = 136, 8
GET_KEYMAP, SET_KEY = 208, 80
DONGLE_STATUS, FWD_OPEN, FWD_LENGTH, FWD_FETCH = 247, 246, 254, 252

# name -> (read command, write command)
TOGGLES = {"angle": (229, 101), "fps_mode": (226, 98),
           "dpi_button": (227, 99), "profile_key": (228, 100)}

# The only first bytes this module will ever put in a packet. Reset (2),
# clear-Bluetooth (97), macros (22, 87) and every boot or firmware command
# (64, 65, 127, 186, 192, 193, 248) are simply not in the list.
ALLOWED = frozenset({
    GET_PARAMS, SET_PARAMS, GET_DPI, SET_DPI, GET_BATTERY,
    GET_PROFILE, SET_PROFILE, GET_DONGLE_SWITCH, SET_DONGLE_SWITCH,
    GET_DONGLE_LIGHT, SET_DONGLE_LIGHT, GET_KEYMAP, SET_KEY,
    DONGLE_STATUS, FWD_OPEN, FWD_LENGTH, FWD_FETCH,
} | {c for pair in TOGGLES.values() for c in pair})


def _allowed(code):
    if code not in ALLOWED:
        raise Refused(f"refusing .100 command {code} ({code:#04x}): not on "
                      f"the allow-list")


def checksum(head):
    return (255 - (sum(x & 0xFF for x in head) & 0xFF)) & 0xFF


def _pad(b):
    b = bytes(x & 0xFF for x in b)
    if len(b) > LEN:
        raise AmError("packet longer than 64 bytes")
    return b.ljust(LEN, b"\x00")


def cmd(code, params=()):
    """A command packet: the code, up to six parameters, the checksum."""
    _allowed(code)
    head = [code & 0xFF] + [p & 0xFF for p in params][:6]
    head += [0] * (7 - len(head))
    return _pad(head + [checksum(head)])


def raw(*b):
    """A packet with no checksum (the dongle's own relay commands)."""
    _allowed(b[0] if b else -1)
    return _pad(b)


def patch_block(block, write_cmd, changes):
    """A block as just read, turned into its write packet with some bytes
    changed. Refuses anything but a full 64-byte block, so unknown fields
    are never overwritten with zeros."""
    _allowed(write_cmd)
    if len(block) != LEN:
        raise AmError(f"only {len(block)} of {LEN} bytes of the block were "
                      f"read -- nothing written")
    out = bytearray(block)
    out[0] = write_cmd
    for offset, value in changes.items():
        if not 8 <= offset < LEN:
            raise AmError(f"byte {offset} is not a setting")
        out[offset] = value & 0xFF
    out[7] = checksum(out[:7])
    return bytes(out)


# -------------------------------------------------------- parameter block

RATE_CODE = {125: 8, 250: 4, 500: 2, 1000: 1, 2000: 132, 4000: 130, 8000: 129}
RATE_HZ = {v: k for k, v in RATE_CODE.items()}

# section key -> (kind, offset)
PARAMS = {
    "polling_rate": ("rate", 9),
    "debounce_wired": ("byte", 10),
    "debounce_24g": ("byte", 61),
    "debounce_bt": ("byte", 62),
    "lod": ("byte", 52),
    "motion_sync": ("flag", 63),
    "angle_snap": ("flag", 53),
    "ripple": ("flag", 12),
    "sleep_bt": ("u16", 40),
    "deep_sleep_bt": ("u16", 42),
    "sleep_24g": ("u16", 44),
    "deep_sleep_24g": ("u16", 46),
    "light_on": ("flag", 60),
    "colour": ("rgb", 54),            # while charging
    "colour_charged": ("rgb", 57),    # once charged
}


def read_params(block):
    out = {}
    for key, (kind, at) in PARAMS.items():
        if kind == "rate":
            out[key] = RATE_HZ.get(block[at])
        elif kind == "byte":
            out[key] = block[at]
        elif kind == "flag":
            out[key] = bool(block[at] & 1)
        elif kind == "u16":
            out[key] = block[at] | (block[at + 1] << 8)
        else:
            out[key] = rgb_to_hex(*block[at:at + 3])
    return out


def param_changes(changes):
    """{offset: byte} for patch_block. Flags are written as a whole byte,
    0 or 1, as the configurator writes them."""
    out = {}
    for key, value in changes.items():
        if key not in PARAMS:
            raise AmError(f"unknown setting '{key}'")
        kind, at = PARAMS[key]
        if kind == "rate":
            if value not in RATE_CODE:
                raise AmError(f"polling rate must be one of {tuple(RATE_CODE)}")
            out[at] = RATE_CODE[value]
        elif kind == "byte":
            out[at] = value & 0xFF
        elif kind == "flag":
            out[at] = 1 if value else 0
        elif kind == "u16":
            out[at], out[at + 1] = value & 0xFF, (value >> 8) & 0xFF
        else:
            out[at], out[at + 1], out[at + 2] = hex_to_rgb(value)
    return out


# -------------------------------------------------------------- DPI block
#
# [2] active stage, [3] stage count, x u16 LE at 8 + 2i, y at 24 + 2i,
# rgb at 40 + 3i, for stages i = 0..7.

DPI_STAGES = 8


def parse_dpi(block):
    stages = []
    for i in range(DPI_STAGES):
        stages.append({
            "x": block[8 + 2 * i] | (block[9 + 2 * i] << 8),
            "y": block[24 + 2 * i] | (block[25 + 2 * i] << 8),
            "colour": rgb_to_hex(*block[40 + 3 * i:43 + 3 * i]),
        })
    return {"active": block[2], "count": block[3], "stages": stages}


def dpi_changes(changes, current):
    """{offset: byte} for patch_block. Setting x alone moves y with it;
    y can be set on its own."""
    out = {}
    for i, st in (changes.get("stages") or {}).items():
        i = int(i)
        x, y = st.get("x"), st.get("y", st.get("x"))
        if x is not None:
            out[8 + 2 * i], out[9 + 2 * i] = x & 0xFF, (x >> 8) & 0xFF
        if y is not None:
            out[24 + 2 * i], out[25 + 2 * i] = y & 0xFF, (y >> 8) & 0xFF
        if st.get("colour") is not None:
            r, g, b = hex_to_rgb(st["colour"])
            out[40 + 3 * i], out[41 + 3 * i], out[42 + 3 * i] = r, g, b
    if "count" in changes:
        out[3] = changes["count"]
    if "active" in changes:
        out[2] = changes["active"]
    return out


def dpi_patch(block, changes):
    """The DPI write packet. Unlike other settings the active stage and the
    stage count sit in the packet's parameter bytes (2 and 3)."""
    _allowed(SET_DPI)
    if len(block) != LEN:
        raise AmError(f"only {len(block)} of {LEN} bytes of the DPI block "
                      f"were read -- nothing written")
    out = bytearray(block)
    out[0] = SET_DPI
    for offset, value in changes.items():
        if offset not in (2, 3) and not 8 <= offset < LEN:
            raise AmError(f"byte {offset} is not a DPI setting")
        out[offset] = value & 0xFF
    out[7] = checksum(out[:7])
    return bytes(out)


# ---------------------------------------------------------- small packets

def set_profile(profile, profile_max):
    return cmd(SET_PROFILE, [profile, profile_max])


def key_packet(profile, index, mapping4):
    """[80][profile][button][0 0 0 0][checksum] then the four mapping bytes."""
    head = [SET_KEY, profile & 0xFF, index & 0xFF, 0, 0, 0, 0]
    return _pad(head + [checksum(head)] + list(bytes(mapping4)[:4]))


def dongle_light_packet(effect, speed, bright, option, rgb):
    """[8][effect][speed][brightness 0-4][option][r][g][b][checksum of 8].
    Option 0-6 picks one of the configurator's preset swatches; 7 means
    "use the rgb bytes"."""
    head = [SET_DONGLE_LIGHT, effect & 0xFF, speed & 0xFF, bright & 0xFF,
            option & 0xFF, *(c & 0xFF for c in rgb)]
    return _pad(head + [checksum(head)])


# ----------------------------------------------------------------- buttons
#
# Each button is four bytes in the key map (read with 208 [profile][0]):
#   mouse function   [1, 0, code, param]   240-244; the wheel is 245 + 1/255
#   keyboard key     [0, usage, 0, 0], [0, 0, mod, usage], [0, mod, mod, usage]
#   multimedia key   [3, 0, usage low byte, 0]
#   DPI              [20, 0, 2 down / 1 up / 0 cycle, 0]
#   disabled         [0, 0, 0, 0]
#   macro [9, ...] and rapid fire [11, ...] are shown, never written

BUTTON_INDEX = {"left": 0, "right": 1, "middle": 2, "forward": 3, "back": 4,
                "wheel-up": 14, "wheel-down": 15}
KEY_OFFSET = {0: 0, 1: 4, 2: 8, 3: 12, 4: 16, 14: 56, 15: 60}

_MOUSE_BYTES = {"left": (240, 0), "right": (241, 0), "middle": (242, 0),
                "back": (243, 0), "forward": (244, 0),
                "wheel-up": (245, 1), "wheel-down": (245, 255)}
_MOUSE_NAME = {v: k for k, v in _MOUSE_BYTES.items()}
_WHEEL_CODES = (245, 249)
_MOD_USAGE = {m: 0xE0 + i for i, m in enumerate(MODS)}
_MOD_NAME = {v: k for k, v in _MOD_USAGE.items()}
_DPI_BYTE = {"up": 1, "down": 2, "cycle": 0}
_DPI_NAME = {v: k for k, v in _DPI_BYTE.items()}
_MEDIA_BY_LOW = {usage & 0xFF: usage for usage in MEDIA_NAMES}
T_KEY, T_MOUSE, T_MEDIA, T_DPI = 0, 1, 3, 20


def encode_action(action):
    """The four key-map bytes for an Action."""
    k = action.kind
    if k == "disabled":
        return bytes(4)
    if k == "mouse":
        code, param = _MOUSE_BYTES[action.value]
        return bytes([T_MOUSE, 0, code, param])
    if k == "key":
        mods = [_MOD_USAGE[m] for m in action.mods]
        if len(mods) > 2:
            raise AmError("the .100 takes at most two modifiers with a key")
        if not mods:
            return bytes([T_KEY, action.value, 0, 0])
        if len(mods) == 1:
            return bytes([T_KEY, 0, mods[0], action.value])
        return bytes([T_KEY, mods[0], mods[1], action.value])
    if k == "media":
        if action.value not in MEDIA_NAMES:
            raise AmError(f"the .100 only takes the configurator's own "
                          f"media keys: {', '.join(MEDIA_NAMES.values())}")
        return bytes([T_MEDIA, 0, action.value & 0xFF, 0])
    if k == "dpi":
        return bytes([T_DPI, 0, _DPI_BYTE[action.value], 0])
    if k == "profile-switch":
        raise Unsupported("the .100 has no profile-switch action")
    raise AmError("a macro or other opaque binding cannot be written")


def decode_action(b4):
    """The Action four key-map bytes stand for; opaque for anything this
    tool does not write itself."""
    b = bytes(b4[:4]).ljust(4, b"\x00")
    if b == bytes(4):
        return Action("disabled")
    if b[0] == T_MOUSE and b[1] == 0:
        code = 245 if b[2] in _WHEEL_CODES else b[2]
        name = _MOUSE_NAME.get((code, b[3]))
        if name:
            return Action("mouse", name)
    if b[0] == T_KEY:
        if 0x04 <= b[1] <= 0xDF and b[2] == 0 and b[3] == 0:
            return Action("key", b[1])
        mods = [m for m in (b[1], b[2]) if m]
        if mods and all(m in _MOD_NAME for m in mods) \
                and len(set(mods)) == len(mods) and 0x04 <= b[3] <= 0xE7:
            names = {_MOD_NAME[m] for m in mods}
            return Action("key", b[3], tuple(m for m in MODS if m in names))
        if 0xE0 <= b[1] <= 0xE7 and b[2] == 0 and b[3] == 0:
            return Action("key", b[1])
    if b[0] == T_MEDIA and b[1] == 0 and b[3] == 0 and b[2] in _MEDIA_BY_LOW:
        return Action("media", _MEDIA_BY_LOW[b[2]])
    if b[0] == T_DPI and b[1] == 0 and b[3] == 0 and b[2] in _DPI_NAME:
        return Action("dpi", _DPI_NAME[b[2]])
    return Action("opaque", b)
