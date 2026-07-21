"""Map bracket segments in file-based URL paths to Django converters.

The `URLPatternParser` turns a filesystem-style logical URL trail into
a Django path pattern. Bracket syntax `[name]` maps to `<str:name>`,
`[int:id]` maps to `<int:id>`, and `[[args]]` maps to `<path:args>`.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, ClassVar
from uuid import UUID


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


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
    """Coerce `value` to `hint`, passing it through on failure or unsupported hint."""
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


class DuplicateURLParameterError(ValueError):
    """Raised when bracket segments in one route conflict after normalisation.

    Covers a repeated normalised parameter name (`-` maps to `_`) and a
    second `[[wildcard]]` segment, both of which Django would otherwise
    reject only at resolve time or resolve ambiguously.
    """

    def __init__(
        self, param_name: str, url_path: str, file_path: Path | None = None
    ) -> None:
        """Build the message from the conflicting name and the source path."""
        self.param_name = param_name
        self.url_path = url_path
        self.file_path = file_path
        message = (
            f"Duplicate URL parameter '{param_name}' in URL pattern "
            f"'{url_path}'. Parameter names must be unique after '-' to '_' "
            "normalisation and a route can hold at most one [[wildcard]] "
            "segment."
        )
        if file_path is not None:
            message = f"{message} Page file: {file_path}."
        super().__init__(message)


class URLPatternParser:
    """Map bracket segments in a file-based path to Django path converters.

    The `url_path` string is the logical URL trail built from
    directory names. An empty string means the tree root. It is not a
    `pathlib.Path`. The on-disk file is the second value from the
    page-tree scanner.
    """

    duplicate_parameter_error: ClassVar[type[DuplicateURLParameterError]] = (
        DuplicateURLParameterError
    )

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
                name = wild.replace("-", "_")
                if wildcard_seen or name in parameters:
                    raise DuplicateURLParameterError(name, url_path)
                wildcard_seen = True
                parameters[name] = name
                return f"<path:{name}>"
            param_name, param_type = self._parse_param_name_and_type(
                match.group("param")
            )
            name = param_name.replace("-", "_")
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

        Lets diagnostics name every duplicate where `parse_url_pattern`
        raises on the first.
        """
        seen: set[str] = set()
        duplicates: list[str] = []
        for match in self._bracket_pattern.finditer(url_path):
            wild = match.group("wild")
            if wild is not None:
                name = wild.replace("-", "_")
            else:
                raw_name, _ = self._parse_param_name_and_type(match.group("param"))
                name = raw_name.replace("-", "_")
            if name in seen and name not in duplicates:
                duplicates.append(name)
            seen.add(name)
        return duplicates

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


__all__ = ["DuplicateURLParameterError", "URLPatternParser", "default_url_parser"]
