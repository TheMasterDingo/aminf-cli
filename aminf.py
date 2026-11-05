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
from __future__ import annotations

import argparse
import json
import sys
import time

__version__ = "2.2.0"

try:
    import hid
except ImportError:                                          # pragma: no cover
    sys.exit("hidapi is not installed:  pip install hidapi")


# =================================================================== .97
#
# Protocol: HID feature report 0x14 on vendor collection 0xFF13.

# --------------------------------------------------------------- device ids

VID = 0x0E8D
PID_DONGLE = 0x0703          # 2.4G receiver
PID_MOUSE = 0x0880           # mouse, connected by cable
ALT_IDS = ((0x35A1, 0x0035),)
USAGE_PAGE = 0xFF13          # vendor collection carrying the config channel

REPORT_ID = 0x14
PAYLOAD_LEN = 61

TYPE_CMD, TYPE_REPLY, TYPE_WRITE_ACK = 0x5A, 0x5B, 0x5D
DEV_LOCAL, DEV_RELAY = 0x00, 0x80


# --------------------------------------------------------------- command ids
#
# Verified against live captures: getBattery, getDongleBattery, getProfile,
# getReportRate, setReportRate, getDongleLight, setDongleLight, readNvData,
# saveRgb, setRgbRealtime, setLightSettings.
# Transcribed from the bundle but not exercised here: everything else.

CMD = {
    "getProfile": 12489, "changeProfile": 12488,
    "getBattery": 12495, "getDongleBattery": 12303,
    "getReportRate": 12305, "setReportRate": 12304,
    "getDpi": 12485, "setDpi": 12484,
    "setDpiLoopRange": 12486, "setCurrentDpiStage": 12487,
    "getDpiButton": 12523, "setDpiButton": 12522,
    "getLod": 12493, "setLod": 12492,
    "getMotionSync": 12499, "setMotionSync": 12498,
    "getAngleSnapping": 12501, "setAngleSnapping": 12500,
    "getRippleControl": 12503, "setRippleControl": 12502,
    "getFpsMode": 12511, "setFpsMode": 12510,
    "getSwDebounce": 12521, "setSwDebounce": 12520,
    "getSleepTime": 12307, "setSleepTime": 12306,
    "getRotateAngle": 12505, "setRotateAngle": 12504,
    "getKeyRemap": 12483, "setKeyRemap": 12482,
    "getDongleLight": 12302, "setDongleLight": 12301,
    "setLightSettings": 12467, "setRgbRealtime": 12480, "saveRgb": 2573,
    "readNvData": 2572,
}

# Refused outright. These sit in the same id range as everything else and are
# indistinguishable from a read at the byte level -- sweeping ids blindly is
# how you wipe your configuration and your 2.4G pairing.
BLOCKED = {
    12494: "resetSettings (wipes all configuration)",
    12395: "clearBonded (destroys the 2.4G pairing)",
    12394: "setPairingUniaa (pairing handshake)",
    4353: "rebootPairingDevice",
}

NV_LIGHT_SETTINGS = 24586    # 0x600A: [enabled, effect, speed]
NV_LIGHT_COLORS = 25088      # 0x6200: rgb pattern
NV_READ_LEN = 1000

RGB_FRAME_INTERVAL = 16
RGB_LED_COUNT = 1
RGB_REALTIME_FLAG = 0x80

HUE_MAX = 1530
VALID_RATES = (125, 250, 500, 1000, 2000, 4000, 8000)

# Button remapping: setKeyRemap [key id][type][params...], getKeyRemap
# [key id] -> [status][key id][type][params...]. Key ids: 1 left, 2 right,
# 3 middle, 4 forward, 5 back. A button's own function is the type equal to
# its key id. Type 6 is a keyboard key: [consumer lo][consumer hi][hid usage]
# [modifiers: 1 ctrl, 2 shift, 4 alt, 8 win].
REMAP_KEY_ID = {"forward": 4, "back": 5}
REMAP_KEYBOARD = 6

DONGLE_EFFECTS = {
    "solid": 1, "breathing": 2, "arc": 3, "mosaic": 4,
    "aurora": 5, "night": 6, "flow": 7,
}
MOUSE_EFFECTS = {0: "solid", 1: "breathing", 2: "neon"}

# Used only if the device does not answer a read.
DONGLE_LIGHT_FALLBACK = {
    "on": True, "effect": 1, "hue1": 0, "sat1": 100,
    "hue2": 0, "sat2": 100, "hue3": 0, "sat3": 100,
    "speed": 10, "brightness": 200,
}
MOUSE_LIGHT_FALLBACK = {"on": True, "effect": 0, "speed": 1}


# --------------------------------------------------------------- colour maths

def hex_to_rgb(text):
    t = text.strip().lstrip("#")
    if len(t) == 3:
        t = "".join(c * 2 for c in t)
    if len(t) != 6 or any(c not in "0123456789abcdefABCDEF" for c in t):
        raise ValueError(f"not a hex colour: {text}")
    return int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)


def hex_to_hue_sat(text):
    """The dongle stores hue (0-1530) and saturation (1-100), not RGB."""
    r, g, b = (v / 255 for v in hex_to_rgb(text))
    hi, lo = max(r, g, b), min(r, g, b)
    c = hi - lo
    if c == 0:
        h = 0.0
    elif hi == r:
        h = ((g - b) / c) % 6
    elif hi == g:
        h = (b - r) / c + 2
    else:
        h = (r - g) / c + 4
    deg = (h * 60) % 360
    sat = 0 if hi == 0 else round(c / hi * 100)
    return round(deg / 360 * HUE_MAX), max(1, min(100, sat))


def hue_to_hex(raw):
    t = max(0, min(HUE_MAX, int(raw)))
    if t <= 255:
        r, g, b = 255, t, 0
    elif t <= 510:
        r, g, b = 510 - t, 255, 0
    elif t <= 765:
        r, g, b = 0, 255, t - 510
    elif t <= 1020:
        r, g, b = 0, 1020 - t, 255
    elif t <= 1275:
        r, g, b = t - 1020, 0, 255
    else:
        r, g, b = 255, 0, HUE_MAX - t
    return f"#{r:02X}{g:02X}{b:02X}"


def u16le(v):
    return [v & 0xFF, (v >> 8) & 0xFF]


# --------------------------------------------------------------- remap keys
#
# Keys are HID keyboard usages. The web configurator's key picker stops at
# F12, but the .100's own code accepts any usage from 0x04 to 0xE7 and the
# .97's passes the byte through unchecked, so F13-F24 can be set from here.

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


# --------------------------------------------------------------- exceptions

class AmError(RuntimeError):
    pass


class DeviceNotFound(AmError):
    pass


class DeviceBusy(AmError):
    pass


# --------------------------------------------------------------- the device

class Am97:
    """Vendor HID client for the AM Infinity .97."""

    model = ".97"

    def __init__(self, path, has_dongle, dry_run=False):
        self.has_dongle = has_dongle
        self.dry_run = dry_run
        # Mouse-side settings travel over the relay only via the dongle.
        self.mouse_id = DEV_RELAY if has_dongle else DEV_LOCAL
        self.dev = None
        if dry_run:
            return
        try:
            self.dev = hid.device()
            self.dev.open_path(path)
        except Exception as exc:
            self.dev = None
            raise DeviceBusy(
                f"could not open the device ({exc}). If the web configurator "
                f"is open in a browser tab, close it and try again."
            ) from exc

    # -- discovery ---------------------------------------------------------

    @staticmethod
    def discover(prefer="auto"):
        """Return (path, has_dongle) or None.

        prefer: 'auto' (dongle first), 'dongle', or 'usb'. The dongle is
        preferred because it owns the dongle lighting and can still reach the
        mouse by relay, so it can service every command on its own.
        """
        dongle = usb = None
        try:
            entries = hid.enumerate()
        except Exception:
            return None
        for d in entries:
            if d.get("usage_page") != USAGE_PAGE:
                continue
            ids = (d.get("vendor_id"), d.get("product_id"))
            if ids == (VID, PID_DONGLE):
                dongle = (d["path"], True)
            elif ids == (VID, PID_MOUSE) or ids in ALT_IDS:
                usb = (d["path"], False)
        if prefer == "dongle":
            return dongle
        if prefer == "usb":
            return usb
        return dongle or usb

    @classmethod
    def open(cls, prefer="auto", dry_run=False):
        found = cls.discover(prefer)
        if not found:
            if dry_run:
                return cls(None, True, dry_run=True)
            raise DeviceNotFound(
                "AM Infinity .97 not found. Plug in the 2.4G dongle or "
                "connect the mouse by cable.")
        return cls(*found, dry_run=dry_run)

    def close(self):
        if self.dev:
            try:
                self.dev.close()
            except Exception:
                pass

    # -- wire format -------------------------------------------------------
    #
    #  [payload_len][device_id] [05][type][len_lo][len_hi][cmd_lo][cmd_hi][data]
    #
    # device_id 0x00 addresses whatever you are connected to; 0x80 addresses
    # the mouse through the dongle's relay. type 0x5a is a command, 0x5b a
    # reply, 0x5d a write acknowledgement. len counts the command id plus the
    # data. The payload is zero-padded to 61 bytes and carried in HID feature
    # report 0x14.

    @staticmethod
    def _frame(device_id, cmd_id, data=()):
        data = bytes(data)
        body = (bytes([0x05, TYPE_CMD]) + bytes(u16le(len(data) + 2))
                + bytes(u16le(cmd_id)) + data)
        return (bytes([len(body), device_id]) + body).ljust(PAYLOAD_LEN, b"\x00")

    def send(self, cmd_id, data=(), device_id=None, expect_reply=True,
             tries=40):
        if cmd_id in BLOCKED:
            raise AmError(f"refusing {cmd_id:#06x}: {BLOCKED[cmd_id]}")
        device_id = self.mouse_id if device_id is None else device_id
        frame = self._frame(device_id, cmd_id, data)
        if self.dry_run:
            print(f"  would send dev={device_id:#04x} cmd={cmd_id:#06x} "
                  f"{frame[:20].hex(' ')} ...")
            return None
        self.dev.send_feature_report(bytes([REPORT_ID]) + frame)
        if not expect_reply:
            return None
        # A read may carry several stacked responses, and a reply left over
        # from an earlier write-only command can arrive first, so walk the
        # frames and keep polling until the command id matches.
        for _ in range(tries):
            r = bytes(self.dev.get_feature_report(REPORT_ID, PAYLOAD_LEN + 1))
            if len(r) < 9 or r[1] == 0:
                time.sleep(0.03)
                continue
            i = 3
            while i + 6 <= len(r):
                if r[i] != 0x05 or r[i + 1] not in (TYPE_REPLY, TYPE_WRITE_ACK):
                    break
                n = r[i + 2] | (r[i + 3] << 8)
                rid = r[i + 4] | (r[i + 5] << 8)
                payload = r[i + 6:i + 4 + n]
                if rid == cmd_id:
                    return payload
                i += 4 + n
            time.sleep(0.03)
        return None

    def read_nv(self, nv_id):
        """readNvData -> [status][nv_lo][nv_hi][len_lo][len_hi][data...]"""
        p = self.send(CMD["readNvData"], u16le(nv_id) + u16le(NV_READ_LEN))
        if not p or len(p) < 6 or p[0] != 0:
            return None
        if (p[1] | (p[2] << 8)) != nv_id:
            return None
        n = p[3] | (p[4] << 8)
        body = p[5:]
        return body[:n] if n > 0 else body

    # -- reads -------------------------------------------------------------

    def battery(self):
        p = self.send(CMD["getBattery"])
        if not p or len(p) < 5 or p[0] != 0:
            return None
        return {"percent": p[2], "charging": bool(p[1]), "present": bool(p[4])}

    def hub_battery(self):
        """Spare battery in the dongle/hub, when one is seated."""
        if not self.has_dongle:
            return None
        p = self.send(CMD["getDongleBattery"], device_id=DEV_LOCAL)
        if not p or len(p) < 5 or p[0] != 0 or not p[4]:
            return None
        return {"percent": p[2], "charging": bool(p[1]), "present": True}

    def profile(self):
        p = self.send(CMD["getProfile"])
        return None if not p or len(p) < 2 else p[1]

    def polling_rate(self):
        p = self.send(CMD["getReportRate"])
        return None if not p or len(p) < 3 or p[0] != 0 else p[1] | (p[2] << 8)

    def _flag(self, key):
        p = self.send(CMD[key])
        return None if not p or len(p) < 2 else bool(p[1])

    def _byte(self, key):
        p = self.send(CMD[key])
        return None if not p or len(p) < 2 else p[1]

    def dongle_light(self):
        if not self.has_dongle or self.dry_run:
            return None
        p = self.send(CMD["getDongleLight"], device_id=DEV_LOCAL)
        if not p or len(p) < 13:
            return None
        return {
            "on": bool(p[0]), "effect": p[1],
            "hue1": (p[2] << 8) | p[3], "sat1": p[4],
            "hue2": (p[5] << 8) | p[6], "sat2": p[7],
            "hue3": (p[8] << 8) | p[9], "sat3": p[10],
            "speed": max(1, p[11] - 80),    # firmware stores speed + 80
            "brightness": p[12],
        }

    def mouse_light(self):
        if self.dry_run:
            return None
        d = self.read_nv(NV_LIGHT_SETTINGS)
        if not d or len(d) < 3:
            return None
        return {"on": bool(d[0]), "effect": d[1], "speed": d[2]}

    def mouse_colour(self):
        if self.dry_run:
            return None
        d = self.read_nv(NV_LIGHT_COLORS)
        if not d or len(d) < 7:
            return None
        return f"#{d[4]:02X}{d[5]:02X}{d[6]:02X}"

    # -- writes ------------------------------------------------------------
    #
    # Lighting has no "set just this field" command: the firmware takes the
    # whole block or nothing. So every change is read-modify-write, which is
    # exactly what the configurator does behind its switches and sliders.

    def set_dongle_light(self, **changes):
        if not self.has_dongle:
            raise AmError("dongle lighting needs the 2.4G dongle connected")
        cfg = self.dongle_light() or dict(DONGLE_LIGHT_FALLBACK)
        cfg.update(changes)
        h1, h2, h3 = cfg["hue1"], cfg["hue2"], cfg["hue3"]
        self.send(CMD["setDongleLight"], [
            1 if cfg["on"] else 0, cfg["effect"],
            (h1 >> 8) & 0xFF, h1 & 0xFF, max(1, min(100, cfg["sat1"])),
            (h2 >> 8) & 0xFF, h2 & 0xFF, max(1, min(100, cfg["sat2"])),
            (h3 >> 8) & 0xFF, h3 & 0xFF, max(1, min(100, cfg["sat3"])),
            max(1, min(20, cfg["speed"])) + 80,
            max(1, min(255, cfg["brightness"])),
        ], device_id=DEV_LOCAL, expect_reply=False)
        return cfg

    def set_mouse_light(self, **changes):
        cfg = self.mouse_light() or dict(MOUSE_LIGHT_FALLBACK)
        cfg.update(changes)
        self.send(CMD["setLightSettings"],
                  [1 if cfg["on"] else 0, cfg["effect"],
                   max(1, min(10, cfg["speed"]))],
                  expect_reply=False)
        return cfg

    def set_mouse_colour(self, colour):
        """Three commands, the same sequence the configurator uses."""
        r, g, b = hex_to_rgb(colour)
        self.send(CMD["saveRgb"],
                  u16le(NV_LIGHT_COLORS) + [RGB_LED_COUNT] + u16le(3)
                  + [RGB_FRAME_INTERVAL, r, g, b], expect_reply=False)
        time.sleep(0.1)
        self.send(CMD["setRgbRealtime"],
                  [RGB_REALTIME_FLAG, 0, RGB_FRAME_INTERVAL, 1, r, g, b],
                  expect_reply=False)
        cur = self.mouse_light() or dict(MOUSE_LIGHT_FALLBACK)
        self.send(CMD["setLightSettings"],
                  [1 if cur["on"] else 0, cur["effect"],
                   max(1, min(10, cur["speed"]))],
                  expect_reply=False)

    def set_polling_rate(self, hz):
        if hz not in VALID_RATES:
            raise AmError(f"polling rate must be one of {VALID_RATES}")
        self.send(CMD["setReportRate"], u16le(hz))

    def set_profile(self, index):
        self.send(CMD["changeProfile"], [index])

    def set_flag(self, key, value):
        self.send(CMD[key], [1 if value else 0])

    def set_lod(self, value):
        self.send(CMD["setLod"], [max(0, min(2, value))])

    # -- button remapping --------------------------------------------------

    def key_remap(self, button):
        """(type, params) for a side button, or None."""
        key_id = REMAP_KEY_ID[button]
        p = self.send(CMD["getKeyRemap"], [key_id])
        if not p or len(p) < 3 or p[0] != 0 or p[1] != key_id:
            return None
        return p[2], list(p[3:])

    def remap_label(self, button):
        if self.dry_run:
            return "unknown (dry run)"
        got = self.key_remap(button)
        if not got:
            return "no reply"
        kind, params = got
        if kind == REMAP_KEY_ID[button]:
            return f"{button} (default)"
        if kind == REMAP_KEYBOARD and len(params) >= 4:
            consumer = params[0] | (params[1] << 8)
            if consumer:
                return f"media key 0x{consumer:04X}"
            mods = [n for i, n in enumerate(MODIFIER_NAMES)
                    if params[3] & (1 << i)]
            return key_name(params[2], mods)
        other = {0: "disabled", 1: "left click", 2: "right click",
                 3: "middle click", 4: "forward", 5: "back"}
        return other.get(kind, f"function type {kind}")

    def set_key_remap(self, button, usage):
        """usage None gives the button its own function back."""
        key_id = REMAP_KEY_ID[button]
        data = ([key_id, key_id] if usage is None
                else [key_id, REMAP_KEYBOARD, 0, 0, usage, 0])
        self.send(CMD["setKeyRemap"], data)
        if self.dry_run:
            return
        want = data[2:]
        for wait in (0.1, 0.3, 0.6):
            time.sleep(wait)
            got = self.key_remap(button)
            if got and got[0] == data[1] and \
                    (got[1] + [0] * len(want))[:len(want)] == want:
                return
        raise AmError(f"{button}: the mouse didn't confirm the new mapping")



# =================================================================== .100
#
# A different protocol from the .97. Every packet is 64 bytes in a feature
# report:  [cmd][6 param bytes][checksum][...]  with
# checksum = 255 - (sum of the first 7 bytes & 255).
#
# When you're on the dongle, commands for the mouse are relayed with a small
# handshake the configurator uses: open channel (F6 05), wait until the
# dongle's status (F7) says ready, set length (FE 40), send, wait, and for
# reads fetch the answer (FC).

M100_VID = 0x3151
M100_PID_MOUSE = 0x402A
M100_PID_DONGLE = 0x5007
M100_USAGE_PAGES = (0xFF01, 0xFFFF)
M100_LEN = 64
# Report ids the configurator falls back through when a device doesn't
# declare one. Only a read-only status packet is used to find the right one.
M100_REPORT_IDS = (0, 4, 5, 6, 7, 20, 21, 22, 23)

M100_GET_PARAMS = 211          # 64-byte parameter block (rate, mouse light ...)
M100_SET_PARAMS = 83           # the same block written back
M100_GET_BATTERY = 214
M100_GET_PROFILE = 133
M100_GET_DONGLE_SWITCH = 135   # dongle light on/off
M100_SET_DONGLE_SWITCH = 7
M100_GET_DONGLE_LIGHT = 136    # dongle effect / speed / bright / option / rgb
M100_SET_DONGLE_LIGHT = 8
M100_DONGLE_STATUS = 247
M100_FWD_OPEN = 246
M100_FWD_LENGTH = 254
M100_FWD_FETCH = 252
M100_GET_KEYMAP = 208          # [208][profile][0] -> 64-byte key map, no echo
M100_SET_KEY = 80              # [80][profile][button][0 0 0 0][sum][4 bytes]

# The only first bytes this script will ever put on the wire for the .100.
# Reset (2), clear-bluetooth (97), the DPI block (212/84) and every
# boot/firmware command are simply not in the list.
M100_ALLOWED = {
    M100_GET_PARAMS, M100_SET_PARAMS,
    M100_GET_BATTERY, M100_GET_PROFILE,
    M100_GET_DONGLE_SWITCH, M100_SET_DONGLE_SWITCH,
    M100_GET_DONGLE_LIGHT, M100_SET_DONGLE_LIGHT,
    M100_DONGLE_STATUS, M100_FWD_OPEN, M100_FWD_LENGTH, M100_FWD_FETCH,
    M100_GET_KEYMAP, M100_SET_KEY,
}

# Button remapping. Each button is 4 bytes in the key map: its own mouse
# function is [1, 0, code, 0]; a keyboard key is [0, usage, 0, 0], or
# [0, mod, mod, usage] with modifiers (0xE0-0xE3 = Ctrl/Shift/Alt/Win).
M100_KEY_INDEX = {"forward": 3, "back": 4}
M100_KEY_OFFSET = {3: 12, 4: 16}
M100_KEY_DEFAULT = {3: [1, 0, 244, 0], 4: [1, 0, 243, 0]}

M100_RATE_CODE = {125: 8, 250: 4, 500: 2, 1000: 1, 2000: 132, 4000: 130,
                  8000: 129}
M100_RATE_HZ = {v: k for k, v in M100_RATE_CODE.items()}

M100_DONGLE_EFFECTS = {0: "off", 1: "solid", 2: "breathing", 3: "neon",
                       4: "wave", 5: "marquee", 6: "ring", 7: "buffer",
                       8: "chase"}
M100_COLOUR_CUSTOM = 7         # option 0-6 are the UI's preset swatches;
                               # anything else means "use the rgb bytes"
M100_EFFECTS_NO_SPEED = (0, 1)  # off / solid: the UI sends speed 0 for these

M100_P_RATE = 9                # byte offsets inside the parameter block
M100_P_MOUSE_LIGHT = 60
M100_P_COLOUR_CHARGING = 54    # 54-56 rgb while charging
M100_P_COLOUR_CHARGED = 57     # 57-59 rgb once charged

# Key map entry types the mouse can report (keyboard, mouse function,
# media, macro, turbo click, DPI step). Used to tell a real key map apart
# from a stale reply, which always starts with a command byte.
M100_KEY_TYPES = {0, 1, 3, 9, 11, 20}


class MouseOffline(AmError):
    pass


def m100_checksum(b):
    return (255 - (sum(x & 0xFF for x in b) & 0xFF)) & 0xFF


def m100_pad(b):
    b = [x & 0xFF for x in b]
    if len(b) > M100_LEN:
        raise ValueError("packet longer than 64 bytes")
    return b + [0] * (M100_LEN - len(b))


def m100_cmd(code, params=()):
    head = [code & 0xFF] + [p & 0xFF for p in params][:6]
    head += [0] * (7 - len(head))
    return m100_pad(head + [m100_checksum(head)])


def m100_raw(*b):
    return m100_pad(list(b))


M100_STATUS_PKT = m100_raw(M100_DONGLE_STATUS)


def m100_hex(pkt, n=None):
    """Hex dump; by default through the last non-zero byte, at least 8."""
    if n is None:
        last = max((i for i, x in enumerate(pkt) if x), default=0)
        n = max(8, last + 1)
    return " ".join(f"{x:02x}" for x in pkt[:n])


def rgb_hex(b):
    return "#%02X%02X%02X" % tuple(b[:3])


class Am100Link:
    """One open .100 interface (the dongle, or the mouse over USB)."""

    def __init__(self, is_dongle, report_id, dev, path=None):
        self.is_dongle = is_dongle
        self.rid = report_id
        self.dev = dev
        self.path = path

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass

    def _reopen(self):
        """Close and reopen the interface -- what the configurator does
        between failed write attempts."""
        if not self.path:
            return
        self.close()
        time.sleep(0.05)
        try:
            dev = hid.device()
            dev.open_path(self.path)
            self.dev = dev
        except Exception:
            pass                      # the next attempt will report it

    def _write(self, pkt, attempts=5):
        if pkt[0] not in M100_ALLOWED:
            raise AmError(f"refusing .100 command {pkt[0]:#04x}")
        last = None
        for attempt in range(1, attempts + 1):
            try:
                n = self.dev.send_feature_report(bytes([self.rid]) + bytes(pkt))
                if n is not None and n < 0:
                    raise OSError("send_feature_report failed")
                return
            except (OSError, ValueError) as exc:
                last = exc
                if attempt < attempts:
                    self._reopen()
                    time.sleep(0.15 * attempt)
        raise AmError(f"HID write failed: {last}")

    def _read(self):
        r = list(self.dev.get_feature_report(self.rid, M100_LEN + 1))
        if len(r) == M100_LEN + 1 and r[0] == self.rid:
            r = r[1:]
        return m100_pad(r[:M100_LEN])

    def send(self, pkt, expect=False, delay=0.03, attempts=5):
        self._write(pkt, attempts)
        if not expect:
            time.sleep(0.005)
            return None
        time.sleep(delay)
        last = None
        for m in range(1, 4):
            try:
                r = self._read()
            except (OSError, ValueError) as exc:
                last = exc
                if m < 3:
                    time.sleep(0.05 * m)
                continue
            if any(r):
                time.sleep(0.005)
                return r
            last = "empty reply"
            if m < 3:
                time.sleep(0.03 * m)
        raise AmError(f"no reply to {pkt[0]:#04x} ({last})")

    # -- relay through the dongle ------------------------------------------

    @staticmethod
    def _route_ready(s):
        return s[5] == 1 or (s[0] == 0 and s[4] == 0 and s[6] == 2)

    def _wait(self, cond, timeout):
        end = time.monotonic() + timeout
        s = None
        while time.monotonic() < end:
            s = self.send(M100_STATUS_PKT, expect=True)
            if s and cond(s):
                return s
            time.sleep(0.1)
        raise AmError(f"dongle not ready ({m100_hex(s) if s else 'no status'})")

    def forward(self, pkt, expect):
        last = None
        for attempt in range(3):
            if attempt:
                time.sleep(0.1)
            try:
                self.send(m100_raw(M100_FWD_OPEN, 5))
                s = self._wait(self._route_ready, 3.0)
                if s[4] == 1:
                    raise MouseOffline("mouse is off or out of range")
                self.send(m100_raw(M100_FWD_LENGTH, M100_LEN))
                self.send(pkt)
                if not expect:
                    self._wait(self._route_ready, 3.0)
                    return None
                self._wait(lambda s: s[0] == 1 or s[0] == pkt[0], 5.0)
                time.sleep(0.01)
                return self.send(m100_raw(M100_FWD_FETCH), expect=True)
            except AmError as exc:
                last = exc
        try:
            self.send(m100_raw(M100_FWD_OPEN, 0))     # close the relay channel
        except AmError:
            pass
        raise last


class Am100:
    """Vendor HID client for the AM Infinity .100.

    Holds up to two links: the dongle (dongle lighting, and the mouse by
    relay) and the mouse over USB (mouse settings directly).
    """

    model = ".100"

    def __init__(self, dongle=None, mouse=None, dry_run=False):
        self.dongle = dongle
        self.mouse = mouse
        self.dry_run = dry_run
        self.offline = False      # set once the dongle reports the mouse away

    @property
    def has_dongle(self):
        return self.dongle is not None or self.dry_run

    # -- discovery ---------------------------------------------------------

    @staticmethod
    def _candidates(prefer):
        try:
            entries = hid.enumerate(M100_VID)
        except Exception:
            return []
        out = []
        for d in entries:
            pid = d.get("product_id")
            if pid not in (M100_PID_DONGLE, M100_PID_MOUSE):
                continue            # boot-mode pids are deliberately ignored
            is_dongle = pid == M100_PID_DONGLE
            if (prefer == "dongle" and not is_dongle) or \
                    (prefer == "usb" and is_dongle):
                continue
            vendor = d.get("usage_page") in M100_USAGE_PAGES
            out.append((0 if vendor else 1, is_dongle, d["path"]))
        out.sort(key=lambda c: c[0])
        return out

    @staticmethod
    def _probe(path, is_dongle):
        """Open an interface and find the report id that answers a harmless
        status read -- the same health check the configurator runs."""
        try:
            dev = hid.device()
            dev.open_path(path)
        except Exception:
            return None
        for rid in M100_REPORT_IDS:
            link = Am100Link(is_dongle, rid, dev, path)
            try:
                if is_dongle:
                    ok = bool(link.send(M100_STATUS_PKT, expect=True,
                                        attempts=1))
                else:
                    r = link.send(m100_cmd(M100_GET_PROFILE), expect=True,
                                  attempts=1)
                    ok = r[0] == M100_GET_PROFILE
            except AmError:
                ok = False
            if ok:
                return link
        try:
            dev.close()
        except Exception:
            pass
        return None

    @classmethod
    def present(cls, prefer="auto"):
        return bool(cls._candidates(prefer))

    @classmethod
    def open(cls, prefer="auto", dry_run=False):
        if dry_run:
            return cls(dry_run=True)
        dongle = mouse = None
        seen = False
        for _pri, is_dongle, path in cls._candidates(prefer):
            seen = True
            if (is_dongle and dongle) or (not is_dongle and mouse):
                continue
            link = cls._probe(path, is_dongle)
            if link is None:
                continue
            if is_dongle:
                dongle = link
            else:
                mouse = link
        if dongle or mouse:
            return cls(dongle, mouse)
        if seen:
            raise DeviceBusy(
                "AM Infinity .100 found but it didn't answer. If the web "
                "configurator or AM Master is open, close it and try again.")
        raise DeviceNotFound("AM Infinity .100 not found.")

    def close(self):
        for link in (self.dongle, self.mouse):
            if link:
                link.close()

    # -- routing: mouse settings go by cable if there is one, else relay ----

    def _to_mouse(self, pkt, expect):
        if self.dry_run:
            print(f"  would send to mouse  {m100_hex(pkt)}")
            return None
        if self.mouse:
            return self.mouse.send(pkt, expect=expect)
        # Once the dongle has said the mouse is away, don't repeat the whole
        # relay handshake for every remaining read in the same run.
        if self.offline:
            raise MouseOffline("mouse is off or out of range")
        try:
            return self.dongle.forward(pkt, expect)
        except MouseOffline:
            self.offline = True
            raise

    def _to_dongle(self, pkt, expect):
        if self.dry_run:
            print(f"  would send to dongle {m100_hex(pkt)}")
            return None
        if not self.dongle:
            raise AmError("dongle lighting needs the 2.4G dongle connected")
        return self.dongle.send(pkt, expect=expect)

    # -- parameter block: read it whole, change bytes, write it whole ------
    #
    # Same as the configurator, which refuses to write the block unless it
    # has just read all 64 bytes of it.

    def _read_params(self):
        if self.dry_run:
            raise AmError("dry run: no device to read from")
        r = self._to_mouse(m100_cmd(M100_GET_PARAMS), expect=True)
        if not r or len(r) != M100_LEN or r[0] != M100_GET_PARAMS:
            raise AmError("could not read the parameter block -- nothing "
                          "written")
        return r

    def _modify_params(self, changes):
        if self.dry_run:
            what = ", ".join(f"byte {o} = {v & 0xFF:#04x}"
                             for o, v in changes.items())
            print(f"  would read the parameter block from the mouse and "
                  f"write it back with {what}")
            return
        b = list(self._read_params())
        b[0] = M100_SET_PARAMS
        for offset, value in changes.items():
            b[offset] = value & 0xFF
        b[7] = m100_checksum(b[:7])
        self._to_mouse(b, expect=False)

    # -- reads -------------------------------------------------------------

    def battery(self):
        if self.dry_run:
            return None
        try:
            b = self._to_mouse(m100_cmd(M100_GET_BATTERY), expect=True)
        except AmError:
            return None
        if not b or b[0] != M100_GET_BATTERY or not b[2] or b[3] > 100:
            return None
        return {"percent": b[3], "charging": b[1] == 1, "present": True}

    def hub_battery(self):
        """Spare battery in the dongle, from the dongle's status packet."""
        if not self.dongle or self.dry_run:
            return None
        try:
            s = self.dongle.send(M100_STATUS_PKT, expect=True)
        except AmError:
            return None
        if not s or not 0 < s[10] <= 100:
            return None
        return {"percent": s[10], "charging": None, "present": True}

    def _params_or_none(self):
        try:
            return self._read_params()
        except AmError:
            return None

    def profile(self):
        if self.dry_run:
            return None
        try:
            return self._profile()
        except AmError:
            return None

    def polling_rate(self, params=None):
        p = params or self._params_or_none()
        return None if not p else M100_RATE_HZ.get(p[M100_P_RATE])

    def mouse_light(self, params=None):
        p = params or self._params_or_none()
        if not p:
            return None
        c1 = rgb_hex(p[M100_P_COLOUR_CHARGING:])
        c2 = rgb_hex(p[M100_P_COLOUR_CHARGED:])
        out = {"on": bool(p[M100_P_MOUSE_LIGHT]), "colour": c1}
        if c2 != c1:
            out["colour_charged"] = c2
        return out

    def dongle_light(self):
        if not self.dongle or self.dry_run:
            return None
        s = self._to_dongle(m100_cmd(M100_GET_DONGLE_SWITCH), expect=True)
        if not s or s[0] != M100_GET_DONGLE_SWITCH:
            return None
        out = {"on": s[1] == 1}
        c = self._to_dongle(m100_cmd(M100_GET_DONGLE_LIGHT), expect=True)
        if c and c[0] == M100_GET_DONGLE_LIGHT:
            out.update({
                "effect": c[1],
                "effect_name": M100_DONGLE_EFFECTS.get(c[1], str(c[1])),
                "brightness": c[3],           # 0-4
                "colour": rgb_hex(c[5:]),
                "preset": c[4] < M100_COLOUR_CUSTOM,
            })
        return out

    # -- writes ------------------------------------------------------------

    def set_dongle_light(self, on):
        self._to_dongle(m100_cmd(M100_SET_DONGLE_SWITCH, [1 if on else 0]),
                        expect=False)

    def set_dongle_colour(self, colour):
        """Keep effect, speed and brightness; switch to a custom colour. The
        web UI only offers seven presets, but the firmware takes any rgb."""
        rgb = hex_to_rgb(colour)
        if self.dry_run:
            effect, speed, bright = 1, 0, 4
        else:
            r = self._to_dongle(m100_cmd(M100_GET_DONGLE_LIGHT), expect=True)
            if not r or r[0] != M100_GET_DONGLE_LIGHT:
                raise AmError("could not read the dongle light -- nothing "
                              "written")
            effect, speed, bright = r[1], r[2], min(r[3], 4)
        if effect in M100_EFFECTS_NO_SPEED:
            speed = 0
        # [8][effect][speed][bright][option][r][g][b][checksum of those 8]
        head = [M100_SET_DONGLE_LIGHT, effect, speed, bright,
                M100_COLOUR_CUSTOM, *rgb]
        self._to_dongle(m100_pad(head + [m100_checksum(head)]), expect=False)

    def set_mouse_light(self, on):
        self._modify_params({M100_P_MOUSE_LIGHT: 1 if on else 0})

    def set_polling_rate(self, hz):
        if hz not in M100_RATE_CODE:
            raise AmError(f"polling rate must be one of "
                          f"{tuple(M100_RATE_CODE)}")
        self._modify_params({M100_P_RATE: M100_RATE_CODE[hz]})

    # -- button remapping (per profile, like the configurator) -------------

    def _profile(self):
        r = self._to_mouse(m100_cmd(M100_GET_PROFILE), expect=True)
        if not r or r[0] != M100_GET_PROFILE or r[1] > 7:
            raise AmError("could not read the active profile -- nothing "
                          "written")
        return r[1]

    def _keymap(self, profile):
        # The key map comes back without an echo of the command byte (the
        # configurator skips its echo check here too), so the one way to
        # catch a reply left over from an earlier command is that byte 0 of
        # a real key map is a key type, never a command byte.
        for _ in range(2):
            r = self._to_mouse(m100_cmd(M100_GET_KEYMAP, [profile, 0]),
                               expect=True)
            if r and len(r) == M100_LEN and r[0] in M100_KEY_TYPES:
                return r
            time.sleep(0.05)
        raise AmError("could not read the key map")

    def key_remap(self, button):
        o = M100_KEY_OFFSET[M100_KEY_INDEX[button]]
        return list(self._keymap(self._profile())[o:o + 4])

    def remap_label(self, button):
        if self.dry_run:
            return "unknown (dry run)"
        m = self.key_remap(button)
        for name, idx in M100_KEY_INDEX.items():
            if m == M100_KEY_DEFAULT[idx]:
                return f"{name} (default)" if name == button else name
        if m == [0, 0, 0, 0]:
            return "disabled"
        if m[0] == 0 and 4 <= m[1] <= 0xE7 and m[2] == 0 and m[3] == 0:
            return key_name(m[1])
        if m[0] == 0 and 4 <= m[3] <= 0xE7:
            mods = [MODIFIER_NAMES[x - 0xE0] for x in m[1:3]
                    if 0xE0 <= x <= 0xE3]
            return key_name(m[3], mods)
        return "bytes " + " ".join(f"{x:02x}" for x in m)

    def set_key_remap(self, button, usage):
        """usage None gives the button its own function back."""
        idx = M100_KEY_INDEX[button]
        mapping = (list(M100_KEY_DEFAULT[idx]) if usage is None
                   else [0, usage, 0, 0])
        profile = 0 if self.dry_run else self._profile()
        head = [M100_SET_KEY, profile, idx, 0, 0, 0, 0]
        pkt = m100_pad(head + [m100_checksum(head)] + mapping)
        o = M100_KEY_OFFSET[idx]
        # Write and read back. If the mouse hasn't taken it, write once more
        # and wait longer -- the configurator's own retry schedule.
        for waits in ((0.25, 0.4, 0.6), (0.5, 0.7)):
            self._to_mouse(pkt, expect=False)
            if self.dry_run:
                return
            for wait in waits:
                time.sleep(wait)
                if list(self._keymap(profile)[o:o + 4]) == mapping:
                    return
        raise AmError(f"{button}: the mouse didn't confirm the new mapping")


# --------------------------------------------------------------------- CLI

FLAGS = {
    "motionsync": "setMotionSync",
    "anglesnap": "setAngleSnapping",
    "ripple": "setRippleControl",
    "fpsmode": "setFpsMode",
}

# Commands only the .97 implements here. On a .100 they're skipped with a
# notice instead of guessing at an untested encoding.
ONLY_97 = {"toggle", "lod", "profile", "raw"}
ONLY_97_LIGHT = {"brightness", "speed", "effect"}


class Unsupported(AmError):
    pass


def collect_status(m):
    if m.model == ".100":
        params = None if m.dry_run else m._params_or_none()
        return {
            "model": m.model,
            "connection": "dongle" if m.dongle else "usb",
            "battery": m.battery(),
            "hub_battery": m.hub_battery(),
            "profile": m.profile(),
            "polling_rate": m.polling_rate(params) if params else None,
            "mouse_light": m.mouse_light(params) if params else None,
            "dongle_light": m.dongle_light(),
            "mouse_offline": m.offline,
        }
    return {
        "model": m.model,
        "connection": "dongle" if m.has_dongle else "usb",
        "battery": m.battery(),
        "hub_battery": m.hub_battery(),
        "profile": m.profile(),
        "polling_rate": m.polling_rate(),
        "lod": m._byte("getLod"),
        "motion_sync": m._flag("getMotionSync"),
        "angle_snapping": m._flag("getAngleSnapping"),
        "ripple_control": m._flag("getRippleControl"),
        "mouse_light": m.mouse_light(),
        "mouse_colour": m.mouse_colour(),
        "dongle_light": m.dongle_light(),
    }


def print_status(s):
    print(f"model          {s['model']}")
    print(f"connection     {s['connection']}")
    for key, label in (("battery", "battery      "),
                       ("hub_battery", "hub battery  ")):
        b = s.get(key)
        if b:
            print(f"{label}  {b['percent']:3d}%"
                  f"{'  charging' if b.get('charging') else ''}")
    if s.get("profile") is not None:
        print(f"profile        {s['profile']}")
    rate = s["polling_rate"]
    print(f"polling rate   {f'{rate} Hz' if rate else '--'}")

    if s["model"] == ".100":
        if s.get("mouse_offline"):
            print("mouse          off or out of range (dongle values only)")
        ml = s["mouse_light"]
        if ml:
            extra = (f" / charged {ml['colour_charged']}"
                     if "colour_charged" in ml else "")
            print(f"mouse light    {'on' if ml['on'] else 'off'}  "
                  f"{ml['colour']}{extra}")
        dl = s["dongle_light"]
        if dl:
            line = f"dongle light   {'on' if dl['on'] else 'off'}"
            if "colour" in dl:
                line += (f"  {dl['effect_name']}  {dl['colour']}"
                         f"{' (preset)' if dl['preset'] else ''}"
                         f"  bright={dl['brightness']}/4")
            print(line)
        return

    print(f"lift-off dist  {s['lod']}")
    print(f"sensor         motion-sync={s['motion_sync']}  "
          f"angle-snap={s['angle_snapping']}  ripple={s['ripple_control']}")
    ml = s["mouse_light"]
    if ml:
        print(f"mouse light    {'on' if ml['on'] else 'off'}  "
              f"{MOUSE_EFFECTS.get(ml['effect'], ml['effect'])}  "
              f"{s['mouse_colour'] or '--'}")
    dl = s["dongle_light"]
    if dl:
        name = next((k for k, v in DONGLE_EFFECTS.items()
                     if v == dl["effect"]), dl["effect"])
        print(f"dongle light   {'on' if dl['on'] else 'off'}  {name}  "
              f"{hue_to_hex(dl['hue1'])} sat={dl['sat1']} "
              f"bright={dl['brightness']} speed={dl['speed']}")


def build_parser():
    ap = argparse.ArgumentParser(
        prog="aminf",
        description="Control Angry Miao AM Infinity mice (.97 and .100) "
                    "from the command line.")
    ap.add_argument("--version", action="version",
                    version=f"%(prog)s {__version__}")
    ap.add_argument("--mouse", choices=["auto", "97", "100"], default="auto",
                    help="which mouse to talk to (default: every one found)")
    ap.add_argument("--device", choices=["auto", "dongle", "usb"],
                    default="auto", help="which endpoint to talk to")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output where applicable")
    ap.add_argument("--quiet", "-q", action="store_true",
                    help="suppress confirmation output")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the frames instead of sending them")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show everything the device reports")

    p = sub.add_parser("light", help="dongle lighting")
    ls = p.add_subparsers(dest="action", required=True)
    for name in ("on", "off", "toggle", "status"):
        ls.add_parser(name)
    q = ls.add_parser("color", aliases=["colour"]); q.add_argument("hex")
    q = ls.add_parser("brightness"); q.add_argument("value", type=int)
    q = ls.add_parser("speed"); q.add_argument("value", type=int)
    q = ls.add_parser("effect"); q.add_argument("name", choices=DONGLE_EFFECTS)

    p = sub.add_parser("mouse-light", help="mouse lighting")
    ms = p.add_subparsers(dest="action", required=True)
    for name in ("on", "off", "toggle", "status"):
        ms.add_parser(name)
    q = ms.add_parser("color", aliases=["colour"]); q.add_argument("hex")

    p = sub.add_parser("all", help="dongle and mouse lighting together")
    p.add_argument("action", choices=["on", "off", "toggle"])

    p = sub.add_parser("rate", help="polling rate in Hz")
    p.add_argument("hz", type=int, choices=VALID_RATES)

    p = sub.add_parser("toggle", help="sensor options (.97)")
    p.add_argument("feature", choices=sorted(FLAGS))
    p.add_argument("state", choices=["on", "off"])

    p = sub.add_parser("lod", help="lift-off distance, 0 low .. 2 high (.97)")
    p.add_argument("value", type=int, choices=[0, 1, 2])

    p = sub.add_parser("profile", help="switch onboard profile (.97)")
    p.add_argument("index", type=int)

    p = sub.add_parser("remap", help="what a side button sends")
    p.add_argument("button", nargs="?", choices=sorted(REMAP_KEY_ID))
    p.add_argument("key", nargs="?",
                   help="F1-F24, a HID usage like 0x47, or 'default'")

    p = sub.add_parser("raw",
                       help="send one command id and print the reply (.97)")
    p.add_argument("cmd_id", help="decimal or 0x-prefixed")
    p.add_argument("--to", default="0x80", help="device id (0x00 or 0x80)")

    return ap


def light_state(cur, action):
    return (not cur["on"]) if (action == "toggle" and cur) else (action == "on")


def run_command(m, args, say):
    """Run one command on one mouse. Returns data for --json status."""
    c = args.cmd
    is100 = m.model == ".100"
    if is100 and (c in ONLY_97 or
                  (c == "light" and args.action in ONLY_97_LIGHT) or
                  (c == "mouse-light" and args.action in ("color", "colour"))):
        what = c
        if c in ("light", "mouse-light"):
            what += " " + args.action
        raise Unsupported(f"'{what}' is not available on the .100 yet "
                          f"-- skipped")

    if c == "status":
        s = collect_status(m)
        if not args.json:
            print_status(s)
        return s

    if c in ("light", "mouse-light"):
        dongle = c == "light"
        read = m.dongle_light if dongle else m.mouse_light
        a = args.action
        if a == "status":
            cur = read()
            if not cur:
                raise AmError("no reply")
            if dongle is False and not is100:
                cur = dict(cur, colour=m.mouse_colour())
            print(json.dumps(dict(cur, model=m.model), indent=2))
        elif a in ("on", "off", "toggle"):
            if dongle and not m.has_dongle:
                raise AmError("dongle lighting needs the 2.4G dongle connected")
            on = light_state(read(), a)
            if is100:
                (m.set_dongle_light if dongle else m.set_mouse_light)(on)
            else:
                (m.set_dongle_light if dongle else m.set_mouse_light)(on=on)
            say(f"{'dongle' if dongle else 'mouse'} light -> "
                f"{'on' if on else 'off'}")
        elif a in ("color", "colour"):
            if dongle and is100:
                m.set_dongle_colour(args.hex)
                say(f"dongle colour -> #{args.hex.lstrip('#').upper()}")
            elif dongle:
                hue, sat = hex_to_hue_sat(args.hex)
                m.set_dongle_light(hue1=hue, sat1=sat)
                say(f"dongle colour -> #{args.hex.lstrip('#').upper()} "
                    f"(hue {hue}, sat {sat})")
            else:
                m.set_mouse_colour(args.hex)
                say(f"mouse colour -> #{args.hex.lstrip('#').upper()}")
        elif a == "brightness":
            m.set_dongle_light(brightness=args.value)
            say(f"dongle brightness -> {args.value}")
        elif a == "speed":
            m.set_dongle_light(speed=args.value)
            say(f"dongle speed -> {args.value}")
        elif a == "effect":
            m.set_dongle_light(effect=DONGLE_EFFECTS[args.name])
            say(f"dongle effect -> {args.name}")

    elif c == "all":
        if m.has_dongle:
            on = light_state(m.dongle_light(), args.action)
            if is100:
                m.set_dongle_light(on)
            else:
                m.set_dongle_light(on=on)
            say(f"dongle light -> {'on' if on else 'off'}")
        else:
            say("no dongle connected -- dongle lighting skipped")
        on = light_state(m.mouse_light(), args.action)
        if is100:
            m.set_mouse_light(on)
        else:
            m.set_mouse_light(on=on)
        say(f"mouse light -> {'on' if on else 'off'}")

    elif c == "rate":
        m.set_polling_rate(args.hz)
        say(f"polling rate -> {args.hz} Hz")

    elif c == "toggle":
        m.set_flag(FLAGS[args.feature], args.state == "on")
        say(f"{args.feature} -> {args.state}")

    elif c == "lod":
        m.set_lod(args.value)
        say(f"lift-off distance -> {args.value}")

    elif c == "profile":
        m.set_profile(args.index)
        say(f"profile -> {args.index}")

    elif c == "remap":
        if args.button and args.key:
            usage = parse_remap_key(args.key)
            m.set_key_remap(args.button, usage)
            say(f"{args.button} -> "
                f"{'default' if usage is None else key_name(usage)}")
        else:
            buttons = [args.button] if args.button else sorted(REMAP_KEY_ID,
                                                               reverse=True)
            labels = {b: m.remap_label(b) for b in buttons}
            if args.json:
                print(json.dumps(dict(labels, model=m.model), indent=2))
            else:
                for b, label in labels.items():
                    print(f"{b:<8} {label}")

    elif c == "raw":
        v = m.send(int(args.cmd_id, 0), device_id=int(args.to, 0))
        print(v.hex(" ") if v else "no reply")
    return None


def open_mice(args):
    """Every supported mouse that's connected (or just the one --mouse asks
    for). Returns (mice, errors)."""
    mice, errors = [], []
    wanted = [(".97", Am97), (".100", Am100)]
    if args.mouse != "auto":
        wanted = [w for w in wanted if w[0] == "." + args.mouse]
    for name, cls in wanted:
        if args.dry_run:
            if args.mouse != "auto" or name == ".97":
                mice.append(cls.open(prefer=args.device, dry_run=True))
            continue
        try:
            mice.append(cls.open(prefer=args.device))
        except DeviceNotFound:
            if args.mouse != "auto":
                errors.append(f"AM Infinity {name} not found.")
        except AmError as exc:
            errors.append(str(exc))
    return mice, errors


def main(argv=None):
    args = build_parser().parse_args(argv)
    say_base = (lambda *a: None) if args.quiet else print

    mice, errors = open_mice(args)
    if not mice:
        for e in errors or ["No AM Infinity .97 or .100 found. Plug in the "
                            "2.4G dongle or connect the mouse by cable."]:
            print(e, file=sys.stderr)
        return 1

    several = len(mice) > 1
    failed = False
    status_out = []
    try:
        for m in mice:
            say = say_base
            if several and not args.quiet and not args.json:
                print(f"--- {m.model}")
            try:
                out = run_command(m, args, say)
                if out is not None:
                    status_out.append(out)
            except Unsupported as exc:
                say(str(exc))
            except AmError as exc:
                failed = True
                print(f"{m.model}: {exc}", file=sys.stderr)
            except Exception as exc:                         # pragma: no cover
                failed = True
                print(f"{m.model}: {args.cmd} failed: {exc}", file=sys.stderr)
    finally:
        for m in mice:
            m.close()

    if args.json and args.cmd == "status":
        print(json.dumps(status_out, indent=2))
    for e in errors:
        print(e, file=sys.stderr)
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
