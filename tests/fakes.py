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
