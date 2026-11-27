"""Command line: parse what was typed, run it on each mouse, print the result.

Commands talk to a mouse only through the sections every model offers
(aminf.device.SECTIONS), so nothing here knows which protocol is in use.
The two exceptions are `status` and `light status`, whose output keeps the
exact shape 2.2.0 gave each model.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from aminf import __version__, profile
from aminf.actions import BUTTON_NAMES, format_action
from aminf.colour import hue_to_hex
from aminf.discover import open_mice
from aminf.errors import AmError, MouseOffline, Unsupported
from aminf.models import MODELS
from aminf.race.device import DONGLE_EFFECTS, MOUSE_EFFECTS, VALID_RATES
from aminf.transport import HidapiBackend

TOGGLES = {
    "motionsync": "motion_sync",
    "anglesnap": "angle_snap",
    "ripple": "ripple",
    "fpsmode": "fps_mode",
    "profilekey": "profile_key",
}
RADIOS = {"wired": "debounce_wired", "2.4g": "debounce_24g",
          "bt": "debounce_bt"}
SLEEP_KEYS = {"2.4g": "sleep_24g", "bt": "sleep_bt"}

# The configurator asks before enabling this; a command line can only say it.
FPS_WARNING = ("note: FPS (blast) mode raises computer resource usage and "
               "shortens mouse battery life")


def on_off(v):
    return "on" if v else "off"


def show_action(text):
    """An action string as shown to a person: function keys in capitals."""
    return re.sub(r"\bf(\d{1,2})\b", r"F\1", text)


def hex_arg(text):
    return f"#{text.lstrip('#').upper()}"


# ------------------------------------------------------------------ status

def quiet(read):
    """A value, or None when the mouse is asleep: status reports that once
    instead of failing."""
    try:
        return read()
    except MouseOffline:
        return None


def collect_status(m, full=False):
    s = m.status_2_2()
    if full:
        for section in ("sensor", "power", "dpi", "buttons"):
            s[section] = quiet(lambda: m.read(section))
    return s


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

    if "lod" not in s:
        print_status_100(s)
        print_full(s)
        return

    if s.get("mouse_offline"):
        print("mouse          off or out of range (dongle values only)")
    else:
        print(f"lift-off dist  {s['lod']}")
        print(f"sensor         motion-sync={s['motion_sync']}  "
              f"angle-snap={s['angle_snapping']}  ripple={s['ripple_control']}")
    ml = s["mouse_light"]
    if ml:
        print(f"mouse light    {on_off(ml['on'])}  "
              f"{MOUSE_EFFECTS.get(ml['effect'], ml['effect'])}  "
              f"{s['mouse_colour'] or '--'}")
    dl = s["dongle_light"]
    if dl:
        name = next((k for k, v in DONGLE_EFFECTS.items()
                     if v == dl["effect"]), dl["effect"])
        print(f"dongle light   {on_off(dl['on'])}  {name}  "
              f"{hue_to_hex(dl['hue1'])} sat={dl['sat1']} "
              f"bright={dl['brightness']} speed={dl['speed']}")
    print_full(s)


def print_status_100(s):
    """The .100's lines, as 2.2.0 printed them."""
    if s.get("mouse_offline"):
        print("mouse          off or out of range (dongle values only)")
    ml = s["mouse_light"]
    if ml:
        extra = (f" / charged {ml['colour_charged']}"
                 if "colour_charged" in ml else "")
        print(f"mouse light    {on_off(ml['on'])}  {ml['colour']}{extra}")
    dl = s["dongle_light"]
    if dl:
        line = f"dongle light   {on_off(dl['on'])}"
        if "colour" in dl:
            line += (f"  {dl['effect_name']}  {dl['colour']}"
                     f"{' (preset)' if dl['preset'] else ''}"
                     f"  bright={dl['brightness']}/4")
        print(line)


def print_full(s):
    """The extra lines of `status --full`."""
    dpi = s.get("dpi")
    if dpi:
        stage = dpi["stages"][dpi["active"]]
        xy = f"{stage['x']}" + (f" x {stage['y']}" if stage["y"] != stage["x"] else "")
        print(f"dpi            {xy}  (stage {dpi['active'] + 1} of {dpi['count']}"
              f"{', button ' + on_off(dpi['button']) if 'button' in dpi else ''})")
    power = s.get("power")
    if power:
        print(f"sleep          {sleep_line(power)}")
        print(f"debounce       {debounce_line(power)}")
    sensor = s.get("sensor")
    if sensor and "angle" in sensor:
        print(f"sensor angle   {angle_text(sensor)}")
    if sensor and "fps_mode" in sensor:
        print(f"fps mode       {on_off(sensor['fps_mode'])}")
    buttons = s.get("buttons")
    if buttons:
        model = next(m for m in MODELS.values() if m.name == s["model"])
        changed = [f"{b} -> {show_action(a)}" for b, a in buttons.items()
                   if a != format_action(model.default_action(b))]
        print(f"buttons        {', '.join(changed) if changed else 'all default'}")


def seconds(v):
    return "never" if v == "never" else f"{v} s"


def sleep_line(p):
    parts = [f"{label} {seconds(p[key])}" for key, label in
             (("sleep_24g", "2.4G"), ("sleep_bt", "Bluetooth")) if key in p]
    deep = [f"{label} {seconds(p[key])}" for key, label in
            (("deep_sleep_24g", "2.4G"), ("deep_sleep_bt", "Bluetooth"))
            if key in p]
    return "  ".join(parts) + (f"   deep sleep: {'  '.join(deep)}" if deep else "")


def debounce_line(p):
    parts = [f"{label} {p[key]}" for key, label in
             (("debounce_wired", "wired"), ("debounce_24g", "2.4G"),
              ("debounce_bt", "Bluetooth")) if key in p]
    hw = ""
    if "hw_debounce_press" in p:
        hw = (f"   hardware (read-only): press {p['hw_debounce_press']}, "
              f"release {p.get('hw_debounce_release', '--')}")
    return "  ".join(parts) + hw


def angle_text(sensor):
    if sensor.get("angle_enabled") is False:
        return f"off ({sensor['angle']})"
    return str(sensor["angle"])


# ------------------------------------------------------------------ parser

def build_parser():
    ap = argparse.ArgumentParser(
        prog="aminf",
        description="Control Angry Miao AM Infinity mice (.97, .100, .100R2) "
                    "from the command line.")
    ap.add_argument("--version", action="version",
                    version=f"%(prog)s {__version__}")
    ap.add_argument("--mouse", choices=["auto", *MODELS], default="auto",
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

    p = sub.add_parser("status", help="show what the device reports")
    p.add_argument("--full", action="store_true",
                   help="also DPI, sleep, debounce and buttons (slower)")

    p = sub.add_parser("light", help="dongle lighting")
    ls = p.add_subparsers(dest="action", required=True)
    for name in ("on", "off", "toggle", "status"):
        ls.add_parser(name)
    q = ls.add_parser("color", aliases=["colour"]); q.add_argument("hex")
    q = ls.add_parser("brightness"); q.add_argument("value", type=int)
    q = ls.add_parser("speed"); q.add_argument("value", type=int)
    q = ls.add_parser("effect"); q.add_argument("name")

    p = sub.add_parser("mouse-light", help="mouse lighting")
    ms = p.add_subparsers(dest="action", required=True)
    for name in ("on", "off", "toggle", "status"):
        ms.add_parser(name)
    q = ms.add_parser("color", aliases=["colour"]); q.add_argument("hex")
    q.add_argument("--charged", action="store_true",
                   help="the colour shown once charged (.100)")

    p = sub.add_parser("all", help="dongle and mouse lighting together")
    p.add_argument("action", choices=["on", "off", "toggle"])

    p = sub.add_parser("rate", help="polling rate in Hz")
    p.add_argument("hz", type=int, choices=VALID_RATES)

    p = sub.add_parser("dpi", help="DPI stages")
    ds = p.add_subparsers(dest="action")
    q = ds.add_parser("set", help="set one stage")
    q.add_argument("stage", type=int, help="stage number, from 1")
    q.add_argument("x", type=int)
    q.add_argument("--y", type=int, help="a different vertical DPI")
    q.add_argument("--color", "--colour", dest="color",
                   help="the stage's indicator colour, #RRGGBB")
    q = ds.add_parser("stages", help="how many stages the DPI button cycles")
    q.add_argument("count", type=int)
    q = ds.add_parser("use", help="make a stage the active one")
    q.add_argument("stage", type=int, help="stage number, from 1")
    q = ds.add_parser("button", help="enable or disable the DPI button")
    q.add_argument("state", choices=["on", "off"])

    p = sub.add_parser("remap", help="what each button sends")
    p.add_argument("button", nargs="?", choices=BUTTON_NAMES)
    p.add_argument("action", nargs="?",
                   help="a key (f13, ctrl+shift+a, 0x47), media:<name>, a "
                        "mouse function, dpi-up/down/cycle, profile-switch, "
                        "disabled or default")

    p = sub.add_parser("toggle", help="sensor options")
    p.add_argument("feature", choices=sorted(TOGGLES))
    p.add_argument("state", choices=["on", "off"])

    p = sub.add_parser("lod", help="lift-off distance, 0 is lowest")
    p.add_argument("value", type=int)

    p = sub.add_parser("sleep", help="idle time before the mouse sleeps")
    p.add_argument("radio", nargs="?", choices=sorted(SLEEP_KEYS))
    p.add_argument("seconds", nargs="?", help="seconds, or 'never'")

    p = sub.add_parser("debounce", help="button debounce")
    p.add_argument("args", nargs="*", metavar="[wired|2.4g|bt] N")

    p = sub.add_parser("angle", help="sensor rotation, -90..90 or off")
    p.add_argument("value", nargs="?")

    p = sub.add_parser("profile", help="show or switch the onboard profile")
    p.add_argument("index", type=int, nargs="?")

    p = sub.add_parser("export", help="save the active profile to a file")
    p.add_argument("file")

    p = sub.add_parser("import", help="make the active profile match a file")
    p.add_argument("file", help="an aminf export, or the web configurator's "
                                ".97 export")
    p.add_argument("--only", help="comma-separated sections: "
                                  + ", ".join(profile.GROUPS))
    p.add_argument("--dry-run", dest="import_dry", action="store_true",
                   help="show what would change and write nothing")

    p = sub.add_parser("raw", help="send one read command and print the "
                                   "reply (.97 and .100R2)")
    p.add_argument("cmd_id", help="decimal or 0x-prefixed")
    p.add_argument("--to", default="0x80", help="device id (0x00 or 0x80)")

    return ap


# ---------------------------------------------------------------- commands

def light_state(cur, action):
    return (not cur["on"]) if (action == "toggle" and cur) else (action == "on")


def emit(args, model, data, lines):
    """A read-only result: JSON with --json, text otherwise."""
    if args.json:
        print(json.dumps(dict(data, model=model.name), indent=2))
    else:
        for line in lines:
            print(line)


def need(value, what="settings"):
    if value is None:
        raise AmError(f"could not read the {what}")
    return value


def cmd_light(m, args, say):
    dongle = args.cmd == "light"
    section = "dongle_light" if dongle else "light"
    what = "dongle" if dongle else "mouse"
    a = args.action
    if a == "status":
        cur = m.light_status_2_2(dongle)
        if not cur:
            raise AmError("no reply")
        print(json.dumps(dict(cur, model=m.model.name), indent=2))
    elif a in ("on", "off", "toggle"):
        if dongle and m.connection != "dongle":
            raise AmError("dongle lighting needs the 2.4G dongle connected")
        cur = None if m.dry_run else m.read(section)
        on = light_state(cur, a)
        m.write(section, {"on": on})
        say(f"{what} light -> {on_off(on)}")
    elif a in ("color", "colour"):
        key = "colour_charged" if getattr(args, "charged", False) else "colour"
        done = m.write(section, {key: args.hex})
        extra = (f" (hue {done['hue1']}, sat {done['sat1']})"
                 if "hue1" in done else "")
        say(f"{what} colour -> {hex_arg(args.hex)}{extra}")
    elif a in ("brightness", "speed"):
        m.write(section, {a: args.value})
        say(f"dongle {a} -> {args.value}")
    elif a == "effect":
        m.write(section, {"effect": args.name})
        say(f"dongle effect -> {args.name}")


def cmd_all(m, args, say):
    if m.connection == "dongle":
        cur = None if m.dry_run else m.read("dongle_light")
        on = light_state(cur, args.action)
        m.write("dongle_light", {"on": on})
        say(f"dongle light -> {on_off(on)}")
    else:
        say("no dongle connected -- dongle lighting skipped")
    cur = None if m.dry_run else m.read("light")
    on = light_state(cur, args.action)
    m.write("light", {"on": on})
    say(f"mouse light -> {on_off(on)}")


def cmd_dpi(m, args, say):
    a = args.action
    if a is None:
        d = need(m.read("dpi"), "DPI settings")
        lines = [f"active stage   {d['active'] + 1} of {d['count']}"]
        if "button" in d:
            lines.append(f"dpi button     {on_off(d['button'])}")
        for i, st in enumerate(d["stages"]):
            mark = "*" if i == d["active"] else " "
            xy = f"{st['x']}" + (f" x {st['y']}" if st["y"] != st["x"] else "")
            unused = "" if i < d["count"] else "  (not in the cycle)"
            lines.append(f"stage {i + 1}      {mark} {xy:<14}"
                         f"{st.get('colour') or '':<9}{unused}".rstrip())
        emit(args, m.model, d, lines)
    elif a == "set":
        change = {"x": args.x}
        if args.y is not None:
            change["y"] = args.y
        if args.color:
            change["colour"] = args.color
        m.write("dpi", {"stages": {args.stage - 1: change}})
        y = f" x {args.y}" if args.y is not None else ""
        colour = f"  {hex_arg(args.color)}" if args.color else ""
        say(f"dpi stage {args.stage} -> {args.x}{y}{colour}")
    elif a == "stages":
        m.write("dpi", {"count": args.count})
        say(f"dpi stages -> {args.count}")
    elif a == "use":
        m.write("dpi", {"active": args.stage - 1})
        say(f"dpi stage -> {args.stage}")
    elif a == "button":
        m.write("dpi", {"button": args.state == "on"})
        say(f"dpi button -> {args.state}")


def cmd_remap(m, args, say):
    if args.button and args.action:
        done = m.write("buttons", {args.button: args.action})
        got = done.get(args.button, args.action)
        default = args.action.strip().lower() == "default"
        say(f"{args.button} -> {show_action(got)}"
            f"{' (default)' if default else ''}")
        return
    buttons = need(m.read("buttons"), "button map")
    if args.button:
        if args.button not in buttons:
            raise Unsupported(f"the {m.model.name} has no '{args.button}' "
                              f"button")
        buttons = {args.button: buttons[args.button]}
    lines = []
    for b, action in buttons.items():
        own = action == format_action(m.model.default_action(b))
        lines.append(f"{b:<10} {show_action(action)}"
                     f"{' (default)' if own else ''}")
    emit(args, m.model, buttons, lines)


def cmd_sleep(m, args, say):
    if args.radio is None:
        p = need(m.read("power"), "sleep times")
        emit(args, m.model, {k: v for k, v in p.items() if "sleep" in k},
             [f"sleep          {sleep_line(p)}"])
        return
    if args.seconds is None:
        raise AmError("give a number of seconds, or 'never'")
    value = "never" if args.seconds.lower() == "never" else to_int(args.seconds, "seconds")
    m.write("power", {SLEEP_KEYS[args.radio]: value})
    say(f"sleep {args.radio} -> {seconds(value)}")


def cmd_debounce(m, args, say):
    a = args.args
    if not a:
        p = need(m.read("power"), "debounce settings")
        emit(args, m.model, {k: v for k, v in p.items() if "debounce" in k},
             [f"debounce       {debounce_line(p)}"])
    elif len(a) == 1:
        n = to_int(a[0], "debounce")
        m.write("power", {key: n for key in RADIOS.values()})
        say(f"debounce -> {n}")
    elif len(a) == 2 and a[0] in RADIOS:
        n = to_int(a[1], "debounce")
        m.write("power", {RADIOS[a[0]]: n})
        say(f"debounce {a[0]} -> {n}")
    else:
        raise AmError("usage: aminf debounce [wired|2.4g|bt] <n>")


def cmd_angle(m, args, say):
    if args.value is None:
        s = need(m.read("sensor"), "sensor settings")
        if "angle" not in s:
            raise Unsupported(f"the {m.model.name} reports no sensor angle")
        emit(args, m.model, {k: s[k] for k in ("angle", "angle_enabled") if k in s},
             [f"sensor angle   {angle_text(s)}"])
    elif args.value.lower() == "off":
        m.write("sensor", {"angle_enabled": False} if m.model.angle_switch
                else {"angle": 0})
        say("sensor angle -> off")
    else:
        n = to_int(args.value, "angle")
        change = {"angle": n}
        if m.model.angle_switch:
            change["angle_enabled"] = True
        m.write("sensor", change)
        say(f"sensor angle -> {n}")


def cmd_export(m, args, say):
    doc = profile.snapshot(m)
    try:
        with open(args.file, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        raise AmError(f"cannot write {args.file}: {exc.strerror or exc}") from None
    say(f"exported {m.model.name} profile {doc['profile']} -> {args.file}")


def cmd_import(m, args, say):
    doc = profile.load(args.file)
    only = ([x.strip() for x in args.only.split(",") if x.strip()]
            if args.only else None)
    dry = args.dry_run or args.import_dry
    warnings = profile.validate(doc, m.model, only)
    for note in doc.get("notes", []):
        say(f"note: {note}")
    for warning in warnings:
        say(f"warning: {warning}")
    active = m.profile()
    if None not in (doc.get("profile"), active) and doc["profile"] != active:
        say(f"note: the file was saved from profile {doc['profile']}; "
            f"writing to the active profile, {active}")
    results = profile.apply(m, doc, only, dry)
    for section, status, detail in results:
        print(f"{section:<13} {status}{'  ' + detail if detail else ''}")
    bad = [section for section, status, _ in results if status == "failed"]
    if bad:
        raise AmError(f"import did not complete: {', '.join(bad)} failed "
                      f"(see above)")


def to_int(text, what):
    try:
        return int(text, 0)
    except ValueError:
        raise AmError(f"{what} must be a number, not '{text}'") from None


def run_command(m, args, say):
    """Run one command on one mouse. Returns data for --json status."""
    c = args.cmd
    if c == "status":
        s = collect_status(m, args.full)
        if not args.json:
            print_status(s)
        return s
    if c in ("light", "mouse-light"):
        cmd_light(m, args, say)
    elif c == "all":
        cmd_all(m, args, say)
    elif c == "rate":
        m.write("sensor", {"polling_rate": args.hz})
        say(f"polling rate -> {args.hz} Hz")
    elif c == "dpi":
        cmd_dpi(m, args, say)
    elif c == "remap":
        cmd_remap(m, args, say)
    elif c == "toggle":
        on = args.state == "on"
        m.write("sensor", {TOGGLES[args.feature]: on})
        say(f"{args.feature} -> {args.state}")
        if args.feature == "fpsmode" and on:
            say(FPS_WARNING)
    elif c == "lod":
        m.write("sensor", {"lod": args.value})
        say(f"lift-off distance -> {args.value}")
    elif c == "sleep":
        cmd_sleep(m, args, say)
    elif c == "debounce":
        cmd_debounce(m, args, say)
    elif c == "angle":
        cmd_angle(m, args, say)
    elif c == "profile":
        if args.index is None:
            emit(args, m.model, {"profile": need(m.profile(), "active profile")},
                 [f"profile        {m.profile()}"] if not args.json else [])
        else:
            before = None if m.dry_run else m.profile()
            m.set_profile(args.index)
            say(f"profile -> {args.index}")
            if m.profile_switch_note and before != args.index:
                say(f"note: {m.profile_switch_note}")
    elif c == "export":
        cmd_export(m, args, say)
    elif c == "import":
        cmd_import(m, args, say)
    elif c == "raw":
        if m.model.family != "race":
            raise Unsupported(f"'raw' is not available on the {m.model.name}")
        v = m.send(int(args.cmd_id, 0), device_id=int(args.to, 0))
        print(v.hex(" ") if v else "no reply")
    return None


def main(argv=None, backend=None):
    args = build_parser().parse_args(argv)
    if backend is None:
        backend = HidapiBackend()
    say_base = (lambda *a: None) if args.quiet else print

    # export and import read the device even in a dry run; import then
    # writes nothing.
    whole_profile = args.cmd in ("export", "import")
    mice, errors = open_mice(backend, args.mouse, args.device,
                             args.dry_run and not whole_profile)
    if not mice:
        for e in errors or ["No AM Infinity mouse found. Plug in the 2.4G "
                            "dongle or connect the mouse by cable."]:
            print(e, file=sys.stderr)
        return 1
    if whole_profile and len(mice) > 1:
        for m in mice:
            m.close()
        print(f"{args.cmd} works on one mouse at a time and "
              f"{len(mice)} are connected: add --mouse "
              f"{'|'.join(m.model.key for m in mice)}", file=sys.stderr)
        return 2

    several = len(mice) > 1
    failed = False
    status_out = []
    try:
        for m in mice:
            say = say_base
            if several and not args.quiet and not args.json:
                print(f"--- {m.model.name}")
            try:
                out = run_command(m, args, say)
                if out is not None:
                    status_out.append(out)
            except Unsupported as exc:
                say(f"{exc} -- skipped")
            except AmError as exc:
                failed = True
                print(f"{m.model.name}: {exc}", file=sys.stderr)
            except Exception as exc:                         # pragma: no cover
                failed = True
                print(f"{m.model.name}: {args.cmd} failed: {exc}",
                      file=sys.stderr)
    finally:
        for m in mice:
            m.close()

    if args.json and args.cmd == "status":
        print(json.dumps(status_out, indent=2))
    for e in errors:
        print(e, file=sys.stderr)
    return 2 if failed else 0
