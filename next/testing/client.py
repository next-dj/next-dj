"""HTTP test client extensions for next-dj."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from django.test import Client

from next.forms.uid import ORIGIN_FIELD_NAME
from next.partial.headers import CONTENT_TYPE, REQUEST_FLAG, ZONE

from .actions import resolve_action_url


if TYPE_CHECKING:
    from django.http import HttpResponse


_PARTIAL_HEADER = f"HTTP_{REQUEST_FLAG.upper().replace('-', '_')}"
_ZONE_HEADER = f"HTTP_{ZONE.upper().replace('-', '_')}"


class PartialEnvelope:
    """Structural view over a decoded patch envelope for test assertions.

    The helpers read the JSON envelope and answer questions about ops
    and their targets without any HTML regex, so tests assert the server
    contract on structure alone.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        """Wrap the decoded envelope mapping."""
        self.data = data

    @property
    def version(self) -> str:
        """Return the asset version stamped in the envelope."""
        return cast("str", self.data["version"])

    @property
    def ops(self) -> list[dict[str, Any]]:
        """Return the ordered list of op objects."""
        return cast("list[dict[str, Any]]", self.data.get("ops", []))

    @property
    def assets(self) -> list[dict[str, Any]]:
        """Return the asset manifest entries."""
        return cast("list[dict[str, Any]]", self.data.get("assets", []))

    def op_verbs(self) -> list[str]:
        """Return the verb of every op in order."""
        return [op["op"] for op in self.ops]

    def targets(self) -> list[dict[str, Any] | None]:
        """Return the target object of every op in order."""
        return [op.get("target") for op in self.ops]

    def zone_targets(self) -> list[str]:
        """Return the zone name of every op that addresses a zone, in order."""
        return [
            op["target"]["zone"]
            for op in self.ops
            if isinstance(op.get("target"), dict) and "zone" in op["target"]
        ]

    def html_for_zone(self, zone: str) -> str:
        """Return the HTML payload of the op morphing the named zone."""
        for op in self.ops:
            target = op.get("target")
            if isinstance(target, dict) and target.get("zone") == zone:
                return cast("str", op.get("html", ""))
        msg = f"no op targets zone {zone!r}"
        raise AssertionError(msg)


def envelope_of(response: HttpResponse) -> PartialEnvelope:
    """Return the structural envelope view of a partial response.

    Raises when the response is not a patch envelope, so a navigation
    fallback never silently passes a structural assertion.
    """
    content_type = response["Content-Type"].split(";")[0].strip()
    if content_type != CONTENT_TYPE:
        msg = f"response is not a patch envelope, content type is {content_type!r}"
        raise AssertionError(msg)
    return PartialEnvelope(json.loads(response.content.decode()))


class NextClient(Client):
    """Django test client with next-dj form-action shortcuts.

    `post_action` resolves an action name to its URL and POSTs data in
    a single call. `get_action_url` returns the URL without dispatching
    so tests can assert on structure before hitting the view. `get_zones`
    GETs a URL as a partial zone request.
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

    def get_zones(
        self,
        url: str,
        zones: str | tuple[str, ...],
        *,
        version: str | None = None,
        **extra: Any,  # noqa: ANN401
    ) -> HttpResponse:
        """GET `url` as a partial request for the named zones.

        `zones` is one name or a tuple of names joined into the
        `X-Next-Zone` header. `version` sets the client asset version
        header so tests can drive the version-sync branch.
        """
        names = zones if isinstance(zones, str) else ",".join(zones)
        headers: dict[str, Any] = {_PARTIAL_HEADER: "1", _ZONE_HEADER: names}
        if version is not None:
            headers["HTTP_X_NEXT_VERSION"] = version
        headers.update(extra)
        return cast("HttpResponse", self.get(url, **headers))


__all__ = ["NextClient", "PartialEnvelope", "envelope_of"]
