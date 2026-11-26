"""In-memory HID backends for tests."""
from aminf.transport import Hid


class EmptyBackend(Hid):
    """A machine with no Angry Miao device plugged in."""

    def enumerate(self):
        return []

    def open(self, path):
        raise AssertionError("nothing to open")
