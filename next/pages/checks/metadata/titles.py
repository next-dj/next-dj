"""The system check that parses every title template under every language.

The ids are `next.E099` for a parse failure and `next.W084` for a missing `{title}`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, NamedTuple

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.utils import translation

from next.checks import NEXT
from next.pages.errors import PageMetadataTemplateError
from next.pages.metadata import SITE_SOURCE, template_has_title

from .pages import loaded_metadata_pages
from .scope import raw_metadata_scope, site_defaults


if TYPE_CHECKING:
    from next.pages.metadata import Segment, Text

    from .pages import MetadataPage


_TEMPLATE_HINT: Final = (
    "Write {title}, the %s placeholder of Next.js is not substituted here."
)


class _TitleTemplate(NamedTuple):
    """One title template with the source declaring it and the object it reports on."""

    source: str
    obj: object
    template: Text


def _title_templates(pages: list[MetadataPage]) -> list[_TitleTemplate]:
    """Return the title templates of the settings tier and of every page's own dict."""
    found: list[_TitleTemplate] = []
    scope = raw_metadata_scope()
    site = None if scope is None else site_defaults(scope)[0]
    sources: list[tuple[str, object, Segment | None]] = [
        (SITE_SOURCE, settings, site),
        *(
            (str(entry.page_path), str(entry.page_path), entry.segment)
            for entry in pages
        ),
    ]
    for source, obj, segment in sources:
        template = (
            None if segment is None or segment.title is None else segment.title.template
        )
        if template is not None:
            found.append(_TitleTemplate(source, obj, template))
    return found


def _language_codes() -> tuple[str | None, ...]:
    """Return the codes a template is evaluated under, or `None` alone without i18n."""
    if not settings.USE_I18N:
        return (None,)
    return tuple(code for code, _name in settings.LANGUAGES)


def _evaluated(template: Text, code: str | None) -> str:
    if code is None:
        return str(template)
    with translation.override(code):
        return str(template)


class _TemplateFinding(NamedTuple):
    """One failure or warning of a title template, keyed without the language."""

    item: _TitleTemplate
    text: str
    detail: str | None


def _template_findings(
    items: list[_TitleTemplate],
) -> dict[_TemplateFinding, list[str | None]]:
    """Evaluate every template under every language, grouping equal findings.

    A `detail` names the parse failure, and `None` marks a template without `{title}`.
    """
    findings: dict[_TemplateFinding, list[str | None]] = {}
    for item in items:
        for code in _language_codes():
            text = _evaluated(item.template, code)
            try:
                has_title = template_has_title(text)
            except PageMetadataTemplateError as exc:
                findings.setdefault(
                    _TemplateFinding(item, text, exc.detail), []
                ).append(code)
                continue
            if not has_title:
                findings.setdefault(_TemplateFinding(item, text, None), []).append(code)
    return findings


def _under_languages(codes: list[str | None]) -> str:
    """Name the languages a finding held under, unless it held under every one."""
    named = [repr(code) for code in codes if code is not None]
    if not named or len(named) == len(_language_codes()):
        return ""
    noun = "language" if len(named) == 1 else "languages"
    return f" under the {noun} {', '.join(named)}"


def _template_message(
    finding: _TemplateFinding, codes: list[str | None]
) -> CheckMessage:
    item = finding.item
    where = _under_languages(codes)
    if finding.detail is not None:
        return Error(
            f"{item.source} declares the title template {finding.text!r}{where}, "
            f"which {finding.detail}. Only {{title}} and {{site_name}} are "
            "substituted, as bare placeholders.",
            obj=item.obj,
            id="next.E099",
        )
    return DjangoWarning(
        f"{item.source} declares the title template {finding.text!r}{where}, which "
        "never names {title}, so every page under it renders the same title.",
        hint=_TEMPLATE_HINT,
        obj=item.obj,
        id="next.W084",
    )


@register(Tags.templates, NEXT)
def check_metadata_title_templates(*args, **kwargs) -> list[CheckMessage]:
    """Parse every title template under every language (`next.E099`, `next.W084`).

    The settings tier and each page's own dict are read, one finding per template.
    """
    init_errors, pages = loaded_metadata_pages()
    messages = list(init_errors)
    for finding, codes in _template_findings(_title_templates(pages)).items():
        messages.append(_template_message(finding, codes))
    return messages


__all__ = ["check_metadata_title_templates"]
