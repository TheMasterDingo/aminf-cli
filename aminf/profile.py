"""Export and import of a mouse's active profile.

A profile file is the six sections of aminf.device.SECTIONS as plain JSON.
Importing compares the file with what the mouse reports and writes only
what differs, section by section, each write confirmed by read-back.

The web configurator's own export for the .97 (and .100R2) is accepted too
and converted on load.
"""
from __future__ import annotations

import json

from aminf.actions import BUTTON_NAMES, format_action, parse_action
from aminf.colour import hex_to_hue_sat, hex_to_rgb, rgb_to_hex
from aminf.device import SECTIONS
from aminf.errors import AmError, MouseOffline
from aminf.models import DPI_MIN, DPI_STEP

FORMAT, VERSION = "aminf-profile", 1

# What --only accepts, and the sections each name covers.
GROUPS = {"sensor": ("sensor",), "power": ("power",), "dpi": ("dpi",),
          "buttons": ("buttons",), "light": ("light", "dongle_light")}

RATES = (125, 250, 500, 1000, 2000, 4000, 8000)
U16 = 0xFFFF
READ_ONLY = ("hw_debounce_press", "hw_debounce_release")

# key -> ("bool" | "str" | "colour" | "sleep" | (low, high))
SCHEMA = {
    "sensor": {"polling_rate": "rate", "lod": (0, 255), "motion_sync": "bool",
               "angle_snap": "bool", "ripple": "bool", "fps_mode": "bool",
               "angle": (-90, 90), "angle_enabled": "bool",
               "profile_key": "bool"},
    "power": {"sleep_24g": "sleep", "sleep_bt": "sleep",
              "deep_sleep_24g": "sleep", "deep_sleep_bt": "sleep",
              "debounce_wired": (0, 255), "debounce_24g": (0, 255),
              "debounce_bt": (0, 255), "hw_debounce_press": (0, 255),
              "hw_debounce_release": (0, 255)},
    "light": {"on": "bool", "effect": "str", "speed": (0, 255),
              "colour": "colour", "colour_charged": "colour"},
    "dongle_light": {"on": "bool", "effect": "effect", "colour": "colour",
                     "hue1": (0, 1530), "sat1": (0, 255), "hue2": (0, 1530),
                     "sat2": (0, 255), "hue3": (0, 1530), "sat3": (0, 255),
                     "speed": (0, 255), "brightness": (0, 255),
                     "preset": "bool"},
}


# ---------------------------------------------------------------- snapshot

def snapshot(mouse):
    """Everything aminf can set on the active profile, as a JSON-able dict."""
    sections = {}
    for name in SECTIONS:
        data = mouse.read(name)
        if data is not None:
            sections[name] = data
    return {"format": FORMAT, "version": VERSION, "model": mouse.model.name,
            "profile": mouse.profile(), "sections": sections}


# ---------------------------------------------------------------- validate

def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _check(path, v, rule):
    if rule == "bool":
        ok = isinstance(v, bool)
        want = "true or false"
    elif rule == "str":
        ok, want = isinstance(v, str), "a name"
    elif rule == "effect":
        ok, want = isinstance(v, str) or _is_int(v), "an effect name"
    elif rule == "colour":
        try:
            ok = isinstance(v, str) and bool(hex_to_rgb(v))
        except ValueError:
            ok = False
        want = "a colour like #FF823C"
    elif rule == "sleep":
        ok = v == "never" or (_is_int(v) and 0 <= v < U16)
        want = "a number of seconds or \"never\""
    elif rule == "rate":
        ok, want = v in RATES and _is_int(v), f"one of {RATES}"
    else:
        low, high = rule
        ok, want = _is_int(v) and low <= v <= high, f"{low}-{high}"
    if not ok:
        raise AmError(f"{path}: {v!r} is not valid, expected {want}")


def validate(doc, model, only=None):
    """Raise AmError, naming the offending key, unless `doc` is a profile
    this model could take. Returns warnings for values that are valid for
    the protocol but outside the configurator's range for this model."""
    if not isinstance(doc, dict) or not isinstance(doc.get("sections"), dict):
        raise AmError("not an aminf profile file")
    if doc.get("format") != FORMAT:
        raise AmError("not an aminf profile file")
    if doc.get("version") != VERSION:
        raise AmError(f"profile file version {doc.get('version')!r} is not "
                      f"supported (this aminf reads version {VERSION})")
    for name in only or ():
        if name not in GROUPS:
            raise AmError(f"--only: unknown section '{name}' -- use "
                          f"{', '.join(GROUPS)}")
    if doc.get("model") not in (None, model.name) and not only:
        raise AmError(f"this file is for the {doc['model']}, not the "
                      f"{model.name}. Use --only to import the sections "
                      f"both mice share ({', '.join(GROUPS)}).")
    warnings = []
    for name, sec in doc["sections"].items():
        if name not in SECTIONS:
            raise AmError(f"sections.{name}: unknown section")
        if not isinstance(sec, dict):
            raise AmError(f"sections.{name}: must be an object")
        path = f"sections.{name}"
        if name == "dpi":
            _validate_dpi(path, sec, model, warnings)
        elif name == "buttons":
            for button, text in sec.items():
                if button not in BUTTON_NAMES:
                    raise AmError(f"{path}.{button}: unknown button")
                if not isinstance(text, str):
                    raise AmError(f"{path}.{button}: must be an action name")
                try:
                    parse_action(text)
                except AmError as exc:
                    raise AmError(f"{path}.{button}: {exc}") from None
        else:
            for key, v in sec.items():
                if key not in SCHEMA[name]:
                    raise AmError(f"{path}.{key}: unknown setting")
                _check(f"{path}.{key}", v, SCHEMA[name][key])
            if name == "sensor" and _is_int(sec.get("lod")) \
                    and sec["lod"] > model.lod_max:
                warnings.append(f"lod {sec['lod']} is above the "
                                f"{model.name}'s range (0-{model.lod_max})")
    return warnings


def _validate_dpi(path, sec, model, warnings):
    for key in sec:
        if key not in ("stages", "count", "active", "button"):
            raise AmError(f"{path}.{key}: unknown setting")
    stages = sec.get("stages", [])
    if not isinstance(stages, list) or len(stages) > 8:
        raise AmError(f"{path}.stages: must be a list of up to 8 stages")
    for i, st in enumerate(stages):
        if not isinstance(st, dict):
            raise AmError(f"{path}.stages[{i}]: must be an object")
        for key, v in st.items():
            here = f"{path}.stages[{i}].{key}"
            if key in ("x", "y"):
                _check(here, v, (1, U16))
                if not DPI_MIN <= v <= model.dpi_max or v % DPI_STEP:
                    warnings.append(
                        f"DPI stage {i + 1} {key} {v} is outside the "
                        f"{model.name}'s range ({DPI_MIN}-{model.dpi_max} in "
                        f"steps of {DPI_STEP})")
            elif key == "colour":
                if v is not None:
                    _check(here, v, "colour")
            else:
                raise AmError(f"{here}: unknown setting")
    if "count" in sec:
        _check(f"{path}.count", sec["count"], (1, 8))
    if "active" in sec:
        _check(f"{path}.active", sec["active"], (0, 7))
    if "button" in sec:
        _check(f"{path}.button", sec["button"], "bool")


# -------------------------------------------------------------------- diff

def _same_colour(a, b):
    return isinstance(a, str) and isinstance(b, str) and a.upper() == b.upper()


def diff(current, wanted, section):
    """The changes that turn `current` into `wanted`, in the shape
    Mouse.write() takes. Read-only values, bindings aminf never writes and
    settings the mouse does not report are left out."""
    if section == "dpi":
        out = {}
        stages = {}
        cur_stages = current.get("stages", [])
        for i, st in enumerate(wanted.get("stages", [])):
            cur = cur_stages[i] if i < len(cur_stages) else {}
            ch = {}
            if st.get("x", cur.get("x")) != cur.get("x") or \
                    st.get("y", cur.get("y")) != cur.get("y"):
                ch["x"] = st.get("x", cur.get("x"))
                ch["y"] = st.get("y", cur.get("y"))
            if st.get("colour") and not _same_colour(st["colour"],
                                                     cur.get("colour")):
                ch["colour"] = st["colour"]
            if ch:
                stages[i] = ch
        if stages:
            out["stages"] = stages
        for key in ("count", "active", "button"):
            if key in wanted and key in current and wanted[key] != current[key]:
                out[key] = wanted[key]
        return out
    if section == "buttons":
        out = {}
        for button, text in wanted.items():
            if button not in current:
                continue                     # this mouse has no such button
            action = parse_action(text)
            if action is None or action.kind == "opaque":
                continue
            if format_action(action) != current[button]:
                out[button] = format_action(action)
        return out
    out = {}
    for key, v in wanted.items():
        if key in READ_ONLY or key not in current:
            continue
        if key in ("colour", "colour_charged"):
            if section == "dongle_light" and "hue1" in wanted:
                continue                     # the hue and saturation say it exactly
            if not _same_colour(v, current[key]):
                out[key] = v
        elif v != current[key]:
            out[key] = v
    if section == "dongle_light":
        out.pop("preset", None)
    return out


def _describe(changes):
    def flat(d, prefix=""):
        for k, v in d.items():
            if isinstance(v, dict):
                yield from flat(v, f"{prefix}{k + 1 if isinstance(k, int) else k}.")
            else:
                yield f"{prefix}{k}={v}"
    return ", ".join(flat(changes))


# ------------------------------------------------------------------- apply

def apply(mouse, doc, only=None, dry_run=False):
    """Make the mouse's active profile match `doc`.

    Returns [(section, status, detail)] with status one of "applied",
    "unchanged", "would change" (dry run), "skipped" and "failed". The whole
    file is validated before the first write. Sections are independent: one
    that fails is reported and the rest are still applied, so a restore
    never stops half-way over one refused value. Only a mouse that stops
    answering ends the import; what was left is reported as skipped.
    """
    validate(doc, mouse.model, only)
    wanted_sections = [s for s in SECTIONS
                       if not only or any(s in GROUPS[g] for g in only)]
    results, gone = [], False
    for name in wanted_sections:
        wanted = doc["sections"].get(name)
        if wanted is None:
            continue
        if gone:
            results.append((name, "skipped", "the mouse stopped answering"))
            continue
        try:
            current = mouse.read(name)
            if current is None:
                results.append((name, "skipped", "not available on this "
                                "mouse or connection"))
                continue
            changes = diff(current, wanted, name)
            if not changes:
                results.append((name, "unchanged", ""))
            elif dry_run:
                results.append((name, "would change", _describe(changes)))
            else:
                mouse.write(name, changes, strict=False)
                results.append((name, "applied", _describe(changes)))
        except AmError as exc:
            gone = isinstance(exc, MouseOffline) or mouse.offline
            results.append((name, "failed", str(exc)))
    return results


# -------------------------------------------------------------------- load

def load(path):
    """A profile document from a file: aminf's own format, or the web
    configurator's .97 export converted to it."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise AmError(f"cannot read {path}: {exc.strerror or exc}") from None
    except ValueError as exc:
        raise AmError(f"{path} is not valid JSON ({exc})") from None
    if isinstance(data, dict) and data.get("format") == FORMAT:
        return data
    if isinstance(data, dict) and any(k in data for k in
                                      ("mouseArgs", "lightData")) \
            and "mouseDPI" not in data:
        return from_configurator_97(data)
    if isinstance(data, dict) and "mouseDPI" in data:
        raise AmError("this is a .100 configurator export, which aminf does "
                      "not import yet")
    raise AmError(f"{path} is not an aminf profile file")


def _num(v, default=None):
    """The configurator writes numbers as numbers, decimal strings or hex
    strings."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str) and v.strip():
        try:
            return int(v.strip(), 0)
        except ValueError:
            return default
    return default


def _flag(v, default=None):
    if isinstance(v, bool):
        return v
    n = _num(v)
    if n is not None:
        return n != 0
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("true", "on", "yes"):
            return True
        if t in ("false", "off", "no", ""):
            return False
    return default


def _colour(v):
    try:
        return rgb_to_hex(*hex_to_rgb(v))
    except (ValueError, AttributeError, TypeError):
        return None


def from_configurator_97(data):
    """Convert the web configurator's "AM Infinity .97 Profile.json" layout
    (KeyMatrix / mouseArgs / lightData / mouseMacroData). Macros and
    rapid-fire bindings are left out and listed in doc["notes"]."""
    from aminf.race import codec          # the file stores raw key-map types

    sections, notes = {}, []
    args = data.get("mouseArgs") if isinstance(data.get("mouseArgs"), dict) else None
    light = data.get("lightData") if isinstance(data.get("lightData"), dict) else None

    if args:
        sensor = {}
        for src, dst, conv in (
                ("reportRate", "polling_rate", _num), ("LOD", "lod", _num),
                ("MotionSync", "motion_sync", _flag),
                ("AngleSnapping", "angle_snap", _flag),
                ("RippleCtrl", "ripple", _flag),
                ("AM35FPStatus", "fps_mode", _flag),
                ("setAM35rotateAngle", "angle", _num),
                ("AM35rotateAngleSwitch", "angle_enabled", _flag),
                ("AM35ProfileKey", "profile_key", _flag)):
            if src in args and conv(args[src]) is not None:
                sensor[dst] = conv(args[src])
        if sensor:
            sections["sensor"] = sensor

        power = {}
        for src, dst in (("sleepTime24G", "sleep_24g"), ("sleepTimeBT", "sleep_bt")):
            n = _num(args.get(src))
            if n is not None:
                power[dst] = "never" if n == -1 else n
        for src, dst in (("wireDebounce", "debounce_wired"),
                         ("rf24gDebounce", "debounce_24g"),
                         ("btDebounce", "debounce_bt")):
            n = _num(args.get(src))
            if n is not None:
                power[dst] = n
        if power:
            sections["power"] = power

        xs = args.get("xDPI") if isinstance(args.get("xDPI"), list) else []
        ys = args.get("yDPI") if isinstance(args.get("yDPI"), list) else []
        cols = args.get("DPIcolor") if isinstance(args.get("DPIcolor"), list) else []
        if xs:
            stages = []
            for i, x in enumerate(xs[:8]):
                x = _num(x, 800)
                y = _num(ys[i], x) if i < len(ys) else x
                stages.append({"x": x, "y": y,
                               "colour": _colour(cols[i]) if i < len(cols) else None})
            dpi = {"stages": stages}
            count = _num(args.get("DPIStages"))
            if count is not None:
                dpi["count"] = max(1, min(8, count))
            active = _num(args.get("currentID"))
            if active is not None:
                dpi["active"] = max(0, min(dpi.get("count", 8) - 1, active))
            button = _flag(args.get("getDPIButton"))
            if button is not None:
                dpi["button"] = button
            sections["dpi"] = dpi

    keymap = data.get("KeyMatrix")
    if isinstance(keymap, dict):
        buttons = {}
        for key, entry in keymap.items():
            key_id = _num(key)
            if key_id is None or not 1 <= key_id <= len(BUTTON_NAMES) \
                    or not isinstance(entry, list) or not entry:
                continue
            button = BUTTON_NAMES[key_id - 1]
            kind = _num(entry[0], -1)
            if kind in (8, 9):
                notes.append(f"{button}: bound to a macro or rapid fire in "
                             f"the file -- left as it is on the mouse")
                continue
            if kind == 6:
                consumer = _num(entry[1] if len(entry) > 1 else 0, 0) or 0
                usage = _num(entry[2] if len(entry) > 2 else 0, 0) or 0
                bits_text = str(entry[3] if len(entry) > 3 else "0000")
                bits_text = "".join(c for c in bits_text if c in "01").ljust(4, "0")
                bits = sum(b for c, b in zip(bits_text, (1, 2, 4, 8)) if c == "1")
                params = bytes([consumer & 0xFF, consumer >> 8 & 0xFF,
                                usage & 0xFF, 0 if consumer else bits])
            elif kind == 11:
                lock = _num(entry[2] if len(entry) > 2 else 0, 0) or 0
                params = bytes([(_num(entry[1], 3) if len(entry) > 1 else 3) & 0xFF,
                                lock & 0xFF, lock >> 8 & 0xFF])
            else:
                params = b""
            action = codec.decode_action(kind & 0xFF, params)
            if action.kind == "opaque":
                notes.append(f"{button}: a binding aminf does not write -- "
                             f"left as it is on the mouse")
                continue
            buttons[button] = format_action(action)
        if buttons:
            sections["buttons"] = buttons

    if light:
        ml = {}
        if _flag(light.get("mouseLightSwitch")) is not None:
            ml["on"] = _flag(light["mouseLightSwitch"])
        effect = light.get("mouseLightType")
        if isinstance(effect, str) and effect.lower() in ("solid", "breathing", "neon"):
            ml["effect"] = effect.lower()
        elif _num(effect) in (0, 1, 2):
            ml["effect"] = ("solid", "breathing", "neon")[_num(effect)]
        if _num(light.get("mouseLightSpeed")) is not None:
            ml["speed"] = _num(light["mouseLightSpeed"])
        rgb = light.get("mouseLightRGB")
        if isinstance(rgb, list) and rgb and _colour(rgb[0]):
            ml["colour"] = _colour(rgb[0])
        if ml:
            sections["light"] = ml

        dl = {}
        on = _flag(light.get("dongleLightEnabled"))
        if on is None:
            on = _flag(light.get("dongleLightSwitch"))
        if on is not None:
            dl["on"] = on
        effect = _num(light.get("dongleLightType"))
        names = {1: "solid", 2: "breathing", 3: "arc", 4: "mosaic",
                 5: "aurora", 6: "night", 7: "flow"}
        if effect in names:
            dl["effect"] = names[effect]
        for n, (ckey, skey) in enumerate((("dongleColor", "dongleSaturationPer"),
                                          ("dongleColor2", "dongleSaturationPer2"),
                                          ("dongleColor3", "dongleSaturationPer3")), 1):
            colour = _colour(light.get(ckey))
            if colour:
                dl[f"hue{n}"] = hex_to_hue_sat(colour)[0]
            sat = _num(light.get(skey))
            if sat is not None:
                dl[f"sat{n}"] = sat
        for src, dst in (("dongleSpeed", "speed"), ("dongleBrightness", "brightness")):
            if _num(light.get(src)) is not None:
                dl[dst] = _num(light[src])
        if dl:
            sections["dongle_light"] = dl

    if not sections:
        raise AmError("the configurator file holds nothing aminf can import")
    profile = _num((args or {}).get("currentProfile"))
    return {"format": FORMAT, "version": VERSION, "model": None,
            "source": "web configurator (.97 format)", "profile": profile,
            "sections": sections, "notes": notes}
