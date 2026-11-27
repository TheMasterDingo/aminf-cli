"""Driver for the AM Infinity .100.

Holds up to two links: the dongle (its own lighting, and the mouse by relay)
and the mouse on a cable (mouse settings directly). Packets come from
aminf.m100.codec. Every write is followed by a read of the same setting and
only succeeds if the mouse reports the new value.

When on the dongle, a command for the mouse is relayed with the handshake
the configurator uses: open the channel (F6 05), wait until the dongle's
status (F7) says it is ready, set the length (FE 40), send, wait, and for
reads fetch the answer (FC).
"""
from __future__ import annotations

import time

from aminf.actions import (BUTTON_NAMES, check_left_click, format_action,
                           parse_action)
from aminf.colour import hex_to_rgb, rgb_to_hex
from aminf.device import Mouse, reject_key
from aminf.errors import (AmError, DeviceBusy, DeviceNotFound, MouseOffline,
                          NotConfirmed, Unsupported)
from aminf.m100 import codec
from aminf.models import DPI_MIN, DPI_STEP

sleep = time.sleep           # one place to wait, so tests need not

VID = 0x3151
USAGE_PAGES = (0xFF01, 0xFFFF)
# Report ids the configurator falls back through when a device does not
# declare one. Only a read-only status packet is used to find the right one.
REPORT_IDS = (0, 4, 5, 6, 7, 20, 21, 22, 23)
STATUS_PKT = codec.raw(codec.DONGLE_STATUS)

DONGLE_EFFECTS = {0: "off", 1: "solid", 2: "breathing", 3: "neon", 4: "wave",
                  5: "marquee", 6: "ring", 7: "buffer", 8: "chase"}
EFFECT_IDS = {v: k for k, v in DONGLE_EFFECTS.items()}
COLOUR_CUSTOM = 7            # option 0-6 are the configurator's swatches;
                             # 7 means "use the rgb bytes"
EFFECTS_NO_SPEED = (0, 1)    # off / solid: the configurator sends speed 0
VERIFY_DELAYS = (0.15, 0.3, 0.6)
# After a key-map write: read back on this schedule, and if the mouse has
# not taken it, write once more and wait longer -- the configurator's own.
KEYMAP_DELAYS = ((0.25, 0.4, 0.6), (0.5, 0.7))
PROFILE_DELAYS = (0.25, 0.4, 0.6)
PROFILE_COUNT = 2            # what the configurator sends as "profile max"
SLEEP_NEVER = 0xFFFF
SLEEP_MIN = 15

PARAM_SENSOR = ("polling_rate", "lod", "motion_sync", "angle_snap", "ripple")
PARAM_POWER = ("sleep_24g", "sleep_bt", "deep_sleep_24g", "deep_sleep_bt",
               "debounce_wired", "debounce_24g", "debounce_bt")
SLEEP_KEYS = ("sleep_24g", "sleep_bt", "deep_sleep_24g", "deep_sleep_bt")
TOGGLE_SENSOR = ("fps_mode", "angle", "profile_key")


def hexdump(pkt, n=None):
    """Hex through the last non-zero byte, at least 8 bytes."""
    if n is None:
        last = max((i for i, x in enumerate(pkt) if x), default=0)
        n = max(8, last + 1)
    return " ".join(f"{x:02x}" for x in pkt[:n])


def _int8(v):
    return v - 256 if v > 127 else v


def _int_in(name, v, low, high):
    if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
        raise AmError(f"{name} must be {low}-{high}")
    return v


def _flag_in(name, v):
    if not isinstance(v, bool):
        raise AmError(f"{name} must be on or off")
    return v


class M100Link:
    """One open .100 interface (the dongle, or the mouse on a cable)."""

    ROUTE_TIMEOUT = 3.0
    REPLY_TIMEOUT = 5.0

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
        sleep(0.05)
        try:
            self.dev = self.backend.open(self.path)
        except Exception:
            pass                      # the next attempt will report it

    def _write(self, pkt, attempts=5):
        if pkt[0] not in codec.ALLOWED:
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
                    sleep(0.15 * attempt)
        raise AmError(f"HID write failed: {last}")

    def _read(self):
        r = bytes(self.dev.get_feature(self.rid, codec.LEN + 1))
        if len(r) == codec.LEN + 1 and r[0] == self.rid:
            r = r[1:]
        return r[:codec.LEN].ljust(codec.LEN, b"\x00")

    def send(self, pkt, expect=False, delay=0.03, attempts=5):
        self._write(pkt, attempts)
        if not expect:
            sleep(0.005)
            return None
        sleep(delay)
        last = None
        for m in range(1, 4):
            try:
                r = self._read()
            except (OSError, ValueError) as exc:
                last = exc
                if m < 3:
                    sleep(0.05 * m)
                continue
            if any(r):
                sleep(0.005)
                return r
            last = "empty reply"
            if m < 3:
                sleep(0.03 * m)
        raise AmError(f"no reply to {pkt[0]:#04x} ({last})")

    # -- relay through the dongle ------------------------------------------

    @staticmethod
    def _route_ready(s):
        return s[5] == 1 or (s[0] == 0 and s[4] == 0 and s[6] == 2)

    def _wait(self, cond, timeout):
        end = time.monotonic() + timeout
        s = None
        while time.monotonic() < end:
            s = self.send(STATUS_PKT, expect=True)
            if s and cond(s):
                return s
            sleep(0.1)
        raise AmError(f"dongle not ready ({hexdump(s) if s else 'no status'})")

    def forward(self, pkt, expect):
        last = None
        for attempt in range(3):
            if attempt:
                sleep(0.1)
            try:
                self.send(codec.raw(codec.FWD_OPEN, 5))
                s = self._wait(self._route_ready, self.ROUTE_TIMEOUT)
                if s[4] == 1:
                    raise MouseOffline("mouse is off or out of range")
                self.send(codec.raw(codec.FWD_LENGTH, codec.LEN))
                self.send(pkt)
                if not expect:
                    self._wait(self._route_ready, self.ROUTE_TIMEOUT)
                    return None
                self._wait(lambda s: s[0] == 1 or s[0] == pkt[0],
                           self.REPLY_TIMEOUT)
                sleep(0.01)
                return self.send(codec.raw(codec.FWD_FETCH), expect=True)
            except AmError as exc:
                last = exc
        try:
            self.send(codec.raw(codec.FWD_OPEN, 0))     # close the relay channel
        except AmError:
            pass
        raise last


class M100Mouse(Mouse):
    def __init__(self, dongle, mouse, model, dry_run=False):
        self.dongle = dongle
        self.mouse = mouse
        self.model = model
        self.dry_run = dry_run
        self.offline = False      # set once the dongle reports the mouse away

    @property
    def connection(self):
        return "dongle" if self.dongle is not None or self.dry_run else "usb"

    def forget_offline(self):
        self.offline = False

    # -- discovery ---------------------------------------------------------

    @staticmethod
    def _candidates(backend, model, prefer):
        out = []
        for d in backend.enumerate():
            ids = (d.vendor_id, d.product_id)
            is_dongle = ids in model.dongle_ids
            if not is_dongle and ids not in model.usb_ids:
                continue            # boot-mode ids are deliberately ignored
            if (prefer == "dongle" and not is_dongle) or \
                    (prefer == "usb" and is_dongle):
                continue
            vendor = d.usage_page in USAGE_PAGES
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
        for rid in REPORT_IDS:
            link = M100Link(backend, is_dongle, rid, dev, path)
            try:
                if is_dongle:
                    ok = bool(link.send(STATUS_PKT, expect=True, attempts=1))
                else:
                    r = link.send(codec.cmd(codec.GET_PROFILE), expect=True,
                                  attempts=1)
                    ok = r[0] == codec.GET_PROFILE
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
    def present(cls, backend, model, prefer="auto"):
        return bool(cls._candidates(backend, model, prefer))

    @classmethod
    def open(cls, backend, model, prefer="auto", dry_run=False):
        if dry_run:
            return cls(None, None, model, dry_run=True)
        dongle = mouse = None
        seen = False
        for _pri, is_dongle, path in cls._candidates(backend, model, prefer):
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
            return cls(dongle, mouse, model)
        if seen:
            raise DeviceBusy(
                f"AM Infinity {model.name} found but it didn't answer. If "
                f"the web configurator or AM Master is open, close it and "
                f"try again.")
        raise DeviceNotFound(f"AM Infinity {model.name} not found.")

    def close(self):
        for link in (self.dongle, self.mouse):
            if link:
                link.close()

    # -- routing: mouse settings go by cable if there is one, else relay ----

    def _to_mouse(self, pkt, expect):
        if self.dry_run:
            if not expect:
                print(f"  would send to mouse  {hexdump(pkt)}")
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
            if not expect:
                print(f"  would send to dongle {hexdump(pkt)}")
            return None
        if not self.dongle:
            raise AmError("dongle lighting needs the 2.4G dongle connected")
        return self.dongle.send(pkt, expect=expect)

    def _confirm(self, name, wanted, read, delays=VERIFY_DELAYS):
        if self.dry_run:
            return wanted
        got = None
        for delay in delays:
            sleep(delay)
            got = read()
            if got == wanted:
                return got
        raise NotConfirmed(name, wanted, got)

    # -- blocks: read whole, change bytes, write whole -----------------------

    def _block(self, read_cmd, params, what):
        r = self._to_mouse(codec.cmd(read_cmd, params), expect=True)
        if not r or len(r) != codec.LEN or r[0] != read_cmd:
            raise AmError(f"could not read the {what} -- nothing written")
        return r

    def _params(self):
        return self._block(codec.GET_PARAMS, (), "parameter block")

    def _dpi_block(self, profile):
        return self._block(codec.GET_DPI, (profile,), "DPI block")

    def _patch_params(self, changes, label):
        """Write section values into the parameter block and confirm them."""
        offsets = codec.param_changes(changes)
        if self.dry_run:
            what = ", ".join(f"byte {o} = {v & 0xFF:#04x}"
                             for o, v in offsets.items())
            print(f"  would read the parameter block from the mouse and "
                  f"write it back with {what}")
            return
        block = self._params()
        self._to_mouse(codec.patch_block(block, codec.SET_PARAMS, offsets),
                       expect=False)

        def state():
            now = self._params()
            return {o: now[o] for o in offsets}
        self._confirm(label, {o: v & 0xFF for o, v in offsets.items()}, state)

    def _toggle_get(self, name):
        read, _ = codec.TOGGLES[name]
        r = self._to_mouse(codec.cmd(read), expect=True)
        if not r or r[0] != read:
            return None
        return _int8(r[1]) if name == "angle" else r[1] == 1

    def _toggle_set(self, name, value):
        _, write = codec.TOGGLES[name]
        byte = value & 0xFF if name == "angle" else (1 if value else 0)
        self._to_mouse(codec.cmd(write, [byte]), expect=False)
        return self._confirm(name, value, lambda: self._toggle_get(name))

    # -- basics --------------------------------------------------------------

    def battery(self):
        if self.dry_run:
            return None
        try:
            b = self._to_mouse(codec.cmd(codec.GET_BATTERY), expect=True)
        except MouseOffline:
            raise
        except AmError:
            return None
        if not b or b[0] != codec.GET_BATTERY or not b[2] or b[3] > 100:
            return None
        return {"percent": b[3], "charging": b[1] == 1, "present": True}

    def hub_battery(self):
        """Spare battery in the dongle, from the dongle's status packet."""
        if not self.dongle or self.dry_run:
            return None
        try:
            s = self.dongle.send(STATUS_PKT, expect=True)
        except AmError:
            return None
        if not s or not 0 < s[10] <= 100:
            return None
        return {"percent": s[10], "charging": None, "present": True}

    def _profile(self):
        r = self._to_mouse(codec.cmd(codec.GET_PROFILE), expect=True)
        if not r or r[0] != codec.GET_PROFILE or r[1] > 7:
            raise AmError("could not read the active profile -- nothing "
                          "written")
        return r[1]

    def profile(self):
        if self.dry_run:
            return None
        try:
            return self._profile()
        except MouseOffline:
            raise
        except AmError:
            return None

    def set_profile(self, index):
        _int_in("profile", index, 0, self.model.profiles - 1)
        # The configurator always sends its own profile count here.
        self._to_mouse(codec.set_profile(index, PROFILE_COUNT), expect=False)
        self._confirm("profile", index, self._profile, PROFILE_DELAYS)
        # ...and then turns performance mode off, which a switch leaves in an
        # undefined state. aminf does the same.
        self._to_mouse(codec.cmd(codec.TOGGLES["fps_mode"][1], [0]),
                       expect=False)
        return index

    # -- sections ------------------------------------------------------------

    def read(self, section):
        if self.dry_run:
            return None
        if section == "sensor":
            p = codec.read_params(self._params())
            out = {k: p[k] for k in PARAM_SENSOR}
            for name in TOGGLE_SENSOR:
                v = self._toggle_get(name)
                if v is not None:
                    out[name] = v
            return out
        if section == "power":
            p = codec.read_params(self._params())
            return {k: ("never" if k in SLEEP_KEYS and p[k] == SLEEP_NEVER
                        else p[k]) for k in PARAM_POWER}
        if section == "dpi":
            out = codec.parse_dpi(self._dpi_block(self._profile()))
            button = self._toggle_get("dpi_button")
            if button is not None:
                out["button"] = button
            return out
        if section == "buttons":
            return {b: format_action(a) for b, a in self._buttons().items()}
        if section == "light":
            p = codec.read_params(self._params())
            return {"on": p["light_on"], "colour": p["colour"],
                    "colour_charged": p["colour_charged"]}
        if section == "dongle_light":
            raw = self._dongle_light_raw()
            if raw is None:
                return None
            return {"on": raw["on"], "effect": raw["effect_name"],
                    "speed": raw["speed"], "brightness": raw["brightness"],
                    "colour": raw["colour"], "preset": raw["preset"]}
        raise AmError(f"unknown section '{section}'")

    def write(self, section, changes, strict=True):
        changes = dict(changes)
        if section == "sensor":
            return self._write_sensor(changes, strict)
        if section == "power":
            return self._write_power(changes, strict)
        if section == "dpi":
            return self._write_dpi(changes, strict)
        if section == "buttons":
            return self._write_buttons(changes)
        if section == "light":
            return self._write_light(changes)
        if section == "dongle_light":
            return self._write_dongle_light(changes, strict)
        raise AmError(f"unknown section '{section}'")

    # sensor ...............................................................

    def _write_sensor(self, changes, strict):
        for key in changes:
            if key not in PARAM_SENSOR + TOGGLE_SENSOR:
                reject_key("sensor", key, self.model)
        if "polling_rate" in changes and \
                changes["polling_rate"] not in codec.RATE_CODE:
            raise AmError(f"polling rate must be one of "
                          f"{tuple(codec.RATE_CODE)}")
        if "lod" in changes:
            _int_in("lod", changes["lod"], 0,
                    self.model.lod_max if strict else 255)
        for key in ("motion_sync", "angle_snap", "ripple", "fps_mode",
                    "profile_key"):
            if key in changes:
                _flag_in(key, changes[key])
        if "angle" in changes:
            _int_in("angle", changes["angle"], -90, 90)

        block = {k: changes[k] for k in PARAM_SENSOR if k in changes}
        if block:
            self._patch_params(block, "sensor settings")
        done = dict(block)
        for name in TOGGLE_SENSOR:
            if name in changes:
                done[name] = self._toggle_set(name, changes[name])
        return done

    # power ................................................................

    def _write_power(self, changes, strict):
        for key in changes:
            if key not in PARAM_POWER:
                reject_key("power", key, self.model)
        block = {}
        for key, v in changes.items():
            if key in SLEEP_KEYS:
                if v == "never" or v == -1:
                    block[key] = SLEEP_NEVER
                else:
                    block[key] = _int_in(f"{key} (seconds, or 'never')", v,
                                         SLEEP_MIN if strict else 1,
                                         SLEEP_NEVER - 1)
            else:
                block[key] = _int_in(key, v, 0, 255)
        if block:
            self._patch_params(block, "power settings")
        return dict(changes)

    # dpi ..................................................................

    def _dpi_value(self, axis, v, strict):
        if isinstance(v, bool) or not isinstance(v, int):
            raise AmError(f"DPI {axis} must be a number, not {v!r}")
        if strict:
            if not DPI_MIN <= v <= self.model.dpi_max or v % DPI_STEP:
                raise AmError(f"DPI {axis} {v} is not valid: the "
                              f"{self.model.name} takes {DPI_MIN}-"
                              f"{self.model.dpi_max} in steps of {DPI_STEP}")
        elif not 1 <= v <= 0xFFFF:
            raise AmError(f"DPI {axis} must be 1-65535")
        return v

    def _write_dpi(self, changes, strict):
        for key in changes:
            if key not in ("stages", "count", "active", "button"):
                reject_key("dpi", key, self.model)
        stages = {}
        for key, ch in (changes.get("stages") or {}).items():
            try:
                i = int(key)
            except (TypeError, ValueError):
                i = -1
            if not 0 <= i < codec.DPI_STAGES:
                raise AmError(f"DPI stage must be 1-{codec.DPI_STAGES}")
            st = {}
            for k, v in ch.items():
                if k in ("x", "y"):
                    st[k] = self._dpi_value(k, v, strict)
                elif k == "colour":
                    if v is not None:
                        st[k] = rgb_to_hex(*hex_to_rgb(v))
                else:
                    raise AmError(f"dpi stage {i + 1}: unknown setting '{k}'")
            if st:
                stages[i] = st
        if "count" in changes:
            _int_in("DPI stage count", changes["count"], 1, codec.DPI_STAGES)
        if "button" in changes:
            _flag_in("DPI button", changes["button"])

        done = {}
        block_changes = {k: changes[k] for k in ("count", "active")
                         if k in changes}
        if stages:
            block_changes["stages"] = stages
        if block_changes:
            if self.dry_run:
                offsets = codec.dpi_changes(block_changes, None)
                what = ", ".join(f"byte {o} = {v & 0xFF:#04x}"
                                 for o, v in sorted(offsets.items()))
                print(f"  would read the DPI block from the mouse and write "
                      f"it back with {what}")
            else:
                profile = self._profile()
                block = self._dpi_block(profile)
                cur = codec.parse_dpi(block)
                count = changes.get("count", cur["count"])
                active = changes.get("active", cur["active"])
                if "active" in changes or "count" in changes:
                    if "active" not in changes:
                        active = min(active, count - 1)
                        block_changes["active"] = active
                    _int_in("active DPI stage", active, 0, count - 1)
                offsets = codec.dpi_changes(block_changes, cur)
                self._to_mouse(codec.dpi_patch(block, offsets), expect=False)

                def state():
                    now = self._dpi_block(profile)
                    return {o: now[o] for o in offsets}
                self._confirm("DPI", {o: v & 0xFF for o, v in offsets.items()},
                              state)
                if block_changes.get("active") is not None and \
                        "active" not in changes and active != cur["active"]:
                    done["active"] = active
            for k in ("count", "active"):
                if k in changes:
                    done[k] = changes[k]
            if stages:
                done["stages"] = {
                    i: ({**st, "y": st.get("y", st["x"])} if "x" in st else st)
                    for i, st in stages.items()}
        if "button" in changes:
            done["button"] = self._toggle_set("dpi_button", changes["button"])
        return done

    # buttons ..............................................................

    def _keymap(self, profile):
        # The key map comes back without an echo of the command byte, so a
        # reply left over from an earlier command can only be told apart by
        # asking again: a real key map never changes between two reads.
        r = self._to_mouse(codec.cmd(codec.GET_KEYMAP, [profile, 0]),
                           expect=True)
        if not r or len(r) != codec.LEN:
            raise AmError("could not read the key map")
        return r

    def _buttons(self, profile=None):
        """{button: Action} for the seven buttons."""
        km = self._keymap(self._profile() if profile is None else profile)
        out = {}
        for button in self.model.buttons:
            at = codec.KEY_OFFSET[codec.BUTTON_INDEX[button]]
            out[button] = codec.decode_action(km[at:at + 4])
        return out

    def _write_buttons(self, changes):
        wanted = {}
        for button, text in changes.items():
            if button not in self.model.buttons:
                if button in BUTTON_NAMES:
                    raise Unsupported(f"the {self.model.name} has no "
                                      f"'{button}' button")
                raise AmError(f"unknown button '{button}' -- use one of "
                              f"{', '.join(self.model.buttons)}")
            action = parse_action(text)
            if action is None:
                action = self.model.default_action(button)
            wanted[button] = (action, codec.encode_action(action))

        if self.dry_run:
            profile = 0
            current = {b: self.model.default_action(b)
                       for b in self.model.buttons}
        else:
            profile = self._profile()
            current = self._buttons(profile)
        check_left_click({**current, **{b: a for b, (a, _) in wanted.items()}})

        done = {}
        for button, (action, mapping) in wanted.items():
            pkt = codec.key_packet(profile, codec.BUTTON_INDEX[button], mapping)
            got = None
            for delays in KEYMAP_DELAYS:
                self._to_mouse(pkt, expect=False)
                if self.dry_run:
                    got = action
                    break
                for delay in delays:
                    sleep(delay)
                    got = self._buttons(profile)[button]
                    if got == action:
                        break
                if got == action:
                    break
            if got != action:
                raise NotConfirmed(f"{button} button", format_action(action),
                                   format_action(got) if got else None)
            done[button] = format_action(action)
        return done

    # mouse light (its charge light) .........................................

    def _write_light(self, changes):
        names = {"on": "light_on", "colour": "colour",
                 "colour_charged": "colour_charged"}
        block = {}
        for key, v in changes.items():
            if key not in names:
                reject_key("light", key, self.model)
            if key == "on":
                block["light_on"] = _flag_in("light on", v)
            else:
                block[key] = rgb_to_hex(*hex_to_rgb(v))
        if block:
            self._patch_params(block, "mouse light")
        return {k: (v if k == "on" else block[names[k]])
                for k, v in changes.items()}

    # dongle light ...........................................................

    def _dongle_light_raw(self):
        """{on, effect (number), effect_name, speed, brightness, colour,
        preset} -- the shape 2.2.0 reported, plus speed."""
        if not self.dongle or self.dry_run:
            return None
        s = self._to_dongle(codec.cmd(codec.GET_DONGLE_SWITCH), expect=True)
        if not s or s[0] != codec.GET_DONGLE_SWITCH:
            return None
        out = {"on": s[1] == 1}
        c = self._to_dongle(codec.cmd(codec.GET_DONGLE_LIGHT), expect=True)
        if c and c[0] == codec.GET_DONGLE_LIGHT:
            out.update({
                "effect": c[1],
                "effect_name": DONGLE_EFFECTS.get(c[1], str(c[1])),
                "speed": c[2],
                "brightness": c[3],           # 0-4
                "colour": rgb_to_hex(c[5], c[6], c[7]),
                "preset": c[4] < COLOUR_CUSTOM,
            })
        return out

    def _write_dongle_light(self, changes, strict):
        if not self.dongle and not self.dry_run:
            raise AmError("dongle lighting needs the 2.4G dongle connected")
        allowed = ("on", "effect", "speed", "brightness", "colour")
        for key in changes:
            if key not in allowed:
                reject_key("dongle_light", key, self.model)
        if "on" in changes:
            _flag_in("light on", changes["on"])
        effect = changes.get("effect")
        if effect is not None and not isinstance(effect, int):
            if effect not in EFFECT_IDS:
                raise AmError(f"dongle light effect must be one of "
                              f"{', '.join(EFFECT_IDS)}")
            effect = EFFECT_IDS[effect]
        if "speed" in changes:
            _int_in("dongle light speed", changes["speed"], 0, 255)
        if "brightness" in changes:
            _int_in("dongle light brightness", changes["brightness"], 0,
                    4 if strict else 255)
        rgb = hex_to_rgb(changes["colour"]) if "colour" in changes else None

        if any(k in changes for k in ("effect", "speed", "brightness", "colour")):
            # [8][effect][speed][bright][option][r][g][b][checksum of those 8]
            if self.dry_run:
                cur = [codec.GET_DONGLE_LIGHT, 1, 0, 4, COLOUR_CUSTOM, 0, 0, 0]
            else:
                cur = self._to_dongle(codec.cmd(codec.GET_DONGLE_LIGHT),
                                      expect=True)
                if not cur or cur[0] != codec.GET_DONGLE_LIGHT:
                    raise AmError("could not read the dongle light -- "
                                  "nothing written")
            eff = cur[1] if effect is None else effect
            speed = changes.get("speed", cur[2])
            if eff in EFFECTS_NO_SPEED:
                speed = 0
            bright = changes.get("brightness", min(cur[3], 4))
            option = COLOUR_CUSTOM if rgb is not None else cur[4]
            colour = rgb if rgb is not None else tuple(cur[5:8])
            pkt = codec.dongle_light_packet(eff, speed, bright, option, colour)
            self._to_dongle(pkt, expect=False)

            def light():
                now = self._to_dongle(codec.cmd(codec.GET_DONGLE_LIGHT),
                                      expect=True)
                return bytes(now[1:8]) if now else None
            self._confirm("dongle light", bytes(pkt[1:8]), light)
        if "on" in changes:
            self._to_dongle(codec.cmd(codec.SET_DONGLE_SWITCH,
                                      [1 if changes["on"] else 0]),
                            expect=False)

            def switch():
                now = self._to_dongle(codec.cmd(codec.GET_DONGLE_SWITCH),
                                      expect=True)
                return bool(now and now[1] == 1)
            self._confirm("dongle light switch", changes["on"], switch)
        done = dict(changes)
        if rgb is not None:
            done["colour"] = rgb_to_hex(*rgb)
        return done

    # -- what 2.2.0 printed ---------------------------------------------------
    #
    # `status` and `light status` keep the exact keys 2.2.0 gave them and
    # cost one parameter-block read.

    def status_2_2(self):
        params = None
        battery = profile = None
        if not self.dry_run:
            for attempt in ("battery", "profile", "params"):
                try:
                    if attempt == "battery":
                        battery = self.battery()
                    elif attempt == "profile":
                        profile = self.profile()
                    else:
                        params = codec.read_params(self._params())
                except AmError:
                    pass
        mouse_light = None
        if params:
            mouse_light = {"on": params["light_on"], "colour": params["colour"]}
            if params["colour_charged"] != params["colour"]:
                mouse_light["colour_charged"] = params["colour_charged"]
        dongle_light = self._dongle_light_raw()
        if dongle_light:
            dongle_light.pop("speed", None)
        return {
            "model": self.model.name,
            "connection": "dongle" if self.dongle else "usb",
            "battery": battery,
            "hub_battery": self.hub_battery(),
            "profile": profile,
            "polling_rate": params["polling_rate"] if params else None,
            "mouse_light": mouse_light,
            "dongle_light": dongle_light,
            "mouse_offline": self.offline,
        }

    def light_status_2_2(self, dongle):
        if dongle:
            raw = self._dongle_light_raw()
            if raw:
                raw.pop("speed", None)
            return raw
        if self.dry_run:
            return None
        p = codec.read_params(self._params())
        out = {"on": p["light_on"], "colour": p["colour"]}
        if p["colour_charged"] != p["colour"]:
            out["colour_charged"] = p["colour_charged"]
        return out
