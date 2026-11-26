"""The 2.2.0 driver for the AM Infinity .100, moved here unchanged.

Replaced by aminf.m100; deleted once nothing imports it.
"""
from __future__ import annotations

import time

from aminf.colour import hex_to_rgb
from aminf.errors import AmError, DeviceBusy, DeviceNotFound, MouseOffline
from aminf.keys import MODIFIER_NAMES, key_name
from aminf.models import MODELS


def rgb_hex(b):
    return "#%02X%02X%02X" % tuple(b[:3])


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


class Am100Link:
    """One open .100 interface (the dongle, or the mouse over USB)."""

    def __init__(self, backend, is_dongle, report_id, dev, path=None):
        self.backend = backend
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
            self.dev = self.backend.open(self.path)
        except Exception:
            pass                      # the next attempt will report it

    def _write(self, pkt, attempts=5):
        if pkt[0] not in M100_ALLOWED:
            raise AmError(f"refusing .100 command {pkt[0]:#04x}")
        last = None
        for attempt in range(1, attempts + 1):
            try:
                self.dev.send_feature(bytes([self.rid]) + bytes(pkt))
                return
            except (OSError, ValueError) as exc:
                last = exc
                if attempt < attempts:
                    self._reopen()
                    time.sleep(0.15 * attempt)
        raise AmError(f"HID write failed: {last}")

    def _read(self):
        r = list(self.dev.get_feature(self.rid, M100_LEN + 1))
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

    model = MODELS["100"]

    def __init__(self, dongle=None, mouse=None, dry_run=False):
        self.dongle = dongle
        self.mouse = mouse
        self.dry_run = dry_run
        self.offline = False      # set once the dongle reports the mouse away

    @property
    def has_dongle(self):
        return self.dongle is not None or self.dry_run

    @property
    def connection(self):
        return "dongle" if self.has_dongle else "usb"

    # -- discovery ---------------------------------------------------------

    @staticmethod
    def _candidates(backend, prefer):
        out = []
        for d in backend.enumerate():
            if d.vendor_id != M100_VID:
                continue
            pid = d.product_id
            if pid not in (M100_PID_DONGLE, M100_PID_MOUSE):
                continue            # boot-mode pids are deliberately ignored
            is_dongle = pid == M100_PID_DONGLE
            if (prefer == "dongle" and not is_dongle) or \
                    (prefer == "usb" and is_dongle):
                continue
            vendor = d.usage_page in M100_USAGE_PAGES
            out.append((0 if vendor else 1, is_dongle, d.path))
        out.sort(key=lambda c: c[0])
        return out

    @staticmethod
    def _probe(backend, path, is_dongle):
        """Open an interface and find the report id that answers a harmless
        status read -- the same health check the configurator runs."""
        try:
            dev = backend.open(path)
        except Exception:
            return None
        for rid in M100_REPORT_IDS:
            link = Am100Link(backend, is_dongle, rid, dev, path)
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
    def present(cls, backend, prefer="auto"):
        return bool(cls._candidates(backend, prefer))

    @classmethod
    def open(cls, backend, prefer="auto", dry_run=False):
        if dry_run:
            return cls(dry_run=True)
        dongle = mouse = None
        seen = False
        for _pri, is_dongle, path in cls._candidates(backend, prefer):
            seen = True
            if (is_dongle and dongle) or (not is_dongle and mouse):
                continue
            link = cls._probe(backend, path, is_dongle)
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
