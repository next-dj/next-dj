"""Patch envelope value objects, the request-bound builder, and PatchResponse."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme

from next.static.collector import default_placeholders
from next.static.serializers import resolve_serializer

from .backends import partial_backend_manager
from .headers import (
    CONTENT_TYPE,
    RESPONSE_VERSION,
    is_partial_request,
    set_partial_vary,
)
from .registry import patch_op_registry


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from django.http import HttpResponseBase

    from .render import ZoneRenderResult


HTML_VERBS: frozenset[str] = frozenset({"replace", "inner"})
TARGET_KEYS: frozenset[str] = frozenset({"zone", "form", "field", "css"})

_SEE_OTHER = 303


class UnknownPatchOpError(LookupError):
    """Raised when the builder is asked to emit an unregistered verb.

    The runtime guard pairs with the `next.E066` check, so an unknown
    verb fails fast in `op()` rather than reaching the client.
    """

    def __init__(self, name: str) -> None:
        """Store the unknown verb name and build a readable message."""
        self.name = name
        super().__init__(
            f'Patch op "{name}" is not registered. Register it with '
            "register_patch_op() before emitting it."
        )


class ForeignPageNotAuthorizedError(PermissionError):
    """Raised when an OOB morph names a foreign page that denies the request.

    A `morph(page=...)` re-runs the foreign page's body resolution before
    rendering its zone, so the zone never travels in the master's response
    when the page would have redirected or denied the caller on its own
    request. The denial is surfaced rather than swallowed into an empty
    morph so the master path can answer with a clear shape.
    """

    def __init__(self, page_path: "Path", status_code: int) -> None:
        """Store the page path and the short-circuit status code."""
        self.page_path = page_path
        self.status_code = status_code
        super().__init__(
            f"Page {page_path} did not authorize an out-of-band zone morph, "
            f"its body resolution short-circuited with status {status_code}. "
            "The zone of a foreign page is rendered only when that page would "
            "have served the request."
        )


class UnknownContextNameError(LookupError):
    """Raised when `context()` names a value that is not a serialize provider.

    Only the names of registered `serialize=True` context providers may
    travel in a context patch, so an arbitrary mapping is rejected at the
    builder rather than serialized blind.
    """

    def __init__(self, name: str) -> None:
        """Store the rejected name and build a readable message."""
        self.name = name
        super().__init__(
            f'Context name "{name}" is not a registered serialize=True '
            "provider on the origin page. Mark its @context provider "
            "serialize=True or drop it from the patch."
        )


@dataclass(frozen=True, slots=True)
class Patch:
    """One addressed DOM operation of a patch envelope.

    A patch carries a verb, an optional target object with a single key,
    optional HTML payload, and any verb-specific extras.
    """

    op: str
    target: "Mapping[str, Any] | None" = None
    html: str | None = None
    extras: "Mapping[str, Any]" = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the wire form of the patch as an ordered mapping."""
        data: dict[str, Any] = {"op": self.op}
        if self.target is not None:
            data["target"] = dict(self.target)
        if self.html is not None:
            data["html"] = self.html
        data.update(self.extras)
        return data


@dataclass(frozen=True, slots=True)
class Asset:
    """One co-located asset of a rendered target, by kind and URL."""

    kind: str
    url: str

    def as_dict(self) -> dict[str, str]:
        """Return the wire form of the asset entry."""
        return {"kind": self.kind, "url": self.url}


@dataclass(frozen=True, slots=True)
class DeferZone:
    """One zone the client should fetch next, with a load trigger."""

    zone: str
    trigger: str = "load"

    def as_dict(self) -> dict[str, str]:
        """Return the wire form of the defer entry."""
        return {"zone": self.zone, "trigger": self.trigger}


@dataclass(frozen=True, slots=True)
class FormMeta:
    """Machine-readable state of a form built from its field specs."""

    uid: str
    valid: bool
    errors: "Mapping[str, Sequence[str]]" = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the wire form of the form meta object."""
        return {
            "uid": self.uid,
            "valid": self.valid,
            "errors": {name: list(msgs) for name, msgs in self.errors.items()},
        }


@dataclass(frozen=True, slots=True)
class Envelope:
    """A patch envelope carrying ordered ops and protocol meta.

    Every field but `version` is optional, an absent value is empty on
    the wire. The `csrf` and `request_id` meta are stamped only when set
    so the wire shape stays stable whether or not they travel.
    """

    version: str
    ops: "Sequence[Patch]" = ()
    assets: "Sequence[Asset]" = ()
    defer: "Sequence[DeferZone]" = ()
    form: "FormMeta | None" = None
    csrf: "Mapping[str, Any] | None" = None
    request_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the wire form of the envelope as an ordered mapping."""
        data: dict[str, Any] = {
            "version": self.version,
            "ops": [op.as_dict() for op in self.ops],
            "assets": [asset.as_dict() for asset in self.assets],
            "defer": [zone.as_dict() for zone in self.defer],
            "form": self.form.as_dict() if self.form is not None else None,
        }
        if self.csrf is not None:
            data["csrf"] = dict(self.csrf)
        if self.request_id is not None:
            data["request_id"] = self.request_id
        return data


class Patches:
    """Request-bound builder of a patch envelope.

    Built from a request, the builder takes its asset version from the
    active protocol backend and resolves the origin page lazily, so a
    `morph(zone=...)` or `morph(component=...)` renders against the page
    that owns the request. Built from a bare version string the builder
    stays a low-level envelope assembler with no request, used by paths
    that already hold the version and render their own HTML.
    """

    def __init__(
        self,
        target: "HttpRequest | str",
        *,
        echo_of: str | None = None,
    ) -> None:
        """Start an empty builder from a request or a literal version.

        Pass `echo_of` with the originating mutation's request id so the
        envelope carries it as `request_id`, letting an SSE subscriber
        suppress its own echo. Only the stream path passes it, the HTTP
        response path leaves it unset since the answer already reaches the
        initiator.
        """
        if isinstance(target, str):
            self._request = None
            self._version: str = target
        else:
            self._request = target
            self._version = partial_backend_manager.get().version()
        self._ops: list[Patch] = []
        self._assets: list[Asset] = []
        self._defer: list[DeferZone] = []
        self._form: FormMeta | None = None
        self._csrf: Mapping[str, Any] | None = None
        self._request_id: str | None = echo_of
        self._page_path: Path | None = None
        self._page_resolved = False

    @property
    def version(self) -> str:
        """Return the asset version stamped on the envelope."""
        return self._version

    def morph(  # noqa: PLR0913
        self,
        target: "Mapping[str, Any] | None" = None,
        html: str | None = None,
        *,
        zone: str | None = None,
        page: "Path | str | None" = None,
        url_kwargs: "Mapping[str, Any] | None" = None,
        form: str | None = None,
        component: str | None = None,
        props: "Mapping[str, Any] | None" = None,
        overrides: "Mapping[str, Any] | None" = None,
        extract: bool = False,
    ) -> "Patches":
        """Morph a target into HTML, the default verb.

        The named argument selects how the HTML and target are produced.
        `zone` renders the named zone of the origin page, with optional
        context `overrides`. Pass `page` alongside `zone` to render the
        named zone of a foreign page out of band, addressed by its page
        path or a URL of it, with that page's `url_kwargs`. The foreign
        page's body resolution is re-run first so its guards, denials, and
        redirects are honoured before its zone travels in this response.
        `component` renders the named component with `props`. `form`
        extract-morphs the form addressed by its uid. `html` morphs a
        passed target into ready HTML. `extract` marks `html` as a whole
        document the client trims down to the node matching the target.
        """
        if zone is not None and page is not None:
            return self._morph_foreign_zone(zone, page, url_kwargs)
        if zone is not None:
            return self._morph_zone(zone, overrides)
        if component is not None:
            return self._morph_component(component, props)
        if form is not None:
            return self._append_morph({"form": form}, html or "", extract=True)
        return self._append_morph(dict(target or {}), html or "", extract=extract)

    def _append_morph(
        self,
        target: "Mapping[str, Any]",
        html: str,
        *,
        extract: bool,
    ) -> "Patches":
        """Record one morph op with an optional extract flag."""
        extras = {"extract": True} if extract else {}
        self._ops.append(
            Patch(op="morph", target=dict(target), html=html, extras=extras)
        )
        return self

    def _morph_zone(
        self,
        zone: str,
        overrides: "Mapping[str, Any] | None",
    ) -> "Patches":
        """Render the named zone of the origin page and morph it in place."""
        result = self._render_zone(zone, overrides)
        self._collect_assets(result)
        return self._append_morph({"zone": zone}, result.html[zone], extract=False)

    def _morph_foreign_zone(
        self,
        zone: str,
        page: "Path | str",
        url_kwargs: "Mapping[str, Any] | None",
    ) -> "Patches":
        """Render a zone of a foreign page out of band, re-running its guards.

        The page is named by its page path or by a URL of it, which is
        resolved through the URLconf to the page that serves it. The
        foreign page's body resolution runs first, so a redirect or a
        denial short-circuits before any zone renders and raises instead
        of morphing an empty body. With the page authorized, the named
        zone renders standalone with the foreign page's URL kwargs and
        morphs in place addressed by zone name.
        """
        request = self._require_request()
        foreign_path = self._foreign_page_path(page)
        kwargs = dict(url_kwargs or {})
        denial = self._foreign_authorization(foreign_path, request, kwargs)
        if denial is not None:
            raise ForeignPageNotAuthorizedError(foreign_path, denial.status_code)
        result = self._render_foreign_zone(foreign_path, zone, request, kwargs)
        self._collect_assets(result)
        return self._append_morph({"zone": zone}, result.html[zone], extract=False)

    def _foreign_page_path(self, page: "Path | str") -> "Path":
        """Return the page path named by a path or a URL of the foreign page."""
        if isinstance(page, Path):
            return page
        resolved = self._page_path_for_url(page)
        if resolved is None:
            msg = f'No page resolves the URL "{page}" for an out-of-band morph.'
            raise LookupError(msg)
        return resolved

    def _page_path_for_url(self, url: str) -> "Path | None":
        """Resolve a URL of a foreign page to its page path through the URLconf."""
        from next.forms.origin import _page_path_from_url  # noqa: PLC0415

        return _page_path_from_url(url, self._require_request())

    def _foreign_authorization(
        self,
        foreign_path: "Path",
        request: HttpRequest,
        url_kwargs: dict[str, Any],
    ) -> "HttpResponseBase | None":
        """Re-run the foreign page's body resolution and return its short-circuit."""
        from next.pages import page  # noqa: PLC0415

        return page.authorization_response(foreign_path, request, **url_kwargs)

    def _render_foreign_zone(
        self,
        foreign_path: "Path",
        zone: str,
        request: HttpRequest,
        url_kwargs: dict[str, Any],
    ) -> "ZoneRenderResult":
        """Render the named zone of an already authorized foreign page."""
        from .render import render_zone  # noqa: PLC0415

        return render_zone(foreign_path, (zone,), request, url_kwargs=url_kwargs)

    def _morph_component(
        self,
        name: str,
        props: "Mapping[str, Any] | None",
    ) -> "Patches":
        """Render the named component with props and morph it in place."""
        html = self._render_component(name, props)
        return self._append_morph({"component": name}, html, extract=False)

    def replace(self, target: "Mapping[str, Any]", html: str) -> "Patches":
        """Replace the target node wholesale with the given HTML."""
        self._ops.append(Patch(op="replace", target=dict(target), html=html))
        return self

    def inner(self, target: "Mapping[str, Any]", html: str) -> "Patches":
        """Replace only the contents of the target with the given HTML."""
        self._ops.append(Patch(op="inner", target=dict(target), html=html))
        return self

    def append(
        self,
        target: "Mapping[str, Any]",
        html: str,
        *,
        dedupe: str = "key",
    ) -> "Patches":
        """Append children to the target, deduplicating by key or id."""
        return self._merge("append", target, html, dedupe)

    def prepend(
        self,
        target: "Mapping[str, Any]",
        html: str,
        *,
        dedupe: str = "key",
    ) -> "Patches":
        """Prepend children to the target, deduplicating by key or id."""
        return self._merge("prepend", target, html, dedupe)

    def _merge(
        self,
        op: str,
        target: "Mapping[str, Any]",
        html: str,
        dedupe: str,
    ) -> "Patches":
        """Record a merge op appending or prepending deduplicated children."""
        self._ops.append(
            Patch(
                op=op,
                target=dict(target),
                html=html,
                extras={"dedupe": dedupe},
            )
        )
        return self

    def remove(self, target: "Mapping[str, Any]") -> "Patches":
        """Remove the target node."""
        self._ops.append(Patch(op="remove", target=dict(target)))
        return self

    def refresh(self, *, zone: str) -> "Patches":
        """Ask the client to refetch the named zone with its own cookies."""
        self._ops.append(Patch(op="refresh", extras={"zone": zone}))
        return self

    def context(self, **names: Any) -> "Patches":  # noqa: ANN401
        """Merge named serialize provider values into the client context.

        Only the names of registered `serialize=True` providers on the
        origin page are accepted. The values are serialized through
        `resolve_serializer()` so the wire carries plain data.
        """
        allowed = self._serializable_names()
        serializer = resolve_serializer()
        data: dict[str, Any] = {}
        for name, value in names.items():
            if name not in allowed:
                raise UnknownContextNameError(name)
            data[name] = json.loads(serializer.dumps(value))
        self._ops.append(Patch(op="context", extras={"data": data}))
        return self

    def layer_close(
        self,
        *,
        result: Any = None,  # noqa: ANN401
        dismiss: str | None = None,
    ) -> "Patches":
        """Close the top layer with an accept result or a dismissal."""
        extras: dict[str, Any] = {}
        if result is not None:
            extras["result"] = result
        if dismiss is not None:
            extras["dismiss"] = dismiss
        self._ops.append(Patch(op="layer.close", extras=extras))
        return self

    def toast(self, text: str, variant: str = "info") -> "Patches":
        """Show a toast, sugar over an event with a built-in container."""
        self._ops.append(Patch(op="toast", extras={"text": text, "variant": variant}))
        return self

    def event(self, name: str, detail: "Mapping[str, Any] | None" = None) -> "Patches":
        """Dispatch a CustomEvent on document and the `Next.on` bus."""
        self._ops.append(
            Patch(op="event", extras={"name": name, "detail": dict(detail or {})})
        )
        return self

    def push_url(self, href: str) -> "Patches":
        """Push the validated href onto the browser history."""
        self._ops.append(
            Patch(op="url", extras={"action": "push", "href": self._safe_url(href)})
        )
        return self

    def redirect(self, href: str, *, external: bool = False) -> "Patches":
        """Drive a full client navigation to a server-authored href.

        An internal href is validated against the request host. An
        external href is sent with a full-navigation marker so a
        server-authored redirect like OAuth or a payment gateway is not
        rejected by the same-host validator.
        """
        if external:
            self._ops.append(Patch(op="visit", extras={"href": href, "external": True}))
        else:
            self._ops.append(Patch(op="visit", extras={"href": self._safe_url(href)}))
        return self

    def op(self, name: str, **payload: Any) -> "Patches":  # noqa: ANN401
        """Emit a custom verb registered through `register_patch_op`."""
        if not patch_op_registry.is_registered(name):
            raise UnknownPatchOpError(name)
        self._ops.append(Patch(op=name, extras=dict(payload)))
        return self

    def add_asset(self, kind: str, url: str) -> "Patches":
        """Record a co-located asset in the envelope manifest."""
        self._assets.append(Asset(kind=kind, url=url))
        return self

    def defer_zone(self, zone: str, trigger: str = "load") -> "Patches":
        """Mark a zone the client should fetch next."""
        self._defer.append(DeferZone(zone=zone, trigger=trigger))
        return self

    def set_form(self, form: FormMeta) -> "Patches":
        """Attach the machine-readable form meta to the envelope."""
        self._form = form
        return self

    def set_csrf(self, csrf: "Mapping[str, Any]") -> "Patches":
        """Attach the rotated CSRF payload so the runtime refreshes tokens."""
        self._csrf = dict(csrf)
        return self

    def envelope(self) -> Envelope:
        """Return the assembled envelope value object."""
        return Envelope(
            version=self._version,
            ops=tuple(self._ops),
            assets=tuple(self._assets),
            defer=tuple(self._defer),
            form=self._form,
            csrf=self._csrf,
            request_id=self._request_id,
        )

    def response(self, fallback: str | None = None) -> "PatchResponse | HttpResponse":
        """Assemble the response for the current request.

        With the partial switch on the request the envelope travels as a
        `PatchResponse`. Without the switch, mutation falls back to the
        full cycle: a 303 to the request origin when no `fallback` is
        given, or a redirect to `fallback` when it is.
        """
        request = self._request
        if request is not None and is_partial_request(request):
            backend = partial_backend_manager.get()
            body = backend.serialize_envelope(self.envelope())
            return PatchResponse(
                body, content_type=backend.content_type, version=self._version
            )
        target = fallback if fallback is not None else self._origin_path()
        return HttpResponseRedirect(target, status=_SEE_OTHER)

    def _origin_path(self) -> str:
        """Return the path the no-runtime fallback redirects to."""
        request = self._request
        if request is None:
            return "/"
        from next.forms.origin import _resolve_origin  # noqa: PLC0415

        match = _resolve_origin(request)
        if match is not None:
            return match.origin
        return request.path

    def _require_request(self) -> HttpRequest:
        """Return the bound request or raise when the builder has none."""
        if self._request is None:
            msg = "This builder operation needs a request-bound Patches(request)."
            raise RuntimeError(msg)
        return self._request

    def _resolve_page_path(self) -> "Path":
        """Resolve the origin page path of the request, memoised on the builder."""
        if self._page_resolved:
            if self._page_path is None:
                msg = "The request origin does not resolve to a page."
                raise RuntimeError(msg)
            return self._page_path
        request = self._require_request()
        from next.forms.origin import _resolve_origin  # noqa: PLC0415

        match = _resolve_origin(request)
        self._page_resolved = True
        self._page_path = match.page_path if match is not None else None
        return self._resolve_page_path()

    def _origin_url_kwargs(self) -> dict[str, object]:
        """Return the URL kwargs of the origin page for a zone or component render."""
        request = self._require_request()
        from next.forms.origin import _resolve_origin  # noqa: PLC0415

        match = _resolve_origin(request)
        return dict(match.url_kwargs) if match is not None else {}

    def _render_zone(
        self,
        zone: str,
        overrides: "Mapping[str, Any] | None",
    ) -> "ZoneRenderResult":
        """Render the named zone of the origin page with optional overrides."""
        from .render import render_zone  # noqa: PLC0415

        return render_zone(
            self._resolve_page_path(),
            (zone,),
            self._require_request(),
            url_kwargs=self._origin_url_kwargs(),
            overrides=dict(overrides) if overrides else None,
        )

    def _render_component(
        self,
        name: str,
        props: "Mapping[str, Any] | None",
    ) -> str:
        """Render the named component of the origin page with props."""
        from next.components import get_component, render_component  # noqa: PLC0415

        request = self._require_request()
        page_path = self._resolve_page_path()
        info = get_component(name, page_path)
        if info is None:
            msg = f'Component "{name}" is not visible to the origin page.'
            raise LookupError(msg)
        context_data: dict[str, Any] = dict(props or {})
        return render_component(info, context_data, request)

    def _serializable_names(self) -> frozenset[str]:
        """Return the serialize=True provider names of the origin page."""
        from next.pages import page  # noqa: PLC0415

        request = self._require_request()
        context_data = page.build_render_context(
            self._resolve_page_path(), request, **self._origin_url_kwargs()
        )
        js_context = context_data.get("_next_js_context", {})
        return frozenset(js_context) if isinstance(js_context, dict) else frozenset()

    def _collect_assets(self, result: "ZoneRenderResult") -> None:
        """Record the assets a rendered zone body collected on the envelope."""
        for slot in default_placeholders:
            for static_asset in result.collector.assets_in_slot(slot.name):
                if static_asset.url:
                    self.add_asset(static_asset.kind, static_asset.url)

    def _safe_url(self, href: str) -> str:
        """Return `href` if it is same-site, else fall back to the origin path."""
        request = self._require_request()
        allowed = {request.get_host()}
        if url_has_allowed_host_and_scheme(
            href, allowed_hosts=allowed, require_https=request.is_secure()
        ):
            return href
        return self._origin_path()


class PatchResponse(HttpResponse):
    """HTTP response that carries a serialized patch envelope.

    The response is an `HttpResponse` subclass so it passes the handler
    normalisation contract that requires rich return types to subclass
    `HttpResponse`. The body bytes and content type come from the active
    protocol backend, the partial Vary headers are set on construction.
    """

    def __init__(
        self,
        body: bytes,
        *,
        content_type: str = CONTENT_TYPE,
        version: str | None = None,
        status: int = 200,
    ) -> None:
        """Build the response from serialized envelope bytes."""
        super().__init__(content=body, content_type=content_type, status=status)
        if version is not None:
            self[RESPONSE_VERSION] = version
        set_partial_vary(self)


__all__ = [
    "Asset",
    "DeferZone",
    "Envelope",
    "ForeignPageNotAuthorizedError",
    "FormMeta",
    "Patch",
    "PatchResponse",
    "Patches",
    "UnknownContextNameError",
    "UnknownPatchOpError",
]
