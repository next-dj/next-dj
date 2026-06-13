"""HTTP test client extensions for next-dj."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django.test import Client

from next.forms.uid import ORIGIN_FIELD_NAME

from .actions import resolve_action_url


if TYPE_CHECKING:
    from django.http import HttpResponse


class NextClient(Client):
    """Django test client with next-dj form-action shortcuts.

    `post_action` resolves an action name to its URL and POSTs data in
    a single call. `get_action_url` returns the URL without dispatching
    so tests can assert on structure before hitting the view.
    """

    def post_action(
        self,
        action_name: str,
        data: dict[str, Any] | None = None,
        *,
        origin: str | None = None,
        **extra: Any,  # noqa: ANN401
    ) -> HttpResponse:
        """Resolve `action_name` and POST `data` to the resulting URL.

        `origin` fills the `_next_form_origin` hidden field the form tag
        emits, unless `data` already carries one.
        """
        url = resolve_action_url(action_name)
        payload: dict[str, Any] = dict(data or {})
        if origin is not None:
            payload.setdefault(ORIGIN_FIELD_NAME, origin)
        return cast("HttpResponse", self.post(url, data=payload, **extra))

    def get_action_url(self, action_name: str) -> str:
        """Return the reverse URL for a registered form action."""
        return resolve_action_url(action_name)


__all__ = ["NextClient"]
