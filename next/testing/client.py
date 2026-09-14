"""HTTP test client extensions for next-dj."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django.test import Client

from next.forms.uid import ORIGIN_FIELD_NAME
from next.partial import keys
from next.partial.envelope import Envelope
from next.partial.headers import REQUEST_FLAG, VERSION, ZONE
from next.partial.manager import partial_backend_manager

from .actions import resolve_action_url


if TYPE_CHECKING:
    from django.http import HttpResponse


class PartialEnvelope:
    """Structural view over a decoded patch envelope for test assertions.

    The payload is rebuilt into the producer's own envelope objects, so the helpers
    answer from named fields instead of a second reading of the wire.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        """Wrap the decoded envelope mapping, rebuilt on the first read of it."""
        self.data = data
        self._parsed: Envelope | None = None

    def _envelope(self) -> Envelope:
        """Return the envelope objects behind the mapping, built once per view.

        Deferred rather than built in the constructor, so a view over a mapping no
        assertion reads costs nothing and a malformed one is refused where it is read.
        """
        if self._parsed is None:
            self._parsed = Envelope.from_dict(self.data)
        return self._parsed

    def _addressed(self, selector: str) -> list[str]:
        return [
            patch.target[selector]
            for patch in self._envelope().ops
            if isinstance(patch.target, dict) and selector in patch.target
        ]

    @property
    def version(self) -> str:
        """Return the asset version stamped in the envelope."""
        return self._envelope().version

    @property
    def ops(self) -> list[dict[str, Any]]:
        """Return the ordered list of op objects."""
        return [patch.as_dict() for patch in self._envelope().ops]

    @property
    def assets(self) -> list[dict[str, Any]]:
        """Return the asset manifest entries."""
        return [asset.as_dict() for asset in self._envelope().assets]

    def op_verbs(self) -> list[str]:
        """Return the verb of every op in order."""
        return [patch.op for patch in self._envelope().ops]

    def targets(self) -> list[dict[str, Any] | None]:
        """Return the target object of every op in order."""
        return [
            None if patch.target is None else dict(patch.target)
            for patch in self._envelope().ops
        ]

    def zone_targets(self) -> list[str]:
        """Return the zone name of every op that addresses a zone, in order."""
        return self._addressed(keys.ZONE)

    def form_targets(self) -> list[str]:
        """Return the form uid of every op that addresses a form, in order."""
        return self._addressed(keys.FORM_SELECTOR)

    def form_meta(self) -> dict[str, Any] | None:
        """Return the machine-readable form meta object of the envelope."""
        form = self._envelope().form
        return None if form is None else form.as_dict()

    def toasts(self) -> list[dict[str, Any]]:
        """Return the payload of every toast op in order."""
        return [
            patch.as_dict() for patch in self._envelope().ops if patch.op == "toast"
        ]

    def html_for_zone(self, zone: str) -> str:
        """Return the HTML payload of the op morphing the named zone."""
        for patch in self._envelope().ops:
            target = patch.target
            if isinstance(target, dict) and target.get(keys.ZONE) == zone:
                return patch.html or ""
        msg = f"no op targets zone {zone!r}"
        raise AssertionError(msg)


def envelope_of(response: HttpResponse) -> PartialEnvelope:
    """Return the structural envelope view of a partial response.

    Raises when the response is not a patch envelope, so a navigation fallback never
    silently passes a structural assertion.
    """
    backend = partial_backend_manager.get()
    content_type = response["Content-Type"].split(";")[0].strip()
    if content_type != backend.content_type:
        msg = f"response is not a patch envelope, content type is {content_type!r}"
        raise AssertionError(msg)
    return PartialEnvelope(backend.deserialize_envelope(response.content).as_dict())


class NextClient(Client):
    """Django test client with next-dj form-action shortcuts.

    `post_action` POSTs to a resolved action name, `get_action_url` resolves without
    dispatching, and `get_zones` GETs a URL as a partial zone request.
    """

    def post_action(
        self,
        action_name: str,
        data: dict[str, Any] | None = None,
        *,
        origin: str | None = None,
        partial: bool = False,
        zones: str | tuple[str, ...] | None = None,
        version: str | None = None,
        headers: dict[str, str] | None = None,
        **extra,
    ) -> HttpResponse:
        """Resolve `action_name` and POST `data` to the resulting URL.

        `origin` fills the `_next_form_origin` hidden field unless `data` carries one,
        and `partial` stamps `X-Next-Request` with `zones` and `version`.
        """
        url = resolve_action_url(action_name)
        payload: dict[str, Any] = dict(data or {})
        if origin is not None:
            payload.setdefault(ORIGIN_FIELD_NAME, origin)
        sent = dict(headers or {})
        if partial:
            sent[REQUEST_FLAG] = "1"
        if zones is not None:
            sent[ZONE] = zones if isinstance(zones, str) else ",".join(zones)
        if version is not None:
            sent[VERSION] = version
        return cast("HttpResponse", self.post(url, data=payload, headers=sent, **extra))

    def get_action_url(self, action_name: str) -> str:
        """Return the reverse URL for a registered form action."""
        return resolve_action_url(action_name)

    def get_zones(
        self,
        url: str,
        zones: str | tuple[str, ...],
        *,
        version: str | None = None,
        headers: dict[str, str] | None = None,
        **extra,
    ) -> HttpResponse:
        """GET `url` as a partial request for the named zones.

        `zones` joins into the `X-Next-Zone` header, and `version` sets the client
        asset version header so tests can drive the version-sync branch.
        """
        names = zones if isinstance(zones, str) else ",".join(zones)
        sent = dict(headers or {})
        sent[REQUEST_FLAG] = "1"
        sent[ZONE] = names
        if version is not None:
            sent[VERSION] = version
        return cast("HttpResponse", self.get(url, headers=sent, **extra))


__all__ = ["NextClient", "PartialEnvelope", "envelope_of"]
