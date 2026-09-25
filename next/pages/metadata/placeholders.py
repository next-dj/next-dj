"""Safe named placeholders for the title template chain."""

import functools
import string
from collections.abc import Mapping
from typing import Final

from django.utils.functional import lazy

from next.pages.errors import PageMetadataTemplateError

from .schema import Text


PLACEHOLDERS: Final[frozenset[str]] = frozenset({"title", "site_name"})
"""The only field names a title template may name."""

type Part = tuple[str, str | None]

_FORMATTER: Final = string.Formatter()


@functools.lru_cache(maxsize=256)
def parse_template(text: str) -> tuple[Part, ...]:
    """Split an evaluated template into literal text and placeholder names.

    The whitelist plus `isidentifier` rejects attribute and index access, and the bare
    field rule rejects conversions and specs, so nothing but the two names ever runs.
    """
    try:
        fields = tuple(_FORMATTER.parse(text))
    except ValueError as exc:
        raise PageMetadataTemplateError(text, f"is malformed, {exc}") from exc
    parts: list[Part] = []
    for literal, name, spec, conversion in fields:
        if name is None:
            parts.append((literal, None))
            continue
        if not name.isidentifier() or name not in PLACEHOLDERS:
            allowed = ", ".join(sorted(PLACEHOLDERS))
            detail = f"names the placeholder {name!r}, expected one of {allowed}"
            raise PageMetadataTemplateError(text, detail)
        if conversion is not None or spec:
            detail = f"formats the placeholder {name!r}, expected a bare {{{name}}}"
            raise PageMetadataTemplateError(text, detail)
        parts.append((literal, name))
    return tuple(parts)


def substitute_title(template: Text, values: Mapping[str, Text | None]) -> str:
    """Evaluate `template` under the active language and fill its placeholders."""
    text = str(template)
    pieces: list[str] = []
    for literal, name in parse_template(text):
        pieces.append(literal)
        if name is None:
            continue
        value = values.get(name)
        if value is None:
            detail = f"needs a value for {name!r}, which the chain did not provide"
            raise PageMetadataTemplateError(text, detail)
        pieces.append(str(value))
    return "".join(pieces)


title_lazy = lazy(substitute_title, str)
"""The substitution as a `Promise`, so a folded title stays lazy until rendered."""


def template_has_title(template: str) -> bool:
    """Whether the evaluated template names `{title}` at all."""
    return any(name == "title" for _, name in parse_template(template))


__all__ = [
    "PLACEHOLDERS",
    "Part",
    "parse_template",
    "substitute_title",
    "template_has_title",
    "title_lazy",
]
