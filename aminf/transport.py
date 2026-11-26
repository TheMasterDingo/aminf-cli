"""HID access behind a small interface, so devices can be tested without one."""
from __future__ import annotations

import sys
from dataclasses import dataclass

from aminf.errors import DeviceBusy


@dataclass(frozen=True)
class HidInfo:
    path: bytes
    vendor_id: int
    product_id: int
    usage_page: int
    usage: int
    interface: int
    product: str


class HidHandle:
    """One open HID interface."""

    def send_feature(self, data: bytes) -> None:
        """Send a feature report; data[0] is the report id. Raises OSError."""
        raise NotImplementedError

    def get_feature(self, report_id: int, length: int) -> bytes:
        """Read a feature report, report id first. Raises OSError."""
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class Hid:
    """A source of HID interfaces."""

    def enumerate(self) -> list[HidInfo]:
        raise NotImplementedError

    def open(self, path: bytes) -> HidHandle:
        """Open one interface. Raises DeviceBusy when it cannot be opened."""
        raise NotImplementedError


class _HidapiHandle(HidHandle):
    def __init__(self, dev):
        self._dev = dev

    def send_feature(self, data):
        n = self._dev.send_feature_report(bytes(data))
        if n is not None and n < 0:
            raise OSError(self._dev.error() or "send_feature_report failed")

    def get_feature(self, report_id, length):
        return bytes(self._dev.get_feature_report(report_id, length))

    def close(self):
        try:
            self._dev.close()
        except Exception:
            pass


class HidapiBackend(Hid):
    """The real thing, through the hidapi package."""

    def __init__(self):
        try:
            import hid
        except ImportError:                                  # pragma: no cover
            sys.exit("hidapi is not installed:  pip install hidapi")
        self._hid = hid

    def enumerate(self):
        try:
            entries = self._hid.enumerate()
        except Exception:
            return []
        return [HidInfo(path=d["path"], vendor_id=d.get("vendor_id") or 0,
                        product_id=d.get("product_id") or 0,
                        usage_page=d.get("usage_page") or 0,
                        usage=d.get("usage") or 0,
                        interface=d.get("interface_number", -1),
                        product=d.get("product_string") or "")
                for d in entries]

    def open(self, path):
        dev = self._hid.device()
        try:
            dev.open_path(path)
        except Exception as exc:
            raise DeviceBusy(str(exc)) from exc
        return _HidapiHandle(dev)
