"""Map bracket segments in file-based URL paths to Django converters.

The `URLPatternParser` turns a filesystem-style logical URL trail into
a Django path pattern. Bracket syntax `[name]` maps to `<str:name>`,
`[int:id]` maps to `<int:id>`, and `[[args]]` maps to `<path:args>`.
"""

from __future__ import annotations

import re
from typing import ClassVar


def _coerce_url_value(value: str, hint: type) -> object:
    """Coerce a URL string toward `int`, `bool`, `float`, or `str`.

    Returns the original string when conversion fails so the caller
    can decide how to react. The helper is shared by `DUrl` and
    `DQuery` parameter resolution.
    """
    if hint is int:
        try:
            return int(value)
        except ValueError:
            return value
    if hint is bool:
        return value.lower() in ("1", "true", "yes")
    if hint is float:
        try:
            return float(value)
        except ValueError:
            return value
    return value


class URLPatternParser:
    """Map bracket segments in a file-based path to Django path converters.

    The `url_path` string is the logical URL trail built from
    directory names. An empty string means the tree root. It is not a
    `pathlib.Path`. The on-disk file is the second value from the
    page-tree scanner.
    """

    _param_pattern: ClassVar[re.Pattern[str]] = re.compile(r"\[([^\[\]]+)\]")
    _args_pattern: ClassVar[re.Pattern[str]] = re.compile(r"\[\[([^\[\]]+)\]\]")

    def parse_url_pattern(self, url_path: str) -> tuple[str, dict[str, str]]:
        """Return the Django path string and parameter names for `url_path`."""
        django_pattern = url_path
        parameters: dict[str, str] = {}

        if args_match := self._args_pattern.search(django_pattern):
            args_name = args_match.group(1)
            django_args_name = args_name.replace("-", "_")
            django_pattern = self._args_pattern.sub(
                f"<path:{django_args_name}>",
                django_pattern,
            )
            parameters[django_args_name] = django_args_name

        param_matches = self._param_pattern.findall(url_path)
        for param_str in param_matches:
            param_name, param_type = self._parse_param_name_and_type(param_str)
            django_param_name = param_name.replace("-", "_")
            django_pattern = django_pattern.replace(
                f"[{param_str}]",
                f"<{param_type}:{django_param_name}>",
            )
            parameters[django_param_name] = django_param_name

        if django_pattern and not django_pattern.endswith("/"):
            django_pattern = f"{django_pattern}/"

        return django_pattern, parameters

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


__all__ = ["URLPatternParser", "default_url_parser"]
