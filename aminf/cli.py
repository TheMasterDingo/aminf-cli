"""Command line: parse what was typed, run it on each mouse, print the result."""
from __future__ import annotations

import argparse
import json
import sys

from aminf import __version__
from aminf.colour import hex_to_hue_sat, hue_to_hex
from aminf.discover import open_mice
from aminf.errors import AmError, MouseOffline, Unsupported
from aminf.keys import key_name, parse_remap_key
from aminf.models import MODELS
from aminf.race.device import DONGLE_EFFECTS, MOUSE_EFFECTS, VALID_RATES, RaceMouse

REMAP_KEY_ID = RaceMouse.REMAP_KEY_ID
from aminf.transport import HidapiBackend


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


def collect_status(m):
    if m.model.family == "m100":
        params = None if m.dry_run else m._params_or_none()
        return {
            "model": m.model.name,
            "connection": "dongle" if m.dongle else "usb",
            "battery": m.battery(),
            "hub_battery": m.hub_battery(),
            "profile": m.profile(),
            "polling_rate": m.polling_rate(params) if params else None,
            "mouse_light": m.mouse_light(params) if params else None,
            "dongle_light": m.dongle_light(),
            "mouse_offline": m.offline,
        }
    def quiet(read):
        # A mouse that is asleep is reported once, not once per line.
        try:
            return read()
        except MouseOffline:
            return None

    return {
        "model": m.model.name,
        "connection": "dongle" if m.has_dongle else "usb",
        "battery": quiet(m.battery),
        "hub_battery": m.hub_battery(),
        "profile": quiet(m.profile),
        "polling_rate": quiet(m.polling_rate),
        "lod": quiet(lambda: m.get("lod")),
        "motion_sync": quiet(lambda: m.get("motion_sync")),
        "angle_snapping": quiet(lambda: m.get("angle_snap")),
        "ripple_control": quiet(lambda: m.get("ripple")),
        "mouse_light": quiet(m.mouse_light),
        "mouse_colour": quiet(m.mouse_colour),
        "dongle_light": m.dongle_light(),
        "mouse_offline": m.offline,
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

    if s.get("mouse_offline"):
        print("mouse          off or out of range (dongle values only)")
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
    is100 = m.model.family == "m100"
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
            print(json.dumps(dict(cur, model=m.model.name), indent=2))
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
                print(json.dumps(dict(labels, model=m.model.name), indent=2))
            else:
                for b, label in labels.items():
                    print(f"{b:<8} {label}")

    elif c == "raw":
        v = m.send(int(args.cmd_id, 0), device_id=int(args.to, 0))
        print(v.hex(" ") if v else "no reply")
    return None


def main(argv=None, backend=None):
    args = build_parser().parse_args(argv)
    if backend is None:
        backend = HidapiBackend()
    say_base = (lambda *a: None) if args.quiet else print

    mice, errors = open_mice(backend, args.mouse, args.device, args.dry_run)
    if not mice:
        for e in errors or ["No AM Infinity mouse found. Plug in the 2.4G "
                            "dongle or connect the mouse by cable."]:
            print(e, file=sys.stderr)
        return 1

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
                say(str(exc))
            except AmError as exc:
                failed = True
                print(f"{m.model.name}: {exc}", file=sys.stderr)
            except Exception as exc:                         # pragma: no cover
                failed = True
                print(f"{m.model.name}: {args.cmd} failed: {exc}", file=sys.stderr)
    finally:
        for m in mice:
            m.close()

    if args.json and args.cmd == "status":
        print(json.dumps(status_out, indent=2))
    for e in errors:
        print(e, file=sys.stderr)
    return 2 if failed else 0

