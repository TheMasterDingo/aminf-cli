"""What differs between the three mice, as data. No I/O here."""
from __future__ import annotations

from dataclasses import dataclass

from aminf.actions import Action

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
    hw_debounce: bool = False    # reports hardware debounce (read-only)
    sleep_nv_on_cable: bool = False   # cabled: sleep lives in an NV record
    profile_to_dongle: bool = False   # receiver keeps its own active profile
    layer_switch: bool = False   # a button can be made to switch profile

    def default_action(self, button):
        """What `default` means for a button: its own function. For the
        .100R2's bottom button that is the layer switch, which is what the
        configurator's own "restore default" writes."""
        if button == "bottom":
            return Action("profile-switch")
        return Action("mouse", button)


MODELS = {m.key: m for m in (
    Model(".97", "97", "race",
          usb_ids=((0x0E8D, 0x0880), (0x35A1, 0x0035)),
          dongle_ids=((0x0E8D, 0x0703),),
          buttons=BUTTONS_7, profiles=3, lod_max=2, dpi_max=30000),
    Model(".100R2", "100r2", "race",
          usb_ids=((0x0E8D, 0x0881),),
          dongle_ids=((0x0E8D, 0x0704),),
          buttons=BUTTONS_7 + ("bottom",), profiles=3, lod_max=4,
          dpi_max=30000, hw_debounce=True, sleep_nv_on_cable=True,
          profile_to_dongle=True, layer_switch=True),
    Model(".100", "100", "m100",
          usb_ids=((0x3151, 0x402A),),
          dongle_ids=((0x3151, 0x5007),),
          buttons=BUTTONS_7, profiles=2, lod_max=2, dpi_max=26000),
)}
