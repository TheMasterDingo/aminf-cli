"""A simulated AM Infinity .100, seeded with replies captured from a real one."""
from aminf.transport import Hid
from tests.fakes import info

RELAY = (246, 247, 254, 252)        # the dongle's own relay plumbing


def _checksummed(block, first):
    b = bytearray(block)
    b[0] = first
    b[7] = (255 - (sum(b[:7]) & 0xFF)) & 0xFF
    return bytes(b)


class _Handle:
    def __init__(self, fake):
        self.fake = fake

    def send_feature(self, data):
        self.fake._receive(bytes(data[1:65]).ljust(64, b"\0"))

    def get_feature(self, report_id, length):
        return bytes([report_id]) + self.fake._response

    def close(self):
        self.fake.closed = True


class FakeM100(Hid):
    """Speaks the .100's packets: the dongle's own commands, the relay
    handshake (open 246, status 247, length 254, fetch 252) and the mouse's
    blocks. Writes change the simulated blocks, so reading back works.

    Knobs for failure tests:
      offline             the dongle reports the mouse away
      drop_writes = {c}   those mouse writes are ignored
      drop_first = {c}    only the first such write is ignored
      truncate = {c}      that read answers with a block that is not one
      profile_max         what the mouse reports as its profile count
    """

    def __init__(self, fixture, dongle=True):
        r = {k: bytes.fromhex(v) for k, v in fixture["replies"].items() if v}
        self.dongle = dongle
        self.infos = [info(0x3151, 0x5007 if dongle else 0x402A, 0xFFFF, 1)]
        self.profile = fixture.get("profile", 0)
        self.blocks = {
            211: r["211:"], 212: r[f"212:{self.profile:02x}"],
            208: r[f"208:{self.profile:02x}00"], 214: r["214:"],
            133: r["133:"], 226: r["226:"], 227: r["227:"], 228: r["228:"],
            229: r["229:"],
        }
        self.local = {135: r.get("local/135:", bytes(64)),
                      136: r.get("local/136:", bytes(64)),
                      247: r.get("local/247:", bytes(64))}
        self.profile_max = None
        self.offline = False
        self.drop_writes, self.drop_first, self.truncate = set(), set(), set()
        self.missing = set()            # reads the firmware never answers
        self.stale_keymap_once = False  # first key-map read returns an old reply
        self.sent = []                  # every packet that is not relay plumbing
        self.closed = False
        self._response = bytes(64)
        self._next_is_forwarded = False
        self._pending = None            # the mouse's answer, waiting for 252

    # -- Hid ----------------------------------------------------------------
    def enumerate(self):
        return list(self.infos)

    def open(self, path):
        return _Handle(self)

    # -- what tests look at ---------------------------------------------------
    def sent_cmds(self):
        return [p[0] for p in self.sent]

    def sent_packet(self, cmd):
        return [p for p in self.sent if p[0] == cmd][-1]

    def block(self, read_cmd):
        return self.blocks[read_cmd]

    # -- the simulated firmware -------------------------------------------------
    def _status(self):
        s = bytearray(self.local[247])
        s[4] = 1 if self.offline else 0
        s[5] = 1
        s[0] = 1 if self._pending is not None else 0
        return bytes(s)

    def _receive(self, pkt):
        if not self.dongle:
            self.sent.append(pkt)
            answer = self._mouse(pkt)
            self._response = answer if answer is not None else bytes(64)
            return
        code = pkt[0]
        if self._next_is_forwarded:
            self._next_is_forwarded = False
            self.sent.append(pkt)
            self._pending = None if self.offline else self._mouse(pkt)
            return
        if code == 246:
            self._pending = None
        elif code == 247:
            self._response = self._status()
        elif code == 254:
            self._next_is_forwarded = True
        elif code == 252:
            self._response = self._pending if self._pending is not None else bytes(64)
            self._pending = None
        else:
            self.sent.append(pkt)
            self._response = self._dongle(pkt)

    def _dongle(self, pkt):
        code = pkt[0]
        if code in (135, 136):
            return self.local[code]
        if code == 7:
            self.local[135] = _checksummed(bytes([135, pkt[1]]).ljust(64, b"\0"), 135)
        elif code == 8:
            self.local[136] = bytes([136]) + pkt[1:8] + bytes(56)
        return bytes(64)

    def _mouse(self, pkt):
        code = pkt[0]
        if code in self.missing:
            return None
        if code == 208 and self.stale_keymap_once:
            self.stale_keymap_once = False
            return self.blocks[133]
        if code in self.truncate and code != 208:
            return bytes([0, 1, 2, 3]).ljust(64, b"\0")
        if code == 133:
            b = bytearray(self.blocks[133])
            b[1] = self.profile
            if self.profile_max is not None:
                b[2] = self.profile_max
            return _checksummed(b, 133)
        if code in (211, 212, 208, 214, 226, 227, 228, 229):
            return self.blocks[code]
        if code in self.drop_writes:
            return None
        if code in self.drop_first:
            self.drop_first.discard(code)
            return None
        if code == 83:
            self.blocks[211] = _checksummed(pkt, 211)
        elif code == 84:
            self.blocks[212] = _checksummed(pkt, 212)
        elif code == 80:
            from aminf.m100.codec import KEY_OFFSET
            at = KEY_OFFSET[pkt[2]]
            b = bytearray(self.blocks[208])
            b[at:at + 4] = pkt[8:12]
            self.blocks[208] = bytes(b)
        elif code in (98, 99, 100, 101):
            read = {98: 226, 99: 227, 100: 228, 101: 229}[code]
            self.blocks[read] = _checksummed(bytes([read, pkt[1]]).ljust(64, b"\0"), read)
        elif code == 5:
            self.profile = pkt[1]
        return None
