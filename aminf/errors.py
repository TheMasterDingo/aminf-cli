"""Every error aminf raises on purpose."""


class AmError(RuntimeError):
    """Base class: a command failed for a reason worth telling the user."""


class DeviceNotFound(AmError):
    pass


class DeviceBusy(AmError):
    pass


class MouseOffline(AmError):
    """The dongle answers but the mouse behind it does not."""


class Unsupported(AmError):
    """This model has no such feature. Reported as a notice, not a failure."""


class NotInFirmware(AmError):
    """The model has the feature but this unit's firmware does not answer."""


class Refused(AmError):
    """Blocked before anything was sent to the device."""


class NotConfirmed(AmError):
    """A write the device did not confirm when read back."""

    def __init__(self, field, wanted, got):
        super().__init__(f"{field}: wrote {wanted!r} but the device reports {got!r}")
        self.field, self.wanted, self.got = field, wanted, got
