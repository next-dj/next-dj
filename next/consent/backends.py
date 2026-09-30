"""Where the consent of a visitor is read from, the first-party cookie by default."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, Final, override

from django.http import HttpRequest

from next.conf.defaults import DEFAULTS
from next.utils import is_int

from .markers import NECESSARY, UNDECIDED, Consent


COOKIE_VERSION: Final = "1"
"""The format the runtime writes the consent cookie in, `1:<categories>:<seconds>`."""

_COOKIE_PARTS: Final = 3
_COOKIE_DEFAULTS: Final[Mapping[str, object]] = DEFAULTS["CONSENT"]["OPTIONS"]


class ConsentBackend(ABC):
    """Reads the consent of a visitor from the request.

    The backend takes its whole `CONSENT` entry, `OPTIONS` holding its own settings.
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Store the `CONSENT` entry the backend was configured with."""
        self._config: Mapping[str, Any] = config or {}
        options = self._config.get("OPTIONS")
        self._options: Mapping[str, Any] = (
            options if isinstance(options, Mapping) else {}
        )

    @property
    def options(self) -> Mapping[str, Any]:
        """Return the `OPTIONS` mapping of the entry."""
        return self._options

    @abstractmethod
    def read(self, request: HttpRequest) -> Consent:
        """Return the consent the request carries, undecided when it carries none."""


class CookieConsentBackend(ConsentBackend):
    """Reads the first-party cookie the runtime writes, no server endpoint involved.

    The cookie is not `HttpOnly` since the runtime writes it on every choice.
    """

    def _option(self, name: str) -> object:
        return self._options.get(name, _COOKIE_DEFAULTS[name])

    @property
    def cookie_name(self) -> str:
        """Return the name of the consent cookie."""
        name = self._option("cookie_name")
        if isinstance(name, str) and name:
            return name
        return str(_COOKIE_DEFAULTS["cookie_name"])

    @override
    def read(self, request: HttpRequest) -> Consent:
        """Parse the cookie, answering undecided for a missing or foreign value."""
        raw = request.COOKIES.get(self.cookie_name)
        if not isinstance(raw, str):
            return UNDECIDED
        parts = raw.split(":")
        if len(parts) != _COOKIE_PARTS or parts[0] != COOKIE_VERSION:
            return UNDECIDED
        granted = {category for category in parts[1].split(",") if category}
        return Consent(frozenset({NECESSARY, *granted}), decided=True)

    def cookie(self) -> dict[str, object]:
        """Return the cookie the runtime writes, a None `secure` taking the scheme."""
        secure = self._option("secure")
        domain = self._option("domain")
        max_age = self._option("max_age")
        return {
            "name": self.cookie_name,
            "max_age": max_age if is_int(max_age) else _COOKIE_DEFAULTS["max_age"],
            "samesite": str(self._option("samesite")),
            "secure": secure if isinstance(secure, bool) else None,
            "domain": domain if isinstance(domain, str) and domain else None,
            "path": str(self._option("path") or "/"),
        }


__all__ = ["COOKIE_VERSION", "ConsentBackend", "CookieConsentBackend"]
