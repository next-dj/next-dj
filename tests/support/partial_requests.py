from __future__ import annotations

from django.test import RequestFactory

from next.forms.manager import form_action_manager
from next.forms.uid import ORIGIN_FIELD_NAME
from next.partial.headers import ORIGIN, REQUEST_FLAG


_ACTION_PATH = "/_next/form/x/"


def partial_meta(host: str | None = None) -> dict[str, str]:
    """Return WSGI META that flags a request as partial, optional host origin.

    Pass `host` to stamp the `X-Next-Origin` header with a host page URL.
    """
    meta = {f"HTTP_{REQUEST_FLAG.upper().replace('-', '_')}": "1"}
    if host is not None:
        meta[f"HTTP_{ORIGIN.upper().replace('-', '_')}"] = host
    return meta


def partial_request(
    origin: str | None = "/zoned/",
    host: str | None = None,
):
    """Return a partial POST whose form origin resolves to a real page.

    Pass `origin=None` to post a partial that names no resolvable origin
    page and `host` to stamp the `X-Next-Origin` host page URL.
    """
    data = {} if origin is None else {ORIGIN_FIELD_NAME: origin}
    return RequestFactory().post(_ACTION_PATH, data=data, **partial_meta(host))


def plain_request(origin: str = "/zoned/"):
    """Return a non-partial POST whose form origin resolves to a real page."""
    return RequestFactory().post(_ACTION_PATH, data={ORIGIN_FIELD_NAME: origin})


def plain_get(path: str):
    """Return a plain GET request for a page URL."""
    return RequestFactory().get(path)


def action_uid(action_name: str) -> str:
    """Return the registered uid of a form action by name."""
    meta = form_action_manager.require_action_meta(action_name)
    return str(meta["uid"])
