"""What differs between the three mice, as data. No I/O here."""
from __future__ import annotations

from dataclasses import dataclass

DPI_MIN = 50
DPI_STEP = 50

BUTTONS_7 = ("left", "right", "middle", "forward", "back",
             "wheel-up", "wheel-down")


@dataclass(frozen=True)
class Model:
    name: str                    # shown to the user: ".97", ".100", ".100R2"
    key: str                     # the --mouse value: "97", "100", "100r2"
    family: str                  # "race" (.97, .100R2) or "m100" (.100)
    usb_ids: tuple               # ((vid, pid), ...) of the mouse on a cable
    dongle_ids: tuple            # ((vid, pid), ...) of the 2.4G receiver
    buttons: tuple               # button names in key-id order
    profiles: int                # onboard profiles (0-based on the CLI)
    lod_max: int                 # lift-off distance runs 0..lod_max
    dpi_max: int                 # the configurator's ceiling, in DPI


MODELS = {m.key: m for m in (
    Model(".97", "97", "race",
          usb_ids=((0x0E8D, 0x0880), (0x35A1, 0x0035)),
          dongle_ids=((0x0E8D, 0x0703),),
          buttons=BUTTONS_7, profiles=3, lod_max=2, dpi_max=30000),
    Model(".100R2", "100r2", "race",
          usb_ids=((0x0E8D, 0x0881),),
          dongle_ids=((0x0E8D, 0x0704),),
          buttons=BUTTONS_7 + ("bottom",), profiles=3, lod_max=4,
          dpi_max=30000),
    Model(".100", "100", "m100",
          usb_ids=((0x3151, 0x402A),),
          dongle_ids=((0x3151, 0x5007),),
          buttons=BUTTONS_7, profiles=2, lod_max=2, dpi_max=26000),
)}
