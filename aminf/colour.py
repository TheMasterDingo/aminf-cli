"""Colour conversions shared by every model."""

from aminf.errors import AmError


class NotAColour(AmError, ValueError):
    """Not a #RRGGBB colour. Also a ValueError, as it was in 2.2.0."""

HUE_MAX = 1530


def hex_to_rgb(text):
    t = text.strip().lstrip("#")
    if len(t) == 3:
        t = "".join(c * 2 for c in t)
    if len(t) != 6 or any(c not in "0123456789abcdefABCDEF" for c in t):
        raise NotAColour(f"not a hex colour: {text} (use #RRGGBB)")
    return int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)


def hex_to_hue_sat(text):
    """The dongle stores hue (0-1530) and saturation (1-100), not RGB."""
    r, g, b = (v / 255 for v in hex_to_rgb(text))
    hi, lo = max(r, g, b), min(r, g, b)
    c = hi - lo
    if c == 0:
        h = 0.0
    elif hi == r:
        h = ((g - b) / c) % 6
    elif hi == g:
        h = (b - r) / c + 2
    else:
        h = (r - g) / c + 4
    deg = (h * 60) % 360
    sat = 0 if hi == 0 else round(c / hi * 100)
    return round(deg / 360 * HUE_MAX), max(1, min(100, sat))


def hue_to_hex(raw):
    t = max(0, min(HUE_MAX, int(raw)))
    if t <= 255:
        r, g, b = 255, t, 0
    elif t <= 510:
        r, g, b = 510 - t, 255, 0
    elif t <= 765:
        r, g, b = 0, 255, t - 510
    elif t <= 1020:
        r, g, b = 0, 1020 - t, 255
    elif t <= 1275:
        r, g, b = t - 1020, 0, 255
    else:
        r, g, b = 255, 0, HUE_MAX - t
    return f"#{r:02X}{g:02X}{b:02X}"


def rgb_to_hex(r, g, b):
    return f"#{r & 0xFF:02X}{g & 0xFF:02X}{b & 0xFF:02X}"
