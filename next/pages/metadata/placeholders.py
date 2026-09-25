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

    The whitelist and the bare-field rule refuse attribute, index, conversion and spec.
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


def template_names(template: str) -> frozenset[str]:
    """Return the placeholder names the evaluated template carries."""
    return frozenset(name for _, name in parse_template(template) if name is not None)


def template_has_title(template: str) -> bool:
    """Whether the evaluated template names `{title}` at all."""
    return "title" in template_names(template)


def _title_or_bare(template: Text, text: Text, site_name: Text | None) -> str:
    """Fill the template, or answer the bare text when it wants an absent site name.

    The template is parsed after translation, so the decision has to wait for `str()`.
    """
    evaluated = str(template)
    if site_name is None and "site_name" in template_names(evaluated):
        return str(text)
    return substitute_title(evaluated, {"title": text, "site_name": site_name})


_title_or_bare_lazy = lazy(_title_or_bare, str)


def apply_title_template(
    template: Text | None, text: Text, *, site_name: Text | None
) -> Text:
    """Return `text` under the chain template, still lazy until it is rendered."""
    if template is None:
        return text
    return _title_or_bare_lazy(template, text, site_name)


__all__ = [
    "PLACEHOLDERS",
    "apply_title_template",
    "parse_template",
    "substitute_title",
    "template_has_title",
    "template_names",
]
