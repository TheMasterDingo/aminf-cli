"""Driver for the AM Infinity .97 and .100R2.

Talks to one HID interface (the dongle, or the mouse on a cable) with the
frames built in aminf.race.codec. Every write is followed by a read of the
same setting and only succeeds if the mouse reports the new value.
"""
from __future__ import annotations

import time

from aminf.actions import (BUTTON_NAMES, check_left_click, format_action,
                           parse_action)
from aminf.colour import hex_to_hue_sat, hex_to_rgb, hue_to_hex, rgb_to_hex
from aminf.device import Mouse
from aminf.errors import (AmError, DeviceBusy, DeviceNotFound, MouseOffline,
                          NotConfirmed, NotInFirmware, Refused, Unsupported)
from aminf.keys import MODIFIER_NAMES, key_name
from aminf.models import DPI_MIN, DPI_STEP
from aminf.race import codec
from aminf.race.codec import CMD, DEV_LOCAL, DEV_RELAY, FIELDS

USAGE_PAGE = 0xFF13          # vendor collection carrying the config channel
VALID_RATES = (125, 250, 500, 1000, 2000, 4000, 8000)

DONGLE_EFFECTS = {"solid": 1, "breathing": 2, "arc": 3, "mosaic": 4,
                  "aurora": 5, "night": 6, "flow": 7}
MOUSE_EFFECTS = {0: "solid", 1: "breathing", 2: "neon"}

# Used only for a --dry-run, which never reads the device.
DONGLE_LIGHT_FALLBACK = {
    "on": True, "effect": 1, "hue1": 0, "sat1": 100,
    "hue2": 0, "sat2": 100, "hue3": 0, "sat3": 100,
    "speed": 10, "brightness": 200,
}
MOUSE_LIGHT_FALLBACK = {"on": True, "effect": 0, "speed": 1}

RGB_FRAME_INTERVAL = 16
RGB_LED_COUNT = 1
RGB_REALTIME_FLAG = 0x80

# Writes the firmware does not acknowledge; everything else answers.
NO_ACK = frozenset({CMD["setDongleLight"], CMD["setLightSettings"],
                    CMD["setRgbRealtime"]})

SENSOR_FLAGS = ("motion_sync", "angle_snap", "ripple", "fps_mode",
                "profile_key")
SENSOR_KEYS = ("polling_rate", "lod") + SENSOR_FLAGS + ("angle",
                                                        "angle_enabled")
POWER_KEYS = ("sleep_24g", "sleep_bt", "debounce_wired", "debounce_24g",
              "debounce_bt", "hw_debounce_press", "hw_debounce_release")
SLEEP_MIN, U16_MAX = 15, 0xFFFF


def _sleep_out(v):
    return "never" if v == -1 else v


def _sleep_in(v, strict):
    if v == "never" or v == -1:
        return -1
    if isinstance(v, bool) or not isinstance(v, int):
        raise AmError(f"sleep time must be a number of seconds or 'never', "
                      f"not {v!r}")
    low = SLEEP_MIN if strict else 0
    if not low <= v < U16_MAX:
        raise AmError(f"sleep time must be {low}-{U16_MAX - 1} seconds or "
                      f"'never'")
    return v


def _int_in(name, v, low, high):
    if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
        raise AmError(f"{name} must be {low}-{high}")
    return v


def _flag_in(name, v):
    if not isinstance(v, bool):
        raise AmError(f"{name} must be on or off")
    return v


class RaceMouse(Mouse):
    TIMEOUT = 1.5           # seconds to wait for a direct reply
    RELAY_TIMEOUT = 3.0     # ... for one relayed through the dongle
    ACK_TIMEOUT = 0.6       # ... for the acknowledgement of a write
    VERIFY_DELAY = 0.1      # between read-backs after a write
    VERIFY_TRIES = 3
    POLL = 0.02             # between polls while the device is still busy

    def __init__(self, handle, model, has_dongle, dry_run=False):
        self.dev = handle
        self.model = model
        self.has_dongle = has_dongle
        self.dry_run = dry_run
        self.offline = False
        # Mouse settings travel through the dongle's relay when on 2.4G.
        self.target = DEV_RELAY if has_dongle else DEV_LOCAL
        self.mouse_id = self.target          # 2.2.0 name, used by `raw`
        self._reader = codec.FrameReader()
        self._probed = False

    # -- discovery ---------------------------------------------------------

    @staticmethod
    def discover(backend, model, prefer="auto"):
        """(path, has_dongle) or None. prefer: 'auto' (dongle first),
        'dongle' or 'usb'. The dongle is preferred because it owns the
        dongle lighting and still reaches the mouse by relay."""
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
    def open(cls, backend, model, prefer="auto", dry_run=False):
        found = cls.discover(backend, model, prefer)
        if not found:
            if dry_run:
                return cls(None, model, True, dry_run=True)
            raise DeviceNotFound(
                f"AM Infinity {model.name} not found. Plug in the 2.4G "
                f"dongle or connect the mouse by cable.")
        path, has_dongle = found
        if dry_run:
            return cls(None, model, has_dongle, dry_run=True)
        try:
            handle = backend.open(path)
        except DeviceBusy as exc:
            raise DeviceBusy(
                f"could not open the device ({exc}). If the web configurator "
                f"is open in a browser tab, close it and try again.") from exc
        return cls(handle, model, has_dongle)

    @property
    def connection(self):
        return "dongle" if self.has_dongle else "usb"

    def close(self):
        if self.dev:
            self.dev.close()

    # -- one command, one answer -------------------------------------------

    def _send(self, cmd, data, device_id):
        frame = codec.frame(device_id, cmd, data)      # may raise Refused
        if self.dry_run:
            print(f"  would send dev={device_id:#04x} cmd={cmd:#06x} "
                  f"{frame[:20].hex(' ')} ...")
            return False
        self.dev.send_feature(bytes([codec.REPORT_ID]) + frame)
        return True

    def _await(self, cmd, timeout):
        """Payload of the next reply to `cmd`; replies to anything else
        (left over from an earlier command) are dropped."""
        end = time.monotonic() + timeout
        while True:
            report = self.dev.get_feature(codec.REPORT_ID,
                                          codec.PAYLOAD_LEN + 1)
            for fr in self._reader.feed(report):
                if fr.cmd == cmd:
                    return fr.payload
            if time.monotonic() >= end:
                return None
            if len(report) < 2 or report[1] == 0:
                time.sleep(self.POLL)

    def request(self, cmd, data=b"", device_id=None, timeout=None):
        """Send one command and return its reply payload, or None when
        nothing answers in time. In a dry run it prints and returns None."""
        device_id = self.target if device_id is None else device_id
        relayed = device_id == DEV_RELAY
        if relayed and self.offline:
            raise MouseOffline("mouse is off or out of range")
        if self.dry_run and cmd in codec.READS:
            return None
        if not self._send(cmd, bytes(data), device_id):
            return None
        if timeout is None:
            timeout = self.RELAY_TIMEOUT if relayed else self.TIMEOUT
        return self._await(cmd, timeout)

    def _read(self, cmd, args=b"", device_id=None):
        """A read whose silence means something. On a dongle, the first
        relayed read that times out is followed by one probe of the profile:
        no answer means the mouse is away (MouseOffline, remembered for the
        rest of the run); an answer means only this setting is missing."""
        device_id = self.target if device_id is None else device_id
        p = self.request(cmd, args, device_id)
        if p is not None or self.dry_run or device_id != DEV_RELAY:
            return p
        if self._probed:
            return None
        alive = cmd != CMD["getProfile"] and \
            self.request(CMD["getProfile"], b"", DEV_RELAY) is not None
        if not alive:
            self.offline = True
            raise MouseOffline("mouse is off or out of range")
        self._probed = True
        return None

    def _write(self, cmd, data, device_id=None, what=None):
        """Send a write. If the firmware answers with a non-zero status the
        write was rejected; silence is left to the read-back to judge."""
        device_id = self.target if device_id is None else device_id
        if not self._send(cmd, bytes(data), device_id) or cmd in NO_ACK:
            return
        ack = self._await(cmd, self.ACK_TIMEOUT)
        if ack and ack[0] != 0:
            raise AmError(f"the mouse rejected {what or f'command {cmd}'} "
                          f"(status {ack[0]:#04x})")

    def _confirm(self, name, wanted, read):
        """Read back until the device reports `wanted`."""
        if self.dry_run:
            return wanted
        got = None
        for attempt in range(self.VERIFY_TRIES):
            if attempt or self.VERIFY_DELAY:
                time.sleep(self.VERIFY_DELAY)
            got = read()
            if got == wanted:
                return got
        raise NotConfirmed(name, wanted, got)

    # -- single-command settings ---------------------------------------------

    def get(self, name):
        f = FIELDS[name]
        p = self._read(f.get, f.get_args)
        return None if p is None else f.decode(p)

    def _need(self, name, label=None):
        """The current value, which must exist before it is overwritten."""
        if self.dry_run:
            return None
        v = self.get(name)
        if v is None:
            raise NotInFirmware(f"{label or name}: not supported by this "
                                f"firmware")
        return v

    def set_verified(self, name, value, label=None):
        f = FIELDS[name]
        if f.set is None:
            raise AmError(f"{label or name} is read-only")
        self._write(f.set, f.encode(value), what=label or name)
        return self._confirm(label or name, value, lambda: self.get(name))

    # -- basics --------------------------------------------------------------

    def battery(self):
        p = self.request(CMD["getBattery"])
        return codec.parse_battery(p) if p else None

    def hub_battery(self):
        """Spare battery in the dongle, when one is seated."""
        if not self.has_dongle:
            return None
        p = self.request(CMD["getDongleBattery"], device_id=DEV_LOCAL)
        b = codec.parse_battery(p) if p else None
        return b if b and b["present"] else None

    def profile(self):
        p = self._read(CMD["getProfile"])
        return None if not p or len(p) < 2 or p[0] != 0 else p[1]

    def set_profile(self, index):
        _int_in("profile", index, 0, self.model.profiles - 1)
        data = bytes([index])
        if self.model.profile_to_dongle and self.has_dongle:
            # The .100R2's receiver keeps its own copy of the active layer.
            # It does not answer a read of it, so this side is never
            # confirmed; the mouse's own answer below is what counts.
            self._send(CMD["changeProfile"], data, DEV_LOCAL)
            if not self.dry_run:
                self._await(CMD["changeProfile"], self.ACK_TIMEOUT)
        self._write(CMD["changeProfile"], data, what="the profile switch")
        return self._confirm("profile", index, self.profile)

    # -- sections ------------------------------------------------------------

    def read(self, section):
        if section == "sensor":
            return self._read_sensor()
        if section == "power":
            return self._read_power()
        if section == "dpi":
            return self._read_dpi()
        if section == "buttons":
            return self._read_buttons()
        if section == "light":
            return self._read_light()
        if section == "dongle_light":
            return self._read_dongle_light()
        raise AmError(f"unknown section '{section}'")

    def write(self, section, changes, strict=True):
        if section == "sensor":
            return self._write_sensor(dict(changes), strict)
        if section == "power":
            return self._write_power(dict(changes), strict)
        if section == "dpi":
            return self._write_dpi(dict(changes), strict)
        if section == "buttons":
            return self._write_buttons(dict(changes))
        if section == "light":
            return self._write_light(dict(changes))
        if section == "dongle_light":
            return self._write_dongle_light(dict(changes))
        raise AmError(f"unknown section '{section}'")

    @staticmethod
    def _check_keys(section, changes, allowed):
        for key in changes:
            if key not in allowed:
                raise AmError(f"{section}: unknown setting '{key}'")

    # sensor ...............................................................

    def _read_sensor(self):
        out = {}
        for key in ("polling_rate", "lod") + SENSOR_FLAGS:
            v = self.get(key)
            if v is not None:
                out[key] = v
        rot = self.get("rotate")
        if rot is not None:
            out["angle_enabled"], out["angle"] = rot
        return out

    def _write_sensor(self, changes, strict):
        self._check_keys("sensor", changes, SENSOR_KEYS)
        done = {}
        if "polling_rate" in changes:
            hz = changes["polling_rate"]
            if hz not in VALID_RATES:
                raise AmError(f"polling rate must be one of {VALID_RATES}")
        if "lod" in changes:
            high = self.model.lod_max if strict else 255
            _int_in("lod", changes["lod"], 0, high)
        for key in SENSOR_FLAGS + ("angle_enabled",):
            if key in changes:
                _flag_in(key, changes[key])
        if "angle" in changes:
            _int_in("angle", changes["angle"], -90, 90)

        for key in ("polling_rate", "lod") + SENSOR_FLAGS:
            if key in changes:
                self._need(key)
                done[key] = self.set_verified(key, changes[key])
        if "angle" in changes or "angle_enabled" in changes:
            cur = self._need("rotate", "sensor angle") or (False, 0)
            want = (changes.get("angle_enabled", cur[0]),
                    changes.get("angle", cur[1]))
            got = self.set_verified("rotate", want, "sensor angle")
            if "angle_enabled" in changes:
                done["angle_enabled"] = got[0]
            if "angle" in changes:
                done["angle"] = got[1]
        return done

    # power ................................................................

    def _sleep_by_nv(self):
        return self.model.sleep_nv_on_cable and not self.has_dongle

    def _read_sleep(self):
        if self._sleep_by_nv():
            rec = self._nv(codec.NV_SLEEP_R2)
            return None if rec is None else FIELDS["sleep"].decode(rec)
        return self.get("sleep")

    def _nv(self, nv_id):
        p = self._read(CMD["readNvData"], codec.read_nv(nv_id))
        return None if p is None else codec.parse_nv(p, nv_id)

    def _read_power(self):
        out = {}
        sleep = self._read_sleep()
        if sleep is not None:
            out["sleep_24g"], out["sleep_bt"] = (_sleep_out(v) for v in sleep)
        deb = self.get("debounce")
        if deb is not None:
            out["debounce_wired"], out["debounce_24g"], out["debounce_bt"] = deb
        if self.model.hw_debounce:
            for key in ("hw_debounce_press", "hw_debounce_release"):
                v = self.get(key)
                if v is not None:
                    out[key] = v
        return out

    def _write_power(self, changes, strict):
        self._check_keys("power", changes, POWER_KEYS)
        for key in ("hw_debounce_press", "hw_debounce_release"):
            if key in changes:
                raise AmError(f"{key} is read-only: the configurator never "
                              f"writes it")
        sleep_keys = [k for k in ("sleep_24g", "sleep_bt") if k in changes]
        deb_keys = [k for k in ("debounce_wired", "debounce_24g",
                                "debounce_bt") if k in changes]
        new_sleep = {k: _sleep_in(changes[k], strict) for k in sleep_keys}
        for k in deb_keys:
            _int_in(k, changes[k], 0, 255)
        done = {}

        if sleep_keys:
            if self.dry_run:
                cur = (180, 180)
            else:
                cur = self._read_sleep()
                if cur is None:
                    raise NotInFirmware("sleep time: not supported by this "
                                        "firmware")
            want = (new_sleep.get("sleep_24g", cur[0]),
                    new_sleep.get("sleep_bt", cur[1]))
            if self._sleep_by_nv():
                rec = self._nv(codec.NV_SLEEP_R2) if not self.dry_run else bytes(16)
                self._write(CMD["writeNvData"], codec.sleep_record_r2(
                    rec, new_sleep.get("sleep_24g"), new_sleep.get("sleep_bt")),
                    what="the sleep time")
            else:
                self._write(CMD["setSleepTime"], FIELDS["sleep"].encode(want),
                            what="the sleep time")
            got = self._confirm("sleep time", want, self._read_sleep)
            for i, k in enumerate(("sleep_24g", "sleep_bt")):
                if k in new_sleep:
                    done[k] = _sleep_out(got[i])

        if deb_keys:
            cur = self._need("debounce", "debounce") or (0, 0, 0)
            names = ("debounce_wired", "debounce_24g", "debounce_bt")
            want = tuple(changes.get(k, cur[i]) for i, k in enumerate(names))
            got = self.set_verified("debounce", want, "debounce")
            for i, k in enumerate(names):
                if k in changes:
                    done[k] = got[i]
        return done

    # dpi ..................................................................

    def _dpi_raw(self):
        p = self._read(CMD["getDpi"])
        return codec.parse_dpi(p) if p else None

    def _dpi_colour(self, stage):
        rec = self._nv(codec.NV_DPI_COLOUR_BASE + stage)
        if not rec or len(rec) < 7:
            return None
        return rgb_to_hex(rec[4], rec[5], rec[6])

    def _read_dpi(self):
        d = self._dpi_raw()
        if d is None:
            return None
        out = {"stages": [{"x": x, "y": y, "colour": self._dpi_colour(i)}
                          for i, (x, y) in enumerate(zip(d["x"], d["y"]))],
               "count": d["count"], "active": d["active"]}
        button = self.get("dpi_button")
        if button is not None:
            out["button"] = button
        return out

    def _dpi_value(self, axis, v, strict):
        if isinstance(v, bool) or not isinstance(v, int):
            raise AmError(f"DPI {axis} must be a number, not {v!r}")
        if strict:
            if not DPI_MIN <= v <= self.model.dpi_max or v % DPI_STEP:
                raise AmError(f"DPI {axis} must be {DPI_MIN}-"
                              f"{self.model.dpi_max} in steps of {DPI_STEP}")
        elif not 1 <= v <= U16_MAX:
            raise AmError(f"DPI {axis} must be 1-{U16_MAX}")
        return v

    def _write_dpi(self, changes, strict):
        self._check_keys("dpi", changes, ("stages", "count", "active", "button"))
        n_stages = codec.DPI_STAGES
        stages = {}
        for key, ch in (changes.get("stages") or {}).items():
            try:
                i = int(key)
            except (TypeError, ValueError):
                i = -1
            if not 0 <= i < n_stages:
                raise AmError(f"DPI stage must be 1-{n_stages}")
            self._check_keys(f"dpi stage {i + 1}", ch, ("x", "y", "colour"))
            st = {a: self._dpi_value(a, ch[a], strict) for a in ("x", "y")
                  if a in ch}
            if ch.get("colour") is not None:
                st["rgb"] = hex_to_rgb(ch["colour"])
            if st:
                stages[i] = st
        if "count" in changes:
            _int_in("DPI stage count", changes["count"], 1, n_stages)
        if "button" in changes:
            _flag_in("DPI button", changes["button"])

        cur = None
        if not self.dry_run:
            cur = self._dpi_raw()
            if cur is None:
                raise AmError("could not read the DPI settings -- nothing "
                              "written")
        count = changes.get("count", cur["count"] if cur else n_stages)
        if "active" in changes:
            _int_in("active DPI stage", changes["active"], 0, count - 1)
            active = changes["active"]
        else:
            active = min(cur["active"], count - 1) if cur else 0

        # Values first, the way the configurator orders it.
        done_stages, wanted_xy, wanted_colour = {}, {}, {}
        for i, st in sorted(stages.items()):
            if "x" in st or "y" in st:
                x = st.get("x", cur["x"][i] if cur and "y" in st else st.get("y"))
                y = st.get("y", x)
                self._write(CMD["setDpi"], codec.set_dpi_stage(i, x, y),
                            what=f"DPI stage {i + 1} ({x}, {y})")
                wanted_xy[i] = (x, y)
                if cur and i == cur["active"] and "count" not in changes \
                        and "active" not in changes:
                    # The sensor keeps using the old value until the stage
                    # is selected again.
                    self._write(CMD["setCurrentDpiStage"], bytes([i]),
                                what="the active DPI stage")
            if "rgb" in st:
                self._write(CMD["saveRgb"], codec.save_dpi_colour(i, st["rgb"]),
                            what=f"the colour of DPI stage {i + 1}")
                wanted_colour[i] = rgb_to_hex(*st["rgb"])
        if "count" in changes:
            self._write(CMD["setDpiLoopRange"], bytes([count]),
                        what="the DPI stage count")
        if "count" in changes or "active" in changes:
            self._write(CMD["setCurrentDpiStage"], bytes([active]),
                        what="the active DPI stage")

        done = {}
        if wanted_xy or "count" in changes or "active" in changes:
            def state():
                d = self._dpi_raw()
                if d is None:
                    return None
                return ({i: (d["x"][i], d["y"][i]) for i in wanted_xy},
                        d["count"] if "count" in changes else None,
                        d["active"] if "count" in changes or "active" in changes
                        else None)
            self._confirm("DPI", (wanted_xy,
                                  count if "count" in changes else None,
                                  active if "count" in changes or "active" in changes
                                  else None), state)
            for i, (x, y) in wanted_xy.items():
                done_stages.setdefault(i, {}).update(x=x, y=y)
            if "count" in changes:
                done["count"] = count
            if "active" in changes or ("count" in changes and cur
                                       and active != cur["active"]):
                done["active"] = active
        for i, colour in wanted_colour.items():
            self._confirm(f"colour of DPI stage {i + 1}", colour,
                          lambda i=i: self._dpi_colour(i))
            done_stages.setdefault(i, {})["colour"] = colour
        if done_stages:
            done["stages"] = done_stages
        if "button" in changes:
            self._need("dpi_button", "DPI button")
            done["button"] = self.set_verified("dpi_button", changes["button"],
                                               "DPI button")
        return done

    # buttons ..............................................................

    def _button_action(self, button):
        """What a button does now, or None when the firmware has no such
        button (older .100R2 firmware has no key 8)."""
        key_id = self.model.buttons.index(button) + 1
        p = self._read(CMD["getKeyRemap"], bytes([key_id]))
        got = codec.parse_key(p) if p else None
        if not got or got[0] != key_id:
            return None
        return codec.decode_action(got[1], got[2])

    def _button_actions(self):
        out = {}
        for button in self.model.buttons:
            action = self._button_action(button)
            if action is not None:
                out[button] = action
        return out

    def _read_buttons(self):
        return {b: format_action(a) for b, a in self._button_actions().items()}

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
            if action.kind == "opaque":
                raise AmError(f"{button}: a macro or other opaque binding "
                              f"cannot be written")
            if action.kind == "profile-switch" and not self.model.layer_switch:
                raise Unsupported(f"the {self.model.name} has no "
                                  f"profile-switch action")
            wanted[button] = action

        if self.dry_run:
            current = {b: self.model.default_action(b)
                       for b in self.model.buttons}
        else:
            current = self._button_actions()
            for button in wanted:
                if button not in current:
                    raise NotInFirmware(f"the '{button}' button is not "
                                        f"supported by this firmware")
        check_left_click({**current, **wanted})

        done = {}
        for button, action in wanted.items():
            key_id = self.model.buttons.index(button) + 1
            kind, params = codec.encode_action(action)
            self._write(CMD["setKeyRemap"], bytes([key_id, kind]) + params,
                        what=f"the {button} mapping")
            self._confirm(f"{button} button", action,
                          lambda b=button: self._button_action(b))
            done[button] = format_action(action)
        return done

    # mouse light ............................................................

    def _mouse_light_raw(self):
        """{on, effect (number), speed} from the light-settings record."""
        d = self._nv(codec.NV_LIGHT_SETTINGS)
        if not d or len(d) < 3:
            return None
        return {"on": bool(d[0]), "effect": d[1], "speed": d[2]}

    def _mouse_colour(self):
        d = self._nv(codec.NV_LIGHT_COLOURS)
        if not d or len(d) < 7:
            return None
        return rgb_to_hex(d[4], d[5], d[6])

    def _read_light(self):
        raw = self._mouse_light_raw()
        if raw is None:
            return None
        out = {"on": raw["on"],
               "effect": MOUSE_EFFECTS.get(raw["effect"], raw["effect"]),
               "speed": raw["speed"]}
        colour = self._mouse_colour()
        if colour:
            out["colour"] = colour
        return out

    def _light_settings_bytes(self, cfg):
        return bytes([1 if cfg["on"] else 0, cfg["effect"],
                      max(1, min(10, cfg["speed"]))])

    def _write_light(self, changes):
        self._check_keys("light", changes, ("on", "effect", "speed", "colour"))
        if "on" in changes:
            _flag_in("light on", changes["on"])
        if "speed" in changes:
            _int_in("light speed", changes["speed"], 1, 10)
        if "effect" in changes:
            names = {v: k for k, v in MOUSE_EFFECTS.items()}
            if changes["effect"] not in names:
                raise AmError(f"mouse light effect must be one of "
                              f"{', '.join(names)}")
        rgb = hex_to_rgb(changes["colour"]) if "colour" in changes else None
        if self.dry_run:
            cfg = dict(MOUSE_LIGHT_FALLBACK)
        else:
            cfg = self._mouse_light_raw()
            if cfg is None:
                raise AmError("no reply from the mouse light -- nothing "
                              "written")
        done = {}
        if rgb is not None:
            # The configurator's own three-step sequence for a solid colour.
            self._write(CMD["saveRgb"],
                        codec.u16(codec.NV_LIGHT_COLOURS) + bytes([RGB_LED_COUNT])
                        + codec.u16(3) + bytes([RGB_FRAME_INTERVAL, *rgb]),
                        what="the mouse light colour")
            if not self.dry_run:
                time.sleep(self.VERIFY_DELAY)
            self._write(CMD["setRgbRealtime"],
                        bytes([RGB_REALTIME_FLAG, 0, RGB_FRAME_INTERVAL, 1, *rgb]))
        wanted = dict(cfg)
        for key in ("on", "speed"):
            if key in changes:
                wanted[key] = changes[key]
        if "effect" in changes:
            wanted["effect"] = {v: k for k, v in MOUSE_EFFECTS.items()}[changes["effect"]]
        if rgb is not None or wanted != cfg or self.dry_run:
            data = self._light_settings_bytes(wanted)
            self._write(CMD["setLightSettings"], data)
            sent = {"on": bool(data[0]), "effect": data[1], "speed": data[2]}
            if any(k in changes for k in ("on", "effect", "speed")):
                self._confirm("mouse light", sent, self._mouse_light_raw)
        for key in ("on", "effect", "speed"):
            if key in changes:
                done[key] = changes[key]
        if rgb is not None:
            want = rgb_to_hex(*rgb)
            done["colour"] = self._confirm("mouse light colour", want,
                                           self._mouse_colour)
        return done

    # dongle light ...........................................................

    def _dongle_light_raw(self):
        """The 13-byte block as 2.2.0 reported it (effect as a number)."""
        if not self.has_dongle or self.dry_run:
            return None
        p = self.request(CMD["getDongleLight"], device_id=DEV_LOCAL)
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

    @staticmethod
    def _dongle_light_bytes(cfg):
        h1, h2, h3 = cfg["hue1"], cfg["hue2"], cfg["hue3"]
        return bytes([
            1 if cfg["on"] else 0, cfg["effect"],
            (h1 >> 8) & 0xFF, h1 & 0xFF, max(1, min(100, cfg["sat1"])),
            (h2 >> 8) & 0xFF, h2 & 0xFF, max(1, min(100, cfg["sat2"])),
            (h3 >> 8) & 0xFF, h3 & 0xFF, max(1, min(100, cfg["sat3"])),
            max(1, min(20, cfg["speed"])) + 80,
            max(1, min(255, cfg["brightness"])),
        ])

    def _read_dongle_light(self):
        raw = self._dongle_light_raw()
        if raw is None:
            return None
        names = {v: k for k, v in DONGLE_EFFECTS.items()}
        return dict(raw, effect=names.get(raw["effect"], raw["effect"]),
                    colour=hue_to_hex(raw["hue1"]))

    def _write_dongle_light(self, changes):
        if not self.has_dongle:
            raise AmError("dongle lighting needs the 2.4G dongle connected")
        allowed = ("on", "effect", "colour", "speed", "brightness",
                   "hue1", "sat1", "hue2", "sat2", "hue3", "sat3")
        self._check_keys("dongle_light", changes, allowed)
        raw = dict(changes)
        if "on" in raw:
            _flag_in("light on", raw["on"])
        if "effect" in raw and not isinstance(raw["effect"], int):
            if raw["effect"] not in DONGLE_EFFECTS:
                raise AmError(f"dongle light effect must be one of "
                              f"{', '.join(DONGLE_EFFECTS)}")
            raw["effect"] = DONGLE_EFFECTS[raw["effect"]]
        if "colour" in raw:
            raw["hue1"], raw["sat1"] = hex_to_hue_sat(raw.pop("colour"))
        for key in raw:
            if key not in ("on", "effect"):
                _int_in(key, raw[key], 0, 1530 if key.startswith("hue") else 255)
        if self.dry_run:
            cfg = dict(DONGLE_LIGHT_FALLBACK)
        else:
            cfg = self._dongle_light_raw()
            if cfg is None:
                raise AmError("no reply from the dongle light -- nothing "
                              "written")
        cfg.update(raw)
        data = self._dongle_light_bytes(cfg)
        self._write(CMD["setDongleLight"], data, device_id=DEV_LOCAL)
        def read_back():
            now = self._dongle_light_raw()
            return None if now is None else self._dongle_light_bytes(now)
        self._confirm("dongle light", data, read_back)
        done = {k: changes[k] for k in changes}
        if "colour" in done:
            done["colour"] = hue_to_hex(cfg["hue1"])
        return done

    # -- 2.2.0 method names --------------------------------------------------
    #
    # What the command line called before sections existed. Kept so the
    # existing commands keep working unchanged; Task 7 moves the command line
    # onto read()/write() and removes these.

    REMAP_KEY_ID = {"forward": 4, "back": 5}
    REMAP_KEYBOARD = 6
    _FLAG_FIELDS = {"setMotionSync": "motion_sync",
                    "setAngleSnapping": "angle_snap",
                    "setRippleControl": "ripple", "setFpsMode": "fps_mode"}

    def polling_rate(self):
        return self.get("polling_rate")

    def mouse_light(self):
        return self._mouse_light_raw()

    def mouse_colour(self):
        return self._mouse_colour()

    def dongle_light(self):
        return self._dongle_light_raw()

    def set_dongle_light(self, **changes):
        return self.write("dongle_light", changes)

    def set_mouse_light(self, **changes):
        return self.write("light", changes)

    def set_mouse_colour(self, colour):
        return self.write("light", {"colour": colour})

    def set_polling_rate(self, hz):
        return self.write("sensor", {"polling_rate": hz})

    def set_flag(self, key, value):
        return self.write("sensor", {self._FLAG_FIELDS[key]: bool(value)})

    def set_lod(self, value):
        return self.write("sensor", {"lod": value})

    def send(self, cmd_id, data=(), device_id=None):
        """`aminf raw`: one read command, never a write."""
        if cmd_id not in codec.READS:
            codec.frame(DEV_LOCAL, cmd_id)        # raises Refused if unknown
            raise Refused(f"refusing command {cmd_id}: raw only sends reads")
        return self.request(cmd_id, bytes(data), device_id)

    def key_remap(self, button):
        """(type, params) for a side button, or None."""
        key_id = self.REMAP_KEY_ID[button]
        p = self._read(CMD["getKeyRemap"], bytes([key_id]))
        got = codec.parse_key(p) if p else None
        if not got or got[0] != key_id:
            return None
        return got[1], list(got[2])

    def remap_label(self, button):
        if self.dry_run:
            return "unknown (dry run)"
        got = self.key_remap(button)
        if not got:
            return "no reply"
        kind, params = got
        if kind == self.REMAP_KEY_ID[button]:
            return f"{button} (default)"
        if kind == self.REMAP_KEYBOARD and len(params) >= 4:
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
        key_id = self.REMAP_KEY_ID[button]
        data = ([key_id, key_id] if usage is None
                else [key_id, self.REMAP_KEYBOARD, 0, 0, usage, 0])
        self._write(CMD["setKeyRemap"], bytes(data), what=f"the {button} mapping")
        if self.dry_run:
            return
        want = data[2:]

        def matches():
            got = self.key_remap(button)
            return bool(got and got[0] == data[1] and
                        (got[1] + [0] * len(want))[:len(want)] == want)
        try:
            self._confirm(button, True, matches)
        except NotConfirmed:
            raise AmError(f"{button}: the mouse didn't confirm the new "
                          f"mapping") from None
