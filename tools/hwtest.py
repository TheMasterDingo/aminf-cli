"""Exercise every write aminf can make on a real mouse, then put it back.

    python tools/hwtest.py --mouse 100r2 [--device dongle|usb] [--yes]

What it does, in order:

  1. reads the whole active profile and saves it next to the report as
     <model>-<connection>-snapshot.json, before anything is written;
  2. changes one setting at a time to a different, harmless value and lets
     the driver confirm it by reading it back;
  3. restores the snapshot and reads everything again to compare.

Left and right click are never touched. If the mouse ends up on another
profile than it started on, nothing is restored (an import writes to the
active profile) and the tool says where the snapshot is.

The report, <model>-<connection>.json, lists each check as verified, failed
or unsupported. Exit status 0 means every check was verified or unsupported
and the mouse was restored exactly.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aminf import __version__, profile                          # noqa: E402
from aminf.discover import open_mice                            # noqa: E402
from aminf.errors import AmError, NotInFirmware, Unsupported    # noqa: E402
from aminf.models import MODELS                                 # noqa: E402

FLAGS = ("motion_sync", "angle_snap", "ripple", "fps_mode", "profile_key")


def vectors(model, s):
    """(check name, section, changes) for everything this mouse reports.
    Each value differs from the snapshot `s` and is inside the model's
    range."""
    out = []
    sensor, power, dpi = s.get("sensor", {}), s.get("power", {}), s.get("dpi", {})
    if "polling_rate" in sensor:
        out.append(("polling_rate", "sensor",
                    {"polling_rate": 500 if sensor["polling_rate"] == 1000 else 1000}))
    if "lod" in sensor:
        lod = sensor["lod"]
        out.append(("lod", "sensor",
                    {"lod": lod + 1 if lod < model.lod_max else lod - 1}))
    for key in FLAGS:
        if key in sensor:
            out.append((key, "sensor", {key: not sensor[key]}))
    if "angle" in sensor:
        out.append(("angle", "sensor", {"angle": 2 if sensor["angle"] == 1 else 1}))
    for key in ("sleep_24g", "sleep_bt", "deep_sleep_24g", "deep_sleep_bt"):
        if key in power:
            cur = power[key]
            out.append((key, "power", {key: 600 if cur == "never" else cur + 30}))
    for key in ("debounce_wired", "debounce_24g", "debounce_bt"):
        if key in power:
            cur = power[key]
            out.append((key, "power", {key: cur + 1 if cur < 20 else cur - 1}))
    stages = dpi.get("stages") or []
    if len(stages) >= 8:
        last = stages[7]
        out.append(("dpi stage 8", "dpi",
                    {"stages": {7: {"x": 1050 if last["x"] == 1000 else 1000}}}))
        colour = "#00FFFF" if (last.get("colour") or "").upper() == "#00FF00" else "#00FF00"
        out.append(("dpi stage 8 colour", "dpi", {"stages": {7: {"colour": colour}}}))
    if "count" in dpi:
        out.append(("dpi count", "dpi", {"count": 7 if dpi["count"] == 8 else 8}))
    if "button" in dpi:
        out.append(("dpi button", "dpi", {"button": not dpi["button"]}))
    buttons = s.get("buttons", {})
    if "forward" in buttons:
        out.append(("remap forward", "buttons",
                    {"forward": "f14" if buttons["forward"] == "f13" else "f13"}))
    if "wheel-down" in buttons:
        out.append(("remap wheel-down", "buttons",
                    {"wheel-down": "media:vol-down"
                     if buttons["wheel-down"] == "media:mute" else "media:mute"}))
    if "on" in s.get("light", {}):
        out.append(("light on/off", "light", {"on": not s["light"]["on"]}))
    if "on" in s.get("dongle_light", {}):
        out.append(("dongle light on/off", "dongle_light",
                    {"on": not s["dongle_light"]["on"]}))
    return out


def run(key, backend=None, device="auto", out_dir=None, yes=False):
    model = MODELS[key]
    out_dir = pathlib.Path(out_dir) if out_dir else ROOT / "docs" / "hardware"
    if backend is None:
        from aminf.transport import HidapiBackend
        backend = HidapiBackend()
    mice, errors = open_mice(backend, which=key, prefer=device)
    if not mice:
        print("\n".join(errors) or f"AM Infinity {model.name} not found.")
        return 1
    mouse = mice[0]
    try:
        return _run(mouse, out_dir, yes)
    finally:
        mouse.close()


def _run(mouse, out_dir, yes):
    model = mouse.model
    tag = f"{model.key}-{mouse.connection}"
    try:
        first = profile.snapshot(mouse)
    except AmError as exc:
        print(f"{model.name}: {exc} -- nothing written")
        return 1
    if first["profile"] is None:
        print(f"{model.name}: could not read the active profile -- nothing "
              f"written")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    snap_path = out_dir / f"{tag}-snapshot.json"
    snap_path.write_text(json.dumps(first, indent=2) + "\n", encoding="utf-8",
                         newline="\n")
    checks = vectors(model, first["sections"])
    print(f"{model.name} on {mouse.connection}, profile {first['profile']}: "
          f"{len(checks) + 1} checks. Snapshot saved to {snap_path}")
    if not yes:
        answer = input("This changes settings one at a time and restores "
                       "them afterwards. Continue? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("nothing written")
            return 1

    report = []

    def record(name, result, detail=""):
        report.append({"check": name, "result": result, "detail": detail})
        print(f"  {name:<22} {result}{'  ' + detail if detail else ''}")

    for name, section, changes in checks:
        try:
            done = mouse.write(section, changes)
            record(name, "verified", json.dumps(done, sort_keys=True))
        except (Unsupported, NotInFirmware) as exc:
            record(name, "unsupported", str(exc))
        except AmError as exc:
            record(name, "failed", str(exc))

    home = first["profile"]
    other = (home + 1) % model.profiles
    try:
        mouse.set_profile(other)
        mouse.set_profile(home)
        record("profile switch", "verified", f"{home} -> {other} -> {home}")
    except AmError as exc:
        record("profile switch", "failed", str(exc))

    # An import writes to whichever profile is active. Never restore into a
    # profile other than the one the snapshot came from.
    restore, restored, final = [], False, None
    try:
        now = mouse.profile()
        if now != home:
            print(f"  the mouse is on profile {now}, not {home}: switching back")
            mouse.set_profile(home)
        restore = profile.apply(mouse, first)
        final = profile.snapshot(mouse)
        restored = final == first
    except AmError as exc:
        print(f"  NOT RESTORED: {exc}")
        print(f"  The mouse may be on another profile. Put it on profile "
              f"{home}, then run:  aminf --mouse {model.key} import "
              f"{snap_path}")
    for section, status, detail in restore:
        print(f"  restore {section:<14} {status}{'  ' + detail if detail else ''}")
    if final is not None and not restored:
        for section in first["sections"]:
            if final["sections"].get(section) != first["sections"][section]:
                print(f"  DIFFERS after restore: {section}")
                print(f"    before: {json.dumps(first['sections'][section], sort_keys=True)}")
                print(f"    after:  {json.dumps(final['sections'].get(section), sort_keys=True)}")

    doc = {"model": model.name, "connection": mouse.connection,
           "date": datetime.date.today().isoformat(), "aminf": __version__,
           "profile": home, "checks": report,
           "restore": [list(r) for r in restore], "restored": restored}
    (out_dir / f"{tag}.json").write_text(json.dumps(doc, indent=2) + "\n",
                                         encoding="utf-8", newline="\n")
    bad = [c["check"] for c in report if c["result"] == "failed"]
    print(f"{model.name} on {mouse.connection}: "
          f"{sum(c['result'] == 'verified' for c in report)} verified, "
          f"{sum(c['result'] == 'unsupported' for c in report)} unsupported, "
          f"{len(bad)} failed; restored: {'yes' if restored else 'NO'}")
    return 0 if restored and not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mouse", required=True, choices=list(MODELS))
    ap.add_argument("--device", choices=["auto", "dongle", "usb"], default="auto")
    ap.add_argument("--out", help="report folder (default: docs/hardware)")
    ap.add_argument("--yes", action="store_true", help="do not ask first")
    args = ap.parse_args(argv)
    return run(args.mouse, device=args.device, out_dir=args.out, yes=args.yes)


if __name__ == "__main__":
    sys.exit(main())
