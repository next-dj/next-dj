"""The signal each rendered navigation sends and the `DEBUG` reports of its cut."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from django.conf import settings

from next.caches import FORK, ROUTES, BoundedCache
from next.pages.loaders import layout_chain, layout_ids, template_source
from next.pages.placeholder import placeholder_spans
from next.pages.responses import depth_of, purpose_of
from next.partial.headers import NAVIGATE
from next.router.markers import Cut, CutKind, Divergence
from next.router.signals import navigation_rendered

from .state import Routing


if TYPE_CHECKING:
    from pathlib import Path

    from django.http import HttpRequest
    from django.http.response import HttpResponseBase


logger = logging.getLogger("next.router")

_noted: Final = BoundedCache[str, bool](depends=(ROUTES, FORK))


def describe(cut: Cut) -> str:
    """Return the DEBUG value of `X-Next-Navigate` naming the cut."""
    if cut.kind is CutKind.SLOT:
        text = f"kept {','.join(cut.kept)} slot {cut.slot}"
    elif cut.kind is CutKind.BODY:
        text = "body_morph"
    else:
        text = f"visit {cut.reason or 'id'}"
    if cut.diverged_at is not None and cut.kind is not CutKind.VISIT:
        text += f", diverged {cut.diverged_at} {cut.reason}"
    return text


def debug_cut(response: HttpResponseBase, cut: Cut) -> None:
    """Name the cut on `response` under `DEBUG`."""
    if settings.DEBUG:
        response[NAVIGATE] = describe(cut)


def _layout_file(page_path: Path, ident: str) -> Path | None:
    """Return the layout of `page_path` whose id is `ident`, `None` for none.

    It pairs the ids with the layouts that hold a placeholder, as `layout_ids` does.
    """
    slotted = [
        layout
        for layout in reversed(layout_chain(page_path))
        if (text := template_source(layout)) is not None and placeholder_spans(text)
    ]
    return next(
        (
            layout
            for layout, found in zip(slotted, layout_ids(page_path), strict=False)
            if found == ident
        ),
        None,
    )


def _note_divergence(page_path: Path, cut: Cut) -> None:
    """Log at INFO, once per layout until the routes reload, a moved layout digest."""
    ident = cut.diverged_at
    if cut.reason is not Divergence.DIGEST or ident is None or ident in _noted:
        return
    _noted[ident] = True
    logger.info(
        "The chrome of the layout %s differs on the navigation to %s, so the client "
        "router cannot keep it (%s). Wrap the part the pages set differently in a "
        "{%% zone %%} or move it into the page slot, as next.W183 names.",
        _layout_file(page_path, ident) or ident,
        page_path,
        describe(cut),
    )


def announce(
    request: HttpRequest, page_path: Path, cut: Cut, *, streamed: bool
) -> None:
    """Send `navigation_rendered` for one navigation or prefetch.

    Under `DEBUG` it also logs the first divergence of each layout by its digest.
    """
    if settings.DEBUG:
        _note_divergence(page_path, cut)
    if navigation_rendered.has_listeners(Routing):
        navigation_rendered.send_robust(
            sender=Routing,
            page_path=page_path,
            purpose=purpose_of(request),
            depth=depth_of(request),
            cut=cut.kind,
            kept=cut.kept,
            diverged_at=cut.diverged_at,
            reason=cut.reason,
            streamed=streamed,
            request=request,
        )


__all__ = ["announce", "debug_cut", "describe"]
