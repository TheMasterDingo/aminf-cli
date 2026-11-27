"""In-memory HID backends for tests."""
from aminf.transport import Hid


class EmptyBackend(Hid):
    """A machine with no Angry Miao device plugged in."""

    def enumerate(self):
        return []

    def open(self, path):
        raise AssertionError("nothing to open")


def info(vid, pid, usage_page, interface=0, usage=1, product=""):
    """A HidInfo with a path unique to (vid, pid, usage_page)."""
    from aminf.transport import HidInfo
    path = f"{vid:04x}:{pid:04x}:{usage_page:04x}:{interface}".encode()
    return HidInfo(path, vid, pid, usage_page, usage, interface, product)


# What the owner's .100R2 dongle enumerates as on Windows (2026-11-27).
R2_DONGLE_IFACES = [
    info(0x0E8D, 0x0704, 0x0001, 0, usage=2),
    info(0x0E8D, 0x0704, 0xFF15, 0),
    info(0x0E8D, 0x0704, 0xFF13, 1),
    info(0x0E8D, 0x0704, 0xFF14, 1),
    info(0x0E8D, 0x0704, 0x0001, 2, usage=6),
    info(0x0E8D, 0x0704, 0x000C, 2),
]


class FakeBackend(Hid):
    """Enumerates the given interfaces; opening one is an error unless a
    subclass provides a device behind it."""

    def __init__(self, infos):
        self.infos = list(infos)

    def enumerate(self):
        return list(self.infos)

    def open(self, path):
        raise AssertionError(f"unexpected open of {path!r}")


# ---------------------------------------------------------------- .97 / .100R2

class _FakeRaceHandle:
    def __init__(self, fake):
        self.fake = fake

    def send_feature(self, data):
        self.fake._receive(bytes(data))

    def get_feature(self, report_id, length):
        q = self.fake._queue
        return q.pop(0) if q else bytes(length)

    def close(self):
        self.fake.closed = True


class FakeRace(Hid):
    """One simulated .97 or .100R2, seeded with replies captured from a real
    mouse. Reads are answered from the table; writes update the table the
    way the firmware would, so reading back after a write works.

    Knobs for failure tests:
      offline              the mouse behind the dongle stops answering
      drop_writes = {cmd}  those writes are acknowledged but not applied
      missing = {key}      those reads never answer ("12525:", "12483:08")
      reject = {cmd: st}   those writes answer with a non-zero status
      stick_profile_after  profile switches after the n-th are ignored
      sleep_after_writes   the mouse goes offline after that many writes
      dpi_limit            DPI values above it are rejected with status 2,
                           as the owner's .100R2 does above 26000
    """

    def __init__(self, fixture, model_key, dongle=True):
        from aminf.models import MODELS
        model = MODELS[model_key]
        vid, pid = (model.dongle_ids if dongle else model.usb_ids)[0]
        self.infos = [info(vid, pid, 0xFF13, 1)]
        self.dongle = dongle
        self.replies = {k: bytes.fromhex(v)
                        for k, v in fixture["replies"].items() if v is not None}
        self.sent = []                  # (device_id, cmd, data) per frame
        self.offline = False
        self.drop_writes = set()
        self.missing = set()
        self.reject = {}
        self.stick_profile_after = None
        self.sleep_after_writes = None
        self.dpi_limit = None
        self._writes = 0
        self.closed = False
        self._switches = 0
        self._queue = []

    # -- Hid ----------------------------------------------------------------
    def enumerate(self):
        return list(self.infos)

    def open(self, path):
        return _FakeRaceHandle(self)

    # -- what tests look at ---------------------------------------------------
    def sent_cmds(self):
        return [c for _, c, _ in self.sent]

    def sent_pairs(self):
        return [(d, c) for d, c, _ in self.sent]

    def sent_data(self, cmd):
        return [x for _, c, x in self.sent if c == cmd][-1]

    def sent_cmds_after_last(self, cmd):
        cmds = self.sent_cmds()
        last = len(cmds) - 1 - cmds[::-1].index(cmd)
        return cmds[last + 1:]

    # -- the simulated firmware -------------------------------------------------
    def _receive(self, data):
        from aminf.race import codec
        assert data[0] == codec.REPORT_ID and len(data) == codec.PAYLOAD_LEN + 1
        dev, body = data[2], data[3:3 + data[1]]
        assert body[0] == 0x05 and body[1] == codec.TYPE_CMD
        cmd = body[4] | (body[5] << 8)
        args = bytes(body[6:4 + (body[2] | (body[3] << 8))])
        self.sent.append((dev, cmd, args))
        local = self.dongle and dev == codec.DEV_LOCAL
        if self.dongle and dev not in (codec.DEV_LOCAL, codec.DEV_RELAY):
            return
        if not self.dongle and dev != codec.DEV_LOCAL:
            return
        if not local and self.offline:
            return
        key = ("local/" if local else "") + f"{cmd}:{args.hex()}"
        if cmd in codec.READS:
            if key in self.missing or key not in self.replies:
                return
            self._reply(dev, cmd, self.replies[key])
            return
        too_high = (cmd == 12484 and self.dpi_limit is not None and
                    max(args[1] | args[2] << 8, args[3] | args[4] << 8) > self.dpi_limit)
        if too_high:
            self._reply(dev, cmd, bytes([2]))
            return
        if cmd in self.reject:
            self._reply(dev, cmd, bytes([self.reject[cmd]]))
            return
        if cmd not in self.drop_writes:
            self._apply(cmd, args, local)
        self._reply(dev, cmd, b"\x00")
        self._writes += 1
        if self.sleep_after_writes is not None and self._writes >= self.sleep_after_writes:
            self.offline, self.sleep_after_writes = True, None

    def _reply(self, dev, cmd, payload):
        fr = bytes([0x05, 0x5B, (len(payload) + 2) & 0xFF, (len(payload) + 2) >> 8,
                    cmd & 0xFF, cmd >> 8]) + payload
        for i in range(0, len(fr), 59):
            chunk = fr[i:i + 59]
            self._queue.append((bytes([0x14, len(chunk), dev]) + chunk).ljust(62, b"\0"))

    def _nv(self, nv_id, data=None):
        key = "2572:" + bytes([nv_id & 0xFF, nv_id >> 8, 0xE8, 0x03]).hex()
        if data is not None:
            self.replies[key] = (bytes([0, nv_id & 0xFF, nv_id >> 8,
                                        len(data) & 0xFF, len(data) >> 8]) + data)
        return key

    def _apply(self, cmd, a, local):
        r = self.replies
        with_status = {12304: ("12305:", 2), 12492: ("12493:", 1),
                       12498: ("12499:", 1), 12500: ("12501:", 1),
                       12502: ("12503:", 1), 12522: ("12523:", 1),
                       12520: ("12521:", 3), 12504: ("12505:", 2)}
        bare = {12510: "12511:", 12524: "12525:"}
        if cmd in with_status:
            key, n = with_status[cmd]
            r[key] = b"\x00" + a[:n]
        elif cmd in bare:
            r[bare[cmd]] = a[:1]
        elif cmd == 12306:
            r["12307:"] = a[:24]
        elif cmd == 12488 and not local:
            self._switches += 1
            if self.stick_profile_after is None or self._switches <= self.stick_profile_after:
                r["12489:"] = b"\x00" + a[:1]
        elif cmd == 12484:
            d = bytearray(r["12485:"]); i = a[0]
            d[3 + 2 * i:5 + 2 * i] = a[1:3]
            d[19 + 2 * i:21 + 2 * i] = a[3:5]
            r["12485:"] = bytes(d)
        elif cmd == 12486:
            d = bytearray(r["12485:"]); d[1] = a[0]; r["12485:"] = bytes(d)
        elif cmd == 12487:
            d = bytearray(r["12485:"]); d[2] = a[0]; r["12485:"] = bytes(d)
        elif cmd == 12482:
            r[f"12483:{a[0]:02x}"] = b"\x00" + a
        elif cmd == 12301 and local:
            r["local/12302:"] = a[:13]
        elif cmd == 12467:
            self._nv(24586, a[:3])
        elif cmd == 2573:
            self._nv(a[0] | (a[1] << 8), a[2:])
        elif cmd == 2561:
            nv = a[0] | (a[1] << 8)
            old = r[self._nv(nv)][5:]
            self._nv(nv, a[2:18] + old[16:])


class Combined(Hid):
    """Several simulated devices plugged in at once."""

    def __init__(self, *fakes):
        self.fakes = fakes

    def enumerate(self):
        return [i for f in self.fakes for i in f.enumerate()]

    def open(self, path):
        for f in self.fakes:
            if any(i.path == path for i in f.enumerate()):
                return f.open(path)
        raise AssertionError(f"unexpected open of {path!r}")
