"""The .97 / .100R2 wire format: frames in, values out. No I/O here.

Both mice speak the same protocol (the configurator calls it RACE). A
command rides HID feature report 0x14 on vendor collection 0xFF13:

    [payload_len][device_id] [05][type][len_lo][len_hi][cmd_lo][cmd_hi][data]

device_id 0x00 addresses whatever is plugged in; 0x80 addresses the mouse
through the dongle. type 0x5A is a command, 0x5B a reply, 0x5D a write
acknowledgement. len counts the command id plus the data.

Every layout below was transcribed from the configurator's JavaScript bundle
or captured from real hardware. Nothing is guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from aminf.errors import AmError, Refused

REPORT_ID = 0x14
PAYLOAD_LEN = 61
MAX_BODY = 59                  # what fits in one report after len + device id
DEV_LOCAL, DEV_RELAY = 0x00, 0x80
TYPE_CMD, TYPE_REPLY, TYPE_WRITE_ACK = 0x5A, 0x5B, 0x5D

CMD = {
    "getProfile": 12489, "changeProfile": 12488,
    "getBattery": 12495, "getDongleBattery": 12303,
    "getReportRate": 12305, "setReportRate": 12304,
    "getDpi": 12485, "setDpi": 12484,
    "setDpiLoopRange": 12486, "setCurrentDpiStage": 12487,
    "getDpiButton": 12523, "setDpiButton": 12522,
    "getKeyRemap": 12483, "setKeyRemap": 12482,
    "getSwDebounce": 12521, "setSwDebounce": 12520,
    "getHardwareDebounce": 12491,
    "getLod": 12493, "setLod": 12492,
    "getMotionSync": 12499, "setMotionSync": 12498,
    "getAngleSnapping": 12501, "setAngleSnapping": 12500,
    "getRippleControl": 12503, "setRippleControl": 12502,
    "getSleepTime": 12307, "setSleepTime": 12306,
    "getFpsMode": 12511, "setFpsMode": 12510,
    "getRotateAngle": 12505, "setRotateAngle": 12504,
    "getProfileKey": 12525, "setProfileKey": 12524,
    "getDongleLight": 12302, "setDongleLight": 12301,
    "setLightSettings": 12467, "setRgbRealtime": 12480,
    "readNvData": 2572, "saveRgb": 2573, "writeNvData": 2561,
}

# The only command ids this module will ever put in a frame. Reads, writes
# and destructive operations share one flat id space with nothing in the
# bytes to tell them apart, so anything not listed here is refused: reset
# (12494), clear-bonded (12395), pairing (11395-11442, 12394, 4353),
# firmware (7168-7178, 1026, 1028) and every id nobody has identified.
READS = frozenset({12489, 12495, 12303, 12305, 12485, 12523, 12483, 12521,
                   12491, 12493, 12499, 12501, 12503, 12307, 12511, 12505,
                   12525, 12302, 2572})
WRITES = frozenset({12488, 12304, 12484, 12486, 12487, 12522, 12482, 12520,
                    12492, 12498, 12500, 12502, 12306, 12510, 12504, 12524,
                    12301, 12467, 12480, 2573, 2561})

NV_LIGHT_SETTINGS = 24586      # 0x600A: [enabled, effect, speed]
NV_LIGHT_COLOURS = 25088       # 0x6200: the mouse light's rgb pattern
NV_DPI_COLOUR_BASE = 25121     # 0x6221 + stage: one record per DPI stage
NV_SLEEP_R2 = 24576            # 0x6000: sleep times on a cabled .100R2
NV_READ_LEN = 1000
DPI_STAGES = 8

# Writes to non-volatile records are pinned to the records this tool owns.
_SAVE_RGB_IDS = frozenset({NV_LIGHT_COLOURS}) | frozenset(
    range(NV_DPI_COLOUR_BASE, NV_DPI_COLOUR_BASE + DPI_STAGES))


def u16(v):
    return bytes([v & 0xFF, (v >> 8) & 0xFF])


def _u16_at(b, i):
    return (b[i] if i < len(b) else 0) | ((b[i + 1] if i + 1 < len(b) else 0) << 8)


# ------------------------------------------------------------------ frames

def frame(device_id, cmd, data=b""):
    """The 61-byte report payload for one command.

    Raises Refused, before anything can be sent, for a command id that is
    not on the allow-list or an NV write outside the pinned records.
    """
    data = bytes(data)
    if cmd not in READS and cmd not in WRITES:
        raise Refused(f"refusing command {cmd} ({cmd:#06x}): not on the "
                      f"allow-list")
    if cmd == CMD["saveRgb"]:
        nv = _u16_at(data, 0)
        if len(data) < 2 or nv not in _SAVE_RGB_IDS:
            raise Refused(f"refusing saveRgb to NV record {nv:#06x}")
    if cmd == CMD["writeNvData"]:
        nv = _u16_at(data, 0)
        if nv != NV_SLEEP_R2 or len(data) != 18:
            raise Refused(f"refusing NV write to record {nv:#06x} "
                          f"({len(data)} bytes)")
    body = bytes([0x05, TYPE_CMD]) + u16(len(data) + 2) + u16(cmd) + data
    if len(body) > MAX_BODY:
        raise AmError(f"command {cmd} needs {len(body)} bytes; one report "
                      f"carries {MAX_BODY}")
    return (bytes([len(body), device_id & 0xFF]) + body).ljust(PAYLOAD_LEN,
                                                               b"\x00")


@dataclass(frozen=True)
class Frame:
    cmd: int
    type: int
    payload: bytes


class FrameReader:
    """Turns feature reports into whole reply frames.

    A report can hold nothing, one reply, several stacked replies, a reply
    left over from an earlier command, or only part of a long reply. So the
    payload bytes of successive reports are joined and whole frames are cut
    out of that stream, as the configurator does.
    """

    def __init__(self):
        self._buf = bytearray()

    def reset(self):
        self._buf.clear()

    def feed(self, report):
        """`report` is what a feature read returns: [0x14][len][device_id]
        [bytes...]. Returns the reply frames completed by it."""
        if len(report) < 3:
            return []
        n = report[1]
        self._buf += bytes(report[3:3 + n])
        out = []
        buf = self._buf
        while len(buf) >= 6:
            if buf[0] not in (0x05, 0x15):
                del buf[0]
                continue
            need = (buf[2] | (buf[3] << 8)) + 4
            if need < 6 or need > 4 + 2 + NV_READ_LEN + 16:
                del buf[0]                      # not a plausible frame header
                continue
            if len(buf) < need:
                break
            fr = bytes(buf[:need])
            del buf[:need]
            if fr[1] in (TYPE_REPLY, TYPE_WRITE_ACK):
                out.append(Frame(fr[4] | (fr[5] << 8), fr[1], fr[6:]))
        return out


# ------------------------------------------------------------------ fields

@dataclass(frozen=True)
class Field:
    """One setting that is read with one command and written with another."""
    name: str
    get: int
    decode: Callable[[bytes], Any]
    get_args: bytes = b""
    status: bool = True          # the reply starts with a status byte
    set: Optional[int] = None    # None: the configurator never writes it
    encode: Optional[Callable[[Any], bytes]] = None


def _byte1(p):
    return p[1] if len(p) >= 2 and p[0] == 0 else None


def _flag1(p):
    v = _byte1(p)
    return None if v is None else v != 0


def _flag_loose(p):
    """A flag whose reply comes with or without a leading status byte."""
    if len(p) >= 2 and p[0] == 0:
        return p[1] != 0
    return p[0] != 0 if p else None


def _bool(v):
    return bytes([1 if v else 0])


def _rate(p):
    return _u16_at(p, 1) if len(p) >= 3 and p[0] == 0 else None


def _debounce(p):
    return (p[1], p[2], p[3]) if len(p) >= 4 and p[0] == 0 else None


def _rotate(p):
    if len(p) < 3 or p[0] != 0:
        return None
    angle = p[2] - 256 if p[2] > 90 else p[2]
    return (p[1] != 0, angle)


def _rotate_bytes(v):
    enabled, angle = v
    angle = max(-90, min(90, int(angle)))
    return bytes([1 if enabled else 0, angle & 0xFF])


def _sleep_sum(p, i):
    total = _u16_at(p, i) + _u16_at(p, i + 2) + _u16_at(p, i + 4)
    return -1 if total == 0xFFFF else total


def _sleep(p):
    """(2.4G seconds, Bluetooth seconds); -1 means never. The reply is 24
    bytes without a status byte or 25 with one."""
    if len(p) < 14:
        return None
    if len(p) == 25:
        off = 1
    elif len(p) == 24:
        off = 0
    else:
        off = 1 if len(p) >= 15 and p[0] == 0 else 0
    return (_sleep_sum(p, off), _sleep_sum(p, off + 8))


def _thirds(seconds):
    """The firmware stores a sleep time as three u16 that add up to it."""
    total = 0xFFFF if seconds == -1 else max(0, min(0xFFFF, int(seconds)))
    n = total // 3
    return u16(n) + u16(n) + u16(total - 2 * n)


def _sleep_bytes(v):
    s24, sbt = v
    return (_thirds(s24) + b"\x00\x00" + _thirds(sbt) + b"\x00\x00"
            + bytes([60, 0, 30, 0, 60, 0, 30, 0]))


def _hw_debounce(p):
    v = _byte1(p)
    return v if v is not None and 0 <= v <= 30 else None


FIELDS = {f.name: f for f in (
    Field("polling_rate", 12305, _rate, set=12304, encode=lambda hz: u16(hz)),
    Field("lod", 12493, _byte1, set=12492, encode=lambda v: bytes([v & 0xFF])),
    Field("motion_sync", 12499, _flag1, set=12498, encode=_bool),
    Field("angle_snap", 12501, _flag1, set=12500, encode=_bool),
    Field("ripple", 12503, _flag1, set=12502, encode=_bool),
    Field("fps_mode", 12511, _flag_loose, status=False, set=12510, encode=_bool),
    Field("profile_key", 12525, _flag_loose, status=False, set=12524,
          encode=_bool),
    Field("dpi_button", 12523, lambda p: None if _byte1(p) is None
          else _byte1(p) == 24, set=12522,
          encode=lambda on: bytes([24 if on else 0])),
    Field("debounce", 12521, _debounce, set=12520,
          encode=lambda v: bytes(x & 0xFF for x in v)),
    Field("rotate", 12505, _rotate, set=12504, encode=_rotate_bytes),
    Field("sleep", 12307, _sleep, status=False, set=12306, encode=_sleep_bytes),
    # Read-only: the configurator shows these and never writes them.
    Field("hw_debounce_press", 12491, _hw_debounce, get_args=b"\x00"),
    Field("hw_debounce_release", 12491, _hw_debounce, get_args=b"\x01"),
)}


# ------------------------------------------------------------------ parsers

def parse_battery(p):
    """getBattery / getDongleBattery -> [status, charging, percent, health,
    present]."""
    if len(p) < 5 or p[0] != 0:
        return None
    return {"percent": p[2], "charging": bool(p[1]), "present": bool(p[4])}


def parse_dpi(p):
    """getDpi -> [status, stage count, active stage, x u16 x N, y u16 x N].
    Current firmware always sends all eight stages."""
    if len(p) < 3 or p[0] != 0:
        return None
    count, active = p[1], p[2]
    n = DPI_STAGES if 0 < count <= DPI_STAGES and len(p) >= 3 + DPI_STAGES * 4 \
        else count
    xs = [_u16_at(p, 3 + 2 * i) for i in range(n)]
    ys = [_u16_at(p, 3 + 2 * n + 2 * i) for i in range(n)]
    return {"count": count, "active": active, "x": xs, "y": ys}


def set_dpi_stage(index, x, y):
    return bytes([index & 0xFF]) + u16(x) + u16(y)


def parse_key(p):
    """getKeyRemap -> (key id, type, params), or None for a refused read."""
    if len(p) < 3 or p[0] != 0:
        return None
    return p[1], p[2], bytes(p[3:])


def read_nv(nv_id):
    return u16(nv_id) + u16(NV_READ_LEN)


def parse_nv(p, nv_id):
    """readNvData -> [status, nv u16, length u16, data...]. The data, or
    None when the read failed or answers for another record."""
    if len(p) < 6 or p[0] != 0 or _u16_at(p, 1) != nv_id:
        return None
    n = _u16_at(p, 3)
    body = bytes(p[5:])
    return body[:n] if n > 0 else body


def save_dpi_colour(stage, rgb):
    """saveRgb data for one DPI stage's indicator colour: six copies of the
    colour followed by six dark entries, as the configurator writes it."""
    r, g, b = (c & 0xFF for c in rgb)
    pattern = bytes([r, g, b]) * 6 + bytes(18)
    return (u16(NV_DPI_COLOUR_BASE + stage) + bytes([33]) + u16(len(pattern))
            + bytes([16]) + pattern)


def sleep_record_r2(current16, sleep_24g, sleep_bt):
    """writeNvData data for the cabled .100R2's sleep record: the 16 bytes
    just read, with the times that are not None replaced."""
    if len(current16) < 16:
        raise AmError("sleep record is shorter than 16 bytes -- nothing written")
    rec = bytearray(current16[:16])
    for value, off in ((sleep_24g, 0), (sleep_bt, 8)):
        if value is not None:
            rec[off:off + 6] = _thirds(value)
    return u16(NV_SLEEP_R2) + bytes(rec)
