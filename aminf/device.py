"""The one interface every mouse offers, whichever protocol it speaks."""
from __future__ import annotations

from abc import ABC, abstractmethod

from aminf.errors import AmError, Unsupported

# Every model groups its settings into the same sections, so the command
# line, export/import and the hardware test tool are written once.
SECTIONS = ("sensor", "power", "dpi", "buttons", "light", "dongle_light")


# Every key some model has in each section. A key from this table that the
# connected model lacks is "not on this mouse" (a notice); anything else is a
# typo (an error).
KNOWN_KEYS = {
    "sensor": {"polling_rate", "lod", "motion_sync", "angle_snap", "ripple",
               "fps_mode", "angle", "angle_enabled", "profile_key"},
    "power": {"sleep_24g", "sleep_bt", "deep_sleep_24g", "deep_sleep_bt",
              "debounce_wired", "debounce_24g", "debounce_bt",
              "hw_debounce_press", "hw_debounce_release"},
    "dpi": {"stages", "count", "active", "button"},
    "light": {"on", "effect", "speed", "colour", "colour_charged"},
    "dongle_light": {"on", "effect", "colour", "speed", "brightness", "hue1",
                     "sat1", "hue2", "sat2", "hue3", "sat3", "preset"},
}


def reject_key(section, key, model):
    if key in KNOWN_KEYS.get(section, ()):
        raise Unsupported(f"the {model.name} has no '{key}' setting")
    raise AmError(f"{section}: unknown setting '{key}'")


class Mouse(ABC):
    model = None           # aminf.models.Model
    connection = "dongle"  # "dongle" or "usb"
    dry_run = False
    offline = False        # set once the dongle reports the mouse away
    # What a profile switch does besides switching, for the user to be told.
    profile_switch_note = None

    def forget_offline(self):
        """Try the mouse again after it was found asleep (the user says it
        is awake now)."""
        self.offline = False

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
