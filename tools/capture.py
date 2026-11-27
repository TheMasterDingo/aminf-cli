"""Record a mouse's replies to the configurator's own read commands.

Read-only: it sends nothing but reads the web configurator sends when it
loads its pages, and saves the raw replies as a test fixture.

usage: python tools/capture.py <97|100r2|100> [--device dongle|usb] [--out DIR]

Writes <DIR>/<model>-<connection>.json (default DIR: tests/fixtures).
Reply keys are "<cmd>:<args hex>" for the mouse and "local/<cmd>:<args hex>"
for the dongle itself; a read that got no answer is recorded as null.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aminf.models import MODELS                      # noqa: E402
from aminf.race import codec                         # noqa: E402
from aminf.transport import HidapiBackend            # noqa: E402

USAGE_PAGE = 0xFF13


def race_reads():
    """(target, cmd, args) for every read the configurator's pages send."""
    reads = [("local", 12303, b""), ("local", 12302, b"")]
    reads += [("mouse", c, b"") for c in (
        12489, 12495, 12305, 12485, 12523, 12521, 12493, 12499, 12501, 12503,
        12307, 12511, 12505, 12525)]
    reads += [("mouse", 12491, b"\x00"), ("mouse", 12491, b"\x01")]
    nv_ids = [codec.NV_LIGHT_SETTINGS, codec.NV_LIGHT_COLOURS, codec.NV_SLEEP_R2]
    nv_ids += [codec.NV_DPI_COLOUR_BASE + i for i in range(codec.DPI_STAGES)]
    reads += [("mouse", 2572, codec.read_nv(nv)) for nv in nv_ids]
    reads += [("mouse", 12483, bytes([k])) for k in range(1, 9)]
    return reads


def ask(handle, device_id, cmd, args, timeout):
    assert cmd in codec.READS, cmd
    handle.send_feature(bytes([codec.REPORT_ID]) + codec.frame(device_id, cmd, args))
    reader = codec.FrameReader()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for fr in reader.feed(handle.get_feature(codec.REPORT_ID, codec.PAYLOAD_LEN + 1)):
            if fr.cmd == cmd:
                return fr.payload
        time.sleep(0.02)
    return None


def find(backend, model, prefer):
    dongle = usb = None
    for d in backend.enumerate():
        if d.usage_page != USAGE_PAGE:
            continue
        ids = (d.vendor_id, d.product_id)
        if ids in model.dongle_ids:
            dongle = d
        elif ids in model.usb_ids:
            usb = d
    if prefer == "usb":
        return usb, False
    if prefer == "dongle" or dongle:
        return dongle, True
    return usb, False


def capture_m100(prefer, out_dir):
    """The .100: the reads its configurator pages send."""
    from aminf.m100 import codec as m100
    from aminf.m100.device import M100Mouse

    dev = M100Mouse.open(HidapiBackend(), MODELS["100"], prefer=prefer)
    replies, answered = {}, 0

    def save(key, fetch):
        nonlocal answered
        try:
            r = fetch()
        except Exception as exc:                      # asleep, relay timeout
            r = None
            print(f"{key:20} -- {exc}")
        replies[key] = None if r is None else bytes(r).hex()
        if r is not None:
            answered += 1
            print(f"{key:20} {bytes(r).hex(' ')}")
        return r

    try:
        if dev.dongle:
            save(f"local/{m100.DONGLE_STATUS}:",
                 lambda: dev.dongle.send(m100.raw(m100.DONGLE_STATUS), expect=True))
            for code in (m100.GET_DONGLE_SWITCH, m100.GET_DONGLE_LIGHT):
                save(f"local/{code}:",
                     lambda code=code: dev.dongle.send(m100.cmd(code), expect=True))
        prof = save(f"{m100.GET_PROFILE}:",
                    lambda: dev._to_mouse(m100.cmd(m100.GET_PROFILE), expect=True))
        profile = prof[1] if prof and prof[0] == m100.GET_PROFILE and prof[1] < 8 else 0
        for code, params in ((m100.GET_BATTERY, ()), (m100.GET_PARAMS, ()),
                             (m100.GET_DPI, (profile,)), (m100.GET_KEYMAP, (profile, 0)),
                             (226, ()), (227, ()), (228, ()), (229, ())):
            save(f"{code}:{bytes(params).hex()}",
                 lambda code=code, params=params: dev._to_mouse(
                     m100.cmd(code, params), expect=True))
        connection = "dongle" if dev.dongle else "usb"
    finally:
        dev.close()
    out = pathlib.Path(out_dir) / f"100-{connection}.json"
    out.write_text(json.dumps({
        "model": "100", "connection": connection, "profile": profile,
        "captured": datetime.date.today().isoformat(), "replies": replies,
    }, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{answered} of {len(replies)} reads answered -> {out}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("model", choices=list(MODELS))
    ap.add_argument("--device", choices=["auto", "dongle", "usb"], default="auto")
    ap.add_argument("--out", default=str(pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures"))
    args = ap.parse_args(argv)
    model = MODELS[args.model]
    if model.family == "m100":
        return capture_m100(args.device, args.out)
    backend = HidapiBackend()
    found, is_dongle = find(backend, model, args.device)
    if not found:
        sys.exit(f"AM Infinity {model.name} not found.")
    handle = backend.open(found.path)
    replies, answered = {}, 0
    try:
        for target, cmd, data in race_reads():
            if target == "local" and not is_dongle:
                continue
            device_id = codec.DEV_LOCAL if target == "local" or not is_dongle else codec.DEV_RELAY
            payload = ask(handle, device_id, cmd, data, 3.0 if is_dongle else 1.5)
            key = ("local/" if target == "local" else "") + f"{cmd}:{data.hex()}"
            replies[key] = None if payload is None else payload.hex()
            answered += payload is not None
            print(f"{key:28} {'-- no reply' if payload is None else payload.hex(' ')}")
    finally:
        handle.close()
    connection = "dongle" if is_dongle else "usb"
    out = pathlib.Path(args.out) / f"{args.model}-{connection}.json"
    out.write_text(json.dumps({
        "model": args.model, "connection": connection,
        "captured": datetime.date.today().isoformat(), "replies": replies,
    }, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{answered} of {len(replies)} reads answered -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
