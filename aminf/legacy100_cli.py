"""The 2.2.0 command line for the .100, kept until aminf.m100 replaces the
old driver. Everything it cannot do yet is reported as a skipped notice."""
from __future__ import annotations

import json

from aminf.errors import AmError, Unsupported
from aminf.keys import key_name, parse_remap_key

NOT_YET = {"toggle", "lod", "profile", "raw", "dpi", "sleep", "debounce",
           "angle", "export", "import"}
NOT_YET_LIGHT = {"brightness", "speed", "effect"}
SIDE_BUTTONS = ("forward", "back")


def collect_status(m):
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


def print_status_tail(s):
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


def light_state(cur, action):
    return (not cur["on"]) if (action == "toggle" and cur) else (action == "on")


def run_command(m, args, say, print_status):
    c = args.cmd
    if (c in NOT_YET or (c == "light" and args.action in NOT_YET_LIGHT) or
            (c == "mouse-light" and args.action in ("color", "colour"))):
        what = c
        if c in ("light", "mouse-light"):
            what += " " + args.action
        raise Unsupported(f"'{what}' is not available on the .100 yet")

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
            print(json.dumps(dict(cur, model=m.model.name), indent=2))
        elif a in ("on", "off", "toggle"):
            if dongle and not m.has_dongle:
                raise AmError("dongle lighting needs the 2.4G dongle connected")
            on = light_state(read(), a)
            (m.set_dongle_light if dongle else m.set_mouse_light)(on)
            say(f"{'dongle' if dongle else 'mouse'} light -> "
                f"{'on' if on else 'off'}")
        elif a in ("color", "colour"):
            m.set_dongle_colour(args.hex)
            say(f"dongle colour -> #{args.hex.lstrip('#').upper()}")

    elif c == "all":
        if m.has_dongle:
            on = light_state(m.dongle_light(), args.action)
            m.set_dongle_light(on)
            say(f"dongle light -> {'on' if on else 'off'}")
        else:
            say("no dongle connected -- dongle lighting skipped")
        on = light_state(m.mouse_light(), args.action)
        m.set_mouse_light(on)
        say(f"mouse light -> {'on' if on else 'off'}")

    elif c == "rate":
        m.set_polling_rate(args.hz)
        say(f"polling rate -> {args.hz} Hz")

    elif c == "remap":
        if args.button and args.button not in SIDE_BUTTONS:
            raise Unsupported(f"remapping '{args.button}' is not available "
                              f"on the .100 yet")
        if args.button and args.action:
            usage = parse_remap_key(args.action)
            m.set_key_remap(args.button, usage)
            say(f"{args.button} -> "
                f"{'default' if usage is None else key_name(usage)}")
        else:
            buttons = [args.button] if args.button else list(SIDE_BUTTONS)
            labels = {b: m.remap_label(b) for b in buttons}
            if args.json:
                print(json.dumps(dict(labels, model=m.model.name), indent=2))
            else:
                for b, label in labels.items():
                    print(f"{b:<8} {label}")
    return None
