"""Resolution of the host page a partial request morphs out of band."""

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from next.forms.origin import resolve_origin, resolve_url_to_match
from next.forms.uid import validated_origin_path

from .headers import partial_intent


if TYPE_CHECKING:
    from pathlib import Path

    from django.http import HttpRequest


class OriginSource(enum.StrEnum):
    """Where the resolved host-page origin was taken from."""

    HEADER = "header"
    FORM = "form"


@dataclass(frozen=True, slots=True)
class PartialOrigin:
    """The host page an out-of-band morph addresses, with its URL kwargs.

    `page_path` names the page source whose zone a `done` handler morphs
    out of band, `url_kwargs` are that page's captured URL parameters, and
    `source` records whether the host page came from the `X-Next-Origin`
    header or fell back to the posted form origin.
    """

    page_path: "Path | None"
    url_kwargs: dict[str, object]
    origin: str
    source: OriginSource


def resolve_partial_origin(request: "HttpRequest") -> "PartialOrigin | None":
    """Resolve the host page of a partial request for an out-of-band morph.

    The `X-Next-Origin` header the runtime stamps with the host page URL
    wins, so a master rendered inside a layer morphs the zone of the page
    that owns the layer rather than the master's own step page. When the
    header is absent or does not resolve to a page the posted form origin
    is the fallback, which keeps the resolver usable from a master that
    posts straight from its host page. The header is validated same-site
    before it is trusted, so an off-site origin cannot redirect the morph.
    """
    intent = partial_intent(request)
    header = validated_origin_path(intent.origin)
    if header is not None:
        match = resolve_url_to_match(header, request)
        if match is not None:
            return PartialOrigin(
                page_path=match.page_path,
                url_kwargs=match.url_kwargs,
                origin=match.origin,
                source=OriginSource.HEADER,
            )
    form_match = resolve_origin(request)
    if form_match is None:
        return None
    return PartialOrigin(
        page_path=form_match.page_path,
        url_kwargs=dict(form_match.url_kwargs),
        origin=form_match.origin,
        source=OriginSource.FORM,
    )


__all__ = ["OriginSource", "PartialOrigin", "resolve_partial_origin"]
