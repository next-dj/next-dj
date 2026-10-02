"""Where the consent of a visitor is read from, the first-party cookie by default."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, Final, override

from django.http import HttpRequest

from next.conf.defaults import DEFAULTS
from next.utils import is_int

from .markers import NECESSARY, UNDECIDED, Consent


COOKIE_VERSION: Final = "2"
"""The format the runtime writes the consent cookie in, `2:<categories>:<seconds>`.

The granted categories are joined by `|`, a cookie-octet a category name never holds.
"""

_SEPARATORS: Final[Mapping[str, str]] = {COOKIE_VERSION: "|", "1": ","}
"""The category separator of every format the backend reads.

The first format joined the names with commas, which RFC 6265 keeps out of a cookie
value, and stays readable for the cookies browsers already hold.
"""

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

    def client_config(self) -> Mapping[str, object]:
        """Return the entries the backend adds to `$consent` for the runtime.

        The runtime keeps a choice only in its consent cookie, so a backend reading
        anything else gets nothing back from the browser. The default adds nothing,
        and the runtime then writes the cookie under its default name and age.
        """
        return {}


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
        separator = _SEPARATORS.get(parts[0])
        if len(parts) != _COOKIE_PARTS or separator is None:
            return UNDECIDED
        granted = {category for category in parts[1].split(separator) if category}
        return Consent(frozenset({NECESSARY, *granted}), decided=True)

    @override
    def client_config(self) -> Mapping[str, object]:
        """Name the cookie the runtime writes, under the `cookie` entry."""
        return {"cookie": self.cookie()}

    def cookie(self) -> dict[str, object]:
        """Return the cookie the runtime writes, a None `secure` taking the scheme."""
        secure = self._option("secure")
        domain = self._option("domain")
        max_age = self._option("max_age")
        samesite = self._option("samesite")
        return {
            "name": self.cookie_name,
            "max_age": max_age if is_int(max_age) else _COOKIE_DEFAULTS["max_age"],
            "samesite": samesite if isinstance(samesite, str) and samesite else None,
            "secure": secure if isinstance(secure, bool) else None,
            "domain": domain if isinstance(domain, str) and domain else None,
            "path": str(self._option("path") or "/"),
        }


__all__ = ["COOKIE_VERSION", "ConsentBackend", "CookieConsentBackend"]
