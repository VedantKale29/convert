"""WCAG contrast check for the design tokens: every text/background pair the compiler emits must pass AA.

If the UI team changes a colour to something unreadable, tests (and /v1/ready) catch it before any user does."""

from .config import tokens

AA_NORMAL_TEXT = 4.5

# (foreground, background, where the compiler uses it). "#FFFFFF" = white text on a coloured fill.
PAIRS = [
    ("text", "background", "page text"),
    ("text", "surface", "card text"),
    ("muted", "background", "muted text"),
    ("muted", "surface", "muted text in cards"),
    ("primary", "surface", "links, active tab"),
    ("primary", "background", "links on the page"),
    ("danger", "background", "error text"),
    ("warning", "background", "placeholder text"),
    ("#FFFFFF", "primary", "primary button, active pill"),
    ("#FFFFFF", "danger", "danger button, error badge"),
    ("#FFFFFF", "success", "success badge"),
    ("#FFFFFF", "warning", "warning badge"),
    ("#FFFFFF", "navDark", "dark navbar / sidebar"),
]


def _luminance(hex_color):
    h = hex_color.lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(fg, bg):
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return round((hi + 0.05) / (lo + 0.05), 2)


def check_tokens(colors=None):
    """List of pairs below WCAG AA (empty = all good)."""
    colors = colors or tokens()["colors"]

    def resolve(c):
        return c if c.startswith("#") else colors[c]

    failures = []
    for fg, bg, use in PAIRS:
        r = ratio(resolve(fg), resolve(bg))
        if r < AA_NORMAL_TEXT:
            failures.append(f"{use}: {fg} on {bg} is {r}:1 (needs {AA_NORMAL_TEXT}:1)")
    return failures
