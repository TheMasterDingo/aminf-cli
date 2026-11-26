"""The one interface every mouse offers, whichever protocol it speaks."""
from __future__ import annotations

from abc import ABC, abstractmethod

# Every model groups its settings into the same sections, so the command
# line, export/import and the hardware test tool are written once.
SECTIONS = ("sensor", "power", "dpi", "buttons", "light", "dongle_light")


class Mouse(ABC):
    model = None           # aminf.models.Model
    connection = "dongle"  # "dongle" or "usb"
    dry_run = False
    offline = False        # set once the dongle reports the mouse away

    @abstractmethod
    def read(self, section):
        """The section as a dict, or None when this mouse has no such
        section on this connection."""

    @abstractmethod
    def write(self, section, changes, strict=True):
        """Apply a partial dict, confirm it by reading back, and return the
        confirmed values. strict=False skips the model's command-line ranges
        (protocol-field bounds still apply); only import uses it."""

    @abstractmethod
    def battery(self):
        ...

    @abstractmethod
    def hub_battery(self):
        ...

    @abstractmethod
    def profile(self):
        ...

    @abstractmethod
    def set_profile(self, index):
        ...

    @abstractmethod
    def close(self):
        ...
