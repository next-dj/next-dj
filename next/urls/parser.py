"""Map bracket segments in file-based URL paths to Django converters.

`URLPatternParser` turns a filesystem-style trail into a Django path pattern: `[name]`
maps to `<str:name>`, `[int:id]` to `<int:id>`, and `[[args]]` to `<path:args>`.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, ClassVar
from uuid import UUID

from next.utils import normalise_route_name

from .errors import (
    DuplicateURLParameterError,
    InvalidURLParameterError,
    URLParameterError,
)


if TYPE_CHECKING:
    from collections.abc import Callable


def _coerce_bool(text: str) -> bool:
    return text.lower() in ("1", "true", "yes")


_COERCERS: dict[type, Callable[[str], object]] = {
    str: str,
    int: int,
    bool: _coerce_bool,
    float: float,
    UUID: UUID,
    Decimal: Decimal,
    datetime: datetime.fromisoformat,
    date: date.fromisoformat,
}


def _coerce_url_value(value: object, hint: object) -> object:
    """Coerce `value` to `hint`, passing it through on failure or unsupported hint.

    The annotation is a hint and not a gate. A typed directory such as `[int:id]`
    refuses a malformed segment with a 404 and a query string passes no converter at
    all, so raising here would answer a bad parameter with a 500.
    """
    if not isinstance(hint, type):
        return value
    if isinstance(value, hint):
        return value
    coercer = _COERCERS.get(hint)
    if coercer is None:
        return value
    text = value if isinstance(value, str) else str(value)
    try:
        return coercer(text)
    except (ValueError, InvalidOperation):
        return value


class URLPatternParser:
    """Map bracket segments in a file-based path to Django path converters.

    An empty `url_path` means the tree root, it is a logical trail of directory names
    and no `pathlib.Path`, and the on-disk file comes from the page-tree scanner.
    """

    parameter_error: ClassVar[type[URLParameterError]] = URLParameterError

    # The wildcard alternative must come first so `[[x]]` never matches
    # the single-bracket branch with a `[` inside the captured name.
    _bracket_pattern: ClassVar[re.Pattern[str]] = re.compile(
        r"\[\[(?P<wild>[^\[\]]+)\]\]|\[(?P<param>[^\[\]]+)\]"
    )

    def parse_url_pattern(self, url_path: str) -> tuple[str, dict[str, str]]:
        """Return the Django path string and parameter names for `url_path`."""
        parameters: dict[str, str] = {}
        wildcard_seen = False

        def _convert(match: re.Match[str]) -> str:
            nonlocal wildcard_seen
            wild = match.group("wild")
            if wild is not None:
                name = self._route_name(wild, url_path)
                if wildcard_seen or name in parameters:
                    raise DuplicateURLParameterError(name, url_path)
                wildcard_seen = True
                parameters[name] = name
                return f"<path:{name}>"
            param_name, param_type = self._parse_param_name_and_type(
                match.group("param")
            )
            name = self._route_name(param_name, url_path)
            if name in parameters:
                raise DuplicateURLParameterError(name, url_path)
            parameters[name] = name
            return f"<{param_type}:{name}>"

        django_pattern = self._bracket_pattern.sub(_convert, url_path)

        if django_pattern and not django_pattern.endswith("/"):
            django_pattern = f"{django_pattern}/"

        return django_pattern, parameters

    def duplicate_parameter_names(self, url_path: str) -> list[str]:
        """Return normalised bracket names repeated within `url_path`.

        Lets diagnostics name every duplicate where `parse_url_pattern` raises on one.
        """
        seen: set[str] = set()
        duplicates: list[str] = []
        for match in self._bracket_pattern.finditer(url_path):
            wild = match.group("wild")
            if wild is not None:
                name = normalise_route_name(wild)
            else:
                raw_name, _ = self._parse_param_name_and_type(match.group("param"))
                name = normalise_route_name(raw_name)
            if name in seen and name not in duplicates:
                duplicates.append(name)
            seen.add(name)
        return duplicates

    def _route_name(self, raw_name: str, url_path: str) -> str:
        """Return the Django route name for a bracket name, refusing a bad one."""
        name = normalise_route_name(raw_name)
        if not name.isidentifier():
            raise InvalidURLParameterError(name, url_path)
        return name

    def _parse_param_name_and_type(self, param_str: str) -> tuple[str, str]:
        """Split bracket text into a name and converter label (default `str`)."""
        if ":" in param_str:
            type_name, param_name = param_str.split(":", 1)
            return param_name.strip(), type_name.strip()
        return param_str.strip(), "str"

    _name_sep_pattern: ClassVar[re.Pattern[str]] = re.compile(r"[/\[\]:\-_]+")

    def prepare_url_name(self, url_path: str) -> str:
        """Python-safe name for `reverse` from a filesystem-style `url_path`."""
        return self._name_sep_pattern.sub("_", url_path).strip("_")


default_url_parser: URLPatternParser = URLPatternParser()


__all__ = [
    "DuplicateURLParameterError",
    "InvalidURLParameterError",
    "URLParameterError",
    "URLPatternParser",
    "default_url_parser",
]
