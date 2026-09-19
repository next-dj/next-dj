import re

from django.db import models


ACCENT_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_ACCENT = "#0f172a"


class TenantTheme(models.TextChoices):
    SHARED = "shared", "Shared default"
    ACME = "acme", "Acme blue"


THEME_STYLESHEETS: dict[str, str] = {
    TenantTheme.SHARED: "",
    TenantTheme.ACME: "notes/css/acme.css",
}


def theme_stylesheet(key: str) -> str:
    """Return the extra stylesheet name the stored theme key adds, if any.

    The shared default adds nothing, and a key outside the table falls back to it.
    """
    return THEME_STYLESHEETS.get(key, THEME_STYLESHEETS[TenantTheme.SHARED])


def accent_color(value: str) -> str:
    """Return the stored accent only when it is a bare hex colour.

    The value lands in an inline `style` attribute, where any other shape smuggles CSS.
    """
    return value if ACCENT_PATTERN.match(value) else DEFAULT_ACCENT
