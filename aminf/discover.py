"""Find the connected mice and open the right driver for each."""
from __future__ import annotations

from aminf.errors import AmError, DeviceNotFound
from aminf.legacy100 import Am100
from aminf.models import MODELS
from aminf.race.device import RaceMouse


def _open(model, backend, prefer, dry_run):
    if model.family == "race":
        return RaceMouse.open(backend, model, prefer=prefer, dry_run=dry_run)
    return Am100.open(backend, prefer=prefer, dry_run=dry_run)


def _present(model, backend, prefer):
    if model.family == "race":
        return RaceMouse.discover(backend, model, prefer) is not None
    return Am100.present(backend, prefer)


def open_mice(backend, which="auto", prefer="auto", dry_run=False):
    """Every supported mouse that is connected, or just the one `which`
    names ("97", "100", "100r2"). Returns (mice, errors)."""
    wanted = list(MODELS.values())
    if which != "auto":
        wanted = [MODELS[which]]
    mice, errors = [], []

    if dry_run:
        # A dry run never opens a device. It previews for whatever is plugged
        # in; with nothing plugged in it previews for the named model, or
        # for the .97 as 2.2.0 did.
        found = [m for m in wanted if _present(m, backend, prefer)]
        if not found:
            found = [wanted[0]] if which != "auto" else [MODELS["97"]]
        return [_open(m, backend, prefer, True) for m in found], errors

    for model in wanted:
        try:
            mice.append(_open(model, backend, prefer, False))
        except DeviceNotFound:
            if which != "auto":
                errors.append(f"AM Infinity {model.name} not found.")
        except AmError as exc:
            errors.append(str(exc))
    return mice, errors
