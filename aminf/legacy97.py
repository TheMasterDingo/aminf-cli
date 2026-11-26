"""The 2.2.0 driver for the AM Infinity .97, moved here unchanged.

Replaced piece by piece by aminf.race; deleted once nothing imports it.
"""
from __future__ import annotations

import time

from aminf.colour import hex_to_rgb
from aminf.errors import AmError, DeviceBusy, DeviceNotFound
from aminf.models import MODELS


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


# --------------------------------------------------------------- the device

class Am97:
    """Vendor HID client for the AM Infinity .97 and, for now, the .100R2,
    which speaks the same protocol."""

    def __init__(self, backend, path, has_dongle, dry_run=False, model=None):
        self.backend = backend
        self.model = model or MODELS["97"]
        self.has_dongle = has_dongle
        self.dry_run = dry_run
        # Mouse-side settings travel over the relay only via the dongle.
        self.mouse_id = DEV_RELAY if has_dongle else DEV_LOCAL
        self.dev = None
        if dry_run:
            return
        try:
            self.dev = backend.open(path)
        except Exception as exc:
            self.dev = None
            raise DeviceBusy(
                f"could not open the device ({exc}). If the web configurator "
                f"is open in a browser tab, close it and try again."
            ) from exc

    # -- discovery ---------------------------------------------------------

    @staticmethod
    def discover(backend, prefer="auto", model=None):
        """Return (path, has_dongle) or None.

        prefer: 'auto' (dongle first), 'dongle', or 'usb'. The dongle is
        preferred because it owns the dongle lighting and can still reach the
        mouse by relay, so it can service every command on its own.
        """
        model = model or MODELS["97"]
        dongle = usb = None
        for d in backend.enumerate():
            if d.usage_page != USAGE_PAGE:
                continue
            ids = (d.vendor_id, d.product_id)
            if ids in model.dongle_ids:
                dongle = (d.path, True)
            elif ids in model.usb_ids:
                usb = (d.path, False)
        if prefer == "dongle":
            return dongle
        if prefer == "usb":
            return usb
        return dongle or usb

    @classmethod
    def open(cls, backend, prefer="auto", dry_run=False, model=None):
        model = model or MODELS["97"]
        found = cls.discover(backend, prefer, model)
        if not found:
            if dry_run:
                return cls(backend, None, True, dry_run=True, model=model)
            raise DeviceNotFound(
                f"AM Infinity {model.name} not found. Plug in the 2.4G "
                f"dongle or connect the mouse by cable.")
        return cls(backend, *found, dry_run=dry_run, model=model)

    @property
    def connection(self):
        return "dongle" if self.has_dongle else "usb"

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
        self.dev.send_feature(bytes([REPORT_ID]) + frame)
        if not expect_reply:
            return None
        # A read may carry several stacked responses, and a reply left over
        # from an earlier write-only command can arrive first, so walk the
        # frames and keep polling until the command id matches.
        for _ in range(tries):
            r = self.dev.get_feature(REPORT_ID, PAYLOAD_LEN + 1)
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
