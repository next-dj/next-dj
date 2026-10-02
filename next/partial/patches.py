"""The request-bound patch envelope builder and its PatchResponse."""

import html
import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, overload
from urllib.parse import urljoin, urlsplit

from django.http import HttpRequest, HttpResponse
from django.utils.functional import Promise
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import SafeData

from next.diagnostics import INTENDED_EXCEPTIONS, FailureLog
from next.forms.origin import resolve_origin, resolve_url_to_page
from next.forms.uid import redirect_or_fallback, validated_origin_path
from next.pages import page as page_manager
from next.pages.metadata import resolve_metadata
from next.pages.metadata.fold import fold_metadata
from next.pages.metadata.normalize import normalize_metadata
from next.pages.metadata.scope import site_segment
from next.pages.visits import visit_request
from next.seeding import JS_CONTEXT_KEY
from next.static.assets import default_kinds
from next.static.manager import default_manager
from next.static.runtime import RESERVED_PAYLOAD_KEYS
from next.static.serializers import resolve_serializer

from . import keys
from .envelope import Asset, Envelope, FormMeta, Patch
from .errors import (
    BuiltinPatchOpError,
    CrossSiteHrefError,
    DynamicForeignPageError,
    ForeignPageNotAuthorizedError,
    LayerHrefWithoutZoneError,
    ReservedContextKeyError,
    ReservedEventNameError,
    UnknownContextNameError,
    UnknownDedupeError,
    UnknownPatchOpError,
)
from .headers import (
    CONTENT_TYPE,
    RESPONSE_VERSION,
    is_partial_request,
    set_partial_vary,
)
from .manager import asset_version, partial_backend_manager
from .registry import BUILTIN_OPS, patch_op_registry
from .render import render_zone


if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.http import HttpResponseBase

    from next.forms.origin import OriginMatch
    from next.pages.metadata import Metadata, MetadataDict, Text

    from .render import ZoneRenderResult


_failures = FailureLog(logging.getLogger(__name__))
# A denied guard or a 404 of an inherited callable answers the action, never a log.
_META_PASSES: tuple[type[BaseException], ...] = (
    *INTENDED_EXCEPTIONS,
    ForeignPageNotAuthorizedError,
)

_SEE_OTHER = 303
_NO_STORE = "private, no-store"
_META_SOURCE = "Patches.meta"

# Framework-owned bus events, refused to event() so an app cannot forge one.
_RESERVED_EVENT_NAMES: frozenset[str] = frozenset({"ready", "context-updated"})
_RESERVED_EVENT_PREFIXES: tuple[str, ...] = ("partial:", "next:")

DedupeMode = Literal["key", "id"]
_DEDUPE_MODES: frozenset[str] = frozenset({"key", "id"})

_FOREIGN_ZONE_UNUSED: tuple[str, ...] = ("target", "html", "form", "overrides")
_ZONE_UNUSED: tuple[str, ...] = ("target", "html", "form", "url_kwargs")
_FORM_UNUSED: tuple[str, ...] = ("target", "overrides", "page", "url_kwargs")
_TARGET_UNUSED: tuple[str, ...] = ("overrides", "page", "url_kwargs")


def _plain(text: "Text | None") -> str | None:
    """Return head text as the plain string the client writes, entities decoded."""
    if text is None:
        return None
    value = str(text)
    return html.unescape(value) if isinstance(value, SafeData) else value


def _requestless(folded: "Metadata") -> "Metadata":
    """Drop what only a request resolves, a self canonical and the unsent URL blocks.

    The client leaves a head tag alone when its key is missing from the operation.
    """
    canonical = None if folded.canonical is True else folded.canonical
    return replace(
        folded,
        canonical=canonical,
        alternates=None,
        og=None,
        twitter=None,
        icons=(),
        manifest=None,
        links=(),
        jsonld=(),
    )


def _local_url(base: str | None, href: str) -> str:
    """Return the path and query `href` names, read against the page at `base`."""
    parts = urlsplit(urljoin(base or "/", href))
    return f"{parts.path}?{parts.query}" if parts.query else parts.path


def _is_reserved_event(name: str) -> bool:
    """Return True when the name belongs to the framework client-bus channel."""
    return name in _RESERVED_EVENT_NAMES or name.startswith(_RESERVED_EVENT_PREFIXES)


class Patches:
    """Request-bound builder of a patch envelope.

    The origin page resolves lazily, so a `morph(zone=...)` renders against the page
    that owns the request, and `versioned` serves paths that already hold the version.
    """

    def __init__(
        self,
        request: HttpRequest | None,
        *,
        version: str | None = None,
        echo_of: str | None = None,
    ) -> None:
        """Start an empty builder bound to the request, or to no request at all.

        `version` pins the asset version literally instead of resolving it. `echo_of`
        carries the originating request id so an SSE subscriber can suppress its echo.
        """
        self._request = request
        self._version = asset_version() if version is None else version
        self._ops: list[Patch] = []
        self._assets: list[Asset] = []
        self._form: FormMeta | None = None
        self._csrf: Mapping[str, Any] | None = None
        self._request_id: str | None = echo_of
        self._origin: OriginMatch | None = None
        self._origin_resolved = False
        self._origin_authorized = False
        self._render_context: dict[str, object] | None = None

    @classmethod
    def versioned(
        cls,
        version: str,
        *,
        echo_of: str | None = None,
        request: HttpRequest | None = None,
    ) -> "Patches":
        """Start an empty builder stamped with a literal version.

        Used by paths that already hold the version and render their own HTML. Pass
        `request` when available so asset URLs stay scoped per request.
        """
        return cls(request, version=version, echo_of=echo_of)

    @property
    def version(self) -> str:
        """Return the asset version stamped on the envelope."""
        return self._version

    @overload
    def morph(self, target: "Mapping[str, Any]", html: str = "") -> "Patches": ...

    @overload
    def morph(
        self, *, zone: str, overrides: "Mapping[str, Any] | None" = None
    ) -> "Patches": ...

    @overload
    def morph(
        self,
        *,
        zone: str,
        page: "Path | str",
        url_kwargs: "Mapping[str, Any] | None" = None,
    ) -> "Patches": ...

    @overload
    def morph(self, *, form: str, html: str) -> "Patches": ...

    def morph(
        self,
        target: "Mapping[str, Any] | None" = None,
        html: str | None = None,
        *,
        zone: str | None = None,
        overrides: "Mapping[str, Any] | None" = None,
        page: "Path | str | None" = None,
        url_kwargs: "Mapping[str, Any] | None" = None,
        form: str | None = None,
    ) -> "Patches":
        """Morph a target into HTML, the default verb.

        A thin facade over the typed per-verb morph methods. A keyword belonging to
        another route raises here rather than being silently dropped.
        """
        if zone is not None and page is not None:
            self._refuse(_FOREIGN_ZONE_UNUSED, target, html, form, overrides)
            return self.morph_foreign_zone(zone, page, url_kwargs=url_kwargs)
        if zone is not None:
            self._refuse(_ZONE_UNUSED, target, html, form, url_kwargs)
            return self.morph_zone(zone, overrides=overrides)
        if form is not None:
            self._refuse(_FORM_UNUSED, target, overrides, page, url_kwargs)
            return self.morph_form(form, html or "")
        self._refuse(_TARGET_UNUSED, overrides, page, url_kwargs)
        if not target:
            msg = "morph() needs a target mapping, or a zone or form selector."
            raise TypeError(msg)
        return self._append_morph(target, html or "", extract=False)

    @staticmethod
    def _refuse(names: tuple[str, ...], *unused: object) -> None:
        """Raise when a morph route is passed a keyword another route owns.

        The empty case is every morph's hot path, so it returns before naming anything.
        """
        if not any(unused):
            return
        named = sorted(name for name, value in zip(names, unused, strict=True) if value)
        msg = f"morph() got {named}, which the selected route does not accept."
        raise TypeError(msg)

    def _append_morph(
        self, target: "Mapping[str, Any]", html: str, *, extract: bool
    ) -> "Patches":
        """Record one morph op with an optional extract flag."""
        extras = {"extract": True} if extract else {}
        self._ops.append(
            Patch(op="morph", target=dict(target), html=html, extras=extras)
        )
        return self

    def morph_zone(
        self, zone: str, *, overrides: "Mapping[str, Any] | None" = None
    ) -> "Patches":
        """Render the named zone of the origin page and morph it in place.

        The origin is a posted value and its view never ran, so the page it names
        authorizes the request first, exactly as a foreign page does.
        """
        self._authorize_origin()
        result = self._render_zone(zone, overrides)
        self._collect_zone_assets(result)
        self._append_morph({keys.ZONE: zone}, result.html[zone], extract=False)
        return self

    def morph_foreign_zone(
        self,
        zone: str,
        page: "Path | str",
        *,
        url_kwargs: "Mapping[str, Any] | None" = None,
    ) -> "Patches":
        """Render a zone of a foreign page out of band, re-running its guards.

        The foreign page's body resolution runs first, so a redirect or denial raises
        instead of morphing an empty body. A `render()` body is refused the same way.
        """
        request = self._require_request()
        foreign_path = self._foreign_page_path(page)
        kwargs = dict(url_kwargs or {})
        visit_url = page if isinstance(page, str) else None
        denial, dynamic = self._foreign_authorization(
            foreign_path, request, kwargs, visit_url
        )
        if denial is not None:
            raise ForeignPageNotAuthorizedError(foreign_path, denial.status_code)
        if dynamic:
            raise DynamicForeignPageError(foreign_path)
        result = self._render_foreign_zone(foreign_path, zone, request, kwargs)
        self._collect_zone_assets(result)
        self._append_morph({keys.ZONE: zone}, result.html[zone], extract=False)
        return self

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
        return resolve_url_to_page(url, self._require_request())

    def _foreign_authorization(
        self,
        foreign_path: "Path",
        request: HttpRequest,
        url_kwargs: dict[str, Any],
        visit_url: str | None,
    ) -> "tuple[HttpResponseBase | None, bool]":
        """Re-run the foreign page's body resolution once for guard and kind.

        The short-circuit response and the dynamic-body flag come from one resolution,
        so the foreign `render()` runs exactly once. A page named by URL is authorized
        against a visit of it, a page named by file path carries no URL to present.
        """
        return page_manager.authorization_outcome(
            foreign_path, request, visit_url, url_kwargs
        )

    def _render_foreign_zone(
        self,
        foreign_path: "Path",
        zone: str,
        request: HttpRequest,
        url_kwargs: dict[str, Any],
    ) -> "ZoneRenderResult":
        """Render the named zone of an already authorized foreign page."""
        return render_zone(foreign_path, (zone,), request, url_kwargs=url_kwargs)

    def morph_form(self, uid: str, html: str) -> "Patches":
        """Extract-morph the form addressed by its uid into the given HTML."""
        return self._append_morph({keys.FORM_SELECTOR: uid}, html, extract=True)

    def replace(self, target: "Mapping[str, Any]", html: str) -> "Patches":
        """Replace the target node wholesale with the given HTML."""
        self._ops.append(Patch(op="replace", target=dict(target), html=html))
        return self

    def inner(self, target: "Mapping[str, Any]", html: str) -> "Patches":
        """Replace only the contents of the target with the given HTML."""
        self._ops.append(Patch(op="inner", target=dict(target), html=html))
        return self

    def append(
        self, target: "Mapping[str, Any]", html: str, *, dedupe: DedupeMode = "key"
    ) -> "Patches":
        """Append children to the target, deduplicating by key or id."""
        return self._merge("append", target, html, dedupe)

    def prepend(
        self, target: "Mapping[str, Any]", html: str, *, dedupe: DedupeMode = "key"
    ) -> "Patches":
        """Prepend children to the target, deduplicating by key or id."""
        return self._merge("prepend", target, html, dedupe)

    def _merge(
        self, op: str, target: "Mapping[str, Any]", html: str, dedupe: DedupeMode
    ) -> "Patches":
        """Record a merge op appending or prepending deduplicated children."""
        if dedupe not in _DEDUPE_MODES:
            raise UnknownDedupeError(dedupe)
        self._ops.append(
            Patch(op=op, target=dict(target), html=html, extras={"dedupe": dedupe})
        )
        return self

    def remove(self, target: "Mapping[str, Any]") -> "Patches":
        """Remove the target node."""
        self._ops.append(Patch(op="remove", target=dict(target)))
        return self

    def refresh(self, *, zone: str) -> "Patches":
        """Ask the client to refetch the named zone with its own cookies."""
        self._ops.append(Patch(op="refresh", extras={keys.ZONE: zone}))
        return self

    def context(self, **names) -> "Patches":
        """Merge named serialize provider values into the client context.

        A reserved init-payload key raises `ReservedContextKeyError` whether or not the
        origin page registered it, so the refusal never depends on the collision check.
        """
        reserved = RESERVED_PAYLOAD_KEYS & names.keys()
        if reserved:
            raise ReservedContextKeyError(frozenset(reserved))
        allowed = self._serializable_names()
        serializer = resolve_serializer()
        data: dict[str, Any] = {}
        for name, value in names.items():
            if name not in allowed:
                raise UnknownContextNameError(name, tuple(sorted(allowed)))
            data[name] = json.loads(serializer.dumps(value))
        self._ops.append(Patch(op="context", extras={"data": data}))
        return self

    def _add_context(self, data: "Mapping[str, Any]") -> "Patches":
        """Record a context patch from already wire-ready provider values.

        The caller owns serialization, used by the zone-GET path where the
        collector already gathered and encoded the js-context.
        """
        self._ops.append(Patch(op="context", extras={"data": dict(data)}))
        return self

    def layer_open(
        self, *, zone: str | None = None, href: str | None = None
    ) -> "Patches":
        """Open a server-initiated layer, optionally seeding a zone or href.

        A seeded href needs a zone to load into and must be same-site, else
        `LayerHrefWithoutZoneError` or `CrossSiteHrefError`.
        """
        if href is not None and zone is None:
            raise LayerHrefWithoutZoneError(href)
        extras: dict[str, Any] = {}
        if zone is not None:
            extras[keys.ZONE] = zone
        if href is not None:
            extras["href"] = self._require_same_site(href)
        self._ops.append(Patch(op="layer.open", extras=extras))
        return self

    def layer_close(
        self, *, result: object = None, dismiss: str | None = None
    ) -> "Patches":
        """Close the top layer with an accept result or a dismissal.

        A dismissal sets the boolean `dismiss` flag and carries the reason text under
        `reason`, rather than overloading `dismiss` with the string.
        """
        extras: dict[str, Any] = {}
        if result is not None:
            extras["result"] = result
        if dismiss is not None:
            extras["dismiss"] = True
            extras["reason"] = dismiss
        self._ops.append(Patch(op="layer.close", extras=extras))
        return self

    def toast(self, text: str, variant: str = "info") -> "Patches":
        """Show a toast, sugar over an event with a built-in container."""
        self._ops.append(Patch(op="toast", extras={"text": text, "variant": variant}))
        return self

    def event(self, name: str, detail: "Mapping[str, Any] | None" = None) -> "Patches":
        """Dispatch a CustomEvent on document and the `Next.on` bus.

        A framework-owned name raises, so an app cannot forge a lifecycle event.
        """
        if _is_reserved_event(name):
            raise ReservedEventNameError(name)
        self._ops.append(
            Patch(op="event", extras={"name": name, "detail": dict(detail or {})})
        )
        return self

    def push_url(self, href: str) -> "Patches":
        """Push the validated href onto the browser history.

        The href must be same-site, a cross-site value raises
        `CrossSiteHrefError` rather than being masked as the origin path.
        """
        self._ops.append(
            Patch(
                op="url",
                extras={"action": "push", "href": self._require_same_site(href)},
            )
        )
        return self

    def replace_url(self, href: str) -> "Patches":
        """Replace the current browser history entry with the validated href.

        The href must be same-site like `push_url`, and no Back step is left behind.
        """
        self._ops.append(
            Patch(
                op="url",
                extras={"action": "replace", "href": self._require_same_site(href)},
            )
        )
        return self

    def meta(self, metadata: "Text | MetadataDict") -> "Patches":
        """Sync the head the origin page renders with `metadata` as its own segment.

        Text is the title alone. Without an origin page only `DEFAULTS` sits under it,
        and a URL operation queued before names the address the canonical reads.
        The operation syncs the title, the description, the canonical and the robots.
        A shape error in `metadata` raises, while an inherited callable that fails
        drops the operation with one log, so the rest of the patch still applies.
        """
        raw = {"title": metadata} if isinstance(metadata, str | Promise) else metadata
        segment = normalize_metadata(raw, source=_META_SOURCE)
        match = None if self._request is None else self._origin_match()
        request = self._request
        if match is None or match.page_path is None:
            folded = fold_metadata((site_segment(), segment))
            # The request is the action endpoint, which is no page to point at.
            self_canonical = folded.canonical is True
            folded = _requestless(folded)
        else:
            try:
                folded = page_manager.fold_metadata(
                    match.page_path,
                    overlay=segment,
                    request=request,
                    url_kwargs=dict(match.url_kwargs),
                    context_data=self._metadata_context,
                )
            except _META_PASSES:
                raise
            except Exception as exc:  # noqa: BLE001 - an inherited callable may raise anything
                _failures.contain(
                    exc,
                    (match.page_path, type(exc)),
                    "The metadata %s inherits for Patches.meta() raised %s, so the "
                    "head is left as it is and the rest of the patch applies. Fix "
                    "the @page.metadata callable or the metadata it names.",
                    match.page_path,
                    type(exc).__name__,
                )
                return self
            queued = self._queued_url()
            address = (
                match.origin if queued is None else _local_url(match.origin, queued)
            )
            request = visit_request(self._require_request(), address)
            self_canonical = False
        resolved = resolve_metadata(folded, request=request)
        extras: dict[str, object] = {
            "title": _plain(resolved.title),
            "description": _plain(resolved.description),
        }
        if not self_canonical:
            extras["canonical"] = resolved.canonical
        extras["robots"] = resolved.robots
        self._ops.append(Patch(op="meta", extras=extras))
        return self

    def redirect(self, href: str, *, external: bool = False) -> "Patches":
        """Drive a full client navigation to a server-authored href.

        An internal href must be same-site. `external=True` bypasses that for a
        server-authored destination, never user input, or it opens a redirect.
        """
        if external:
            self._ops.append(Patch(op="visit", extras={"href": href, "external": True}))
        else:
            self._ops.append(
                Patch(op="visit", extras={"href": self._require_same_site(href)})
            )
        return self

    def op(self, name: str, **payload) -> "Patches":
        """Emit a custom verb registered through `register_patch_op`.

        A built-in verb is refused so it travels only through its typed
        method, which owns the verb's wire keys, never as a raw payload.
        """
        if name in BUILTIN_OPS:
            raise BuiltinPatchOpError(name)
        if name not in patch_op_registry:
            raise UnknownPatchOpError(name)
        self._ops.append(Patch(op=name, extras=dict(payload)))
        return self

    def add_asset(self, kind: str, url: str, *, inline: str | None = None) -> "Patches":
        """Record an authored asset reference in the envelope manifest.

        The reference passes the resolution a full render gives it, so a backend
        mapping names to build outputs answers the same on either path.
        """
        return self._record_asset(
            kind, default_manager.resolve_url(url) if url else url, inline=inline
        )

    def _record_asset(
        self, kind: str, url: str, *, inline: str | None = None
    ) -> "Patches":
        """Record an already-resolved asset URL under the verb its kind registers.

        An inline body carries no URL, so it never reaches the per-render hook.
        """
        self._assets.append(
            Asset(
                kind=kind,
                url=default_manager.asset_url(url, request=self._request)
                if url
                else url,
                inline=inline,
                load=default_kinds.load(kind, inline=inline is not None),
            )
        )
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
            form=self._form,
            csrf=self._csrf,
            request_id=self._request_id,
        )

    def response(self, fallback: str | None = None) -> "PatchResponse | HttpResponse":
        """Assemble the response for the current request.

        Without the partial switch, 303s to the origin or `fallback`, to `/` if refused.
        """
        request = self._request
        if request is not None and is_partial_request(request):
            backend = partial_backend_manager.get()
            body = backend.serialize_envelope(self.envelope())
            return PatchResponse(
                body, content_type=backend.content_type, version=self._version
            )
        target = self._fallback_target(fallback)
        return redirect_or_fallback(target, "/", status=_SEE_OTHER)

    def _fallback_target(self, fallback: str | None) -> str:
        """Return the validated no-runtime redirect target.

        A request-free builder has no host, so `fallback` passes through unchecked.
        """
        if fallback is None:
            return self._origin_path()
        if self._request is None:
            return fallback
        return self._safe_url(fallback)

    def _origin_path(self) -> str:
        """Return the path the no-runtime fallback redirects to."""
        request = self._request
        if request is None:
            return "/"
        match = self._origin_match()
        if match is not None:
            return match.origin
        return request.path

    def _require_request(self) -> HttpRequest:
        """Return the bound request or raise when the builder has none."""
        if self._request is None:
            msg = "This builder operation needs a request-bound Patches(request)."
            raise RuntimeError(msg)
        return self._request

    def _queued_url(self) -> str | None:
        """Return the href of the last URL operation queued so far, if any."""
        return next(
            (
                str(patch.extras["href"])
                for patch in reversed(self._ops)
                if patch.op == "url"
            ),
            None,
        )

    def _origin_match(self) -> "OriginMatch | None":
        """Resolve the request's posted origin once, memoised on the builder."""
        if not self._origin_resolved:
            self._origin = resolve_origin(self._require_request())
            self._origin_resolved = True
        return self._origin

    def _resolve_page_path(self) -> "Path":
        """Return the origin page path of the request, raising when it has none."""
        self._require_request()
        match = self._origin_match()
        page_path = None if match is None else match.page_path
        if page_path is None:
            msg = "The request origin does not resolve to a page."
            raise RuntimeError(msg)
        return page_path

    def _authorize_origin(self) -> None:
        """Re-run the origin page's authorization once per builder.

        Memoised, so several origin-zone morphs in one envelope re-run no page render.
        """
        if self._origin_authorized:
            return
        page_path = self._resolve_page_path()
        match = self._origin_match()
        denial, _dynamic = page_manager.authorization_outcome(
            page_path,
            self._require_request(),
            match.origin if match is not None else None,
            self._origin_url_kwargs(),
        )
        if denial is not None:
            raise ForeignPageNotAuthorizedError(page_path, denial.status_code)
        self._origin_authorized = True

    def _metadata_context(self) -> dict[str, object]:
        """Return the origin render context for an inherited metadata callable.

        The callable renders part of the origin page, so its guard runs first.
        """
        self._authorize_origin()
        return self._origin_render_context()

    def _origin_url_kwargs(self) -> dict[str, object]:
        """Return the URL kwargs of the origin page for a zone or component render."""
        match = self._origin_match()
        return dict(match.url_kwargs) if match is not None else {}

    def _origin_render_context(self) -> dict[str, object]:
        """Return the origin page render context, built once per builder.

        Memoised because `context()` and `morph(zone=...)` both resolve it, and a fresh
        copy is handed out since consumers mutate the mapping.
        """
        if self._render_context is None:
            # Pinned so mypy does not read the url kwarg splat as the zone batch.
            self._render_context = page_manager.build_render_context(
                self._resolve_page_path(),
                self._require_request(),
                _requested_zones=None,
                **self._origin_url_kwargs(),
            )
        return dict(self._render_context)

    def _render_zone(
        self, zone: str, overrides: "Mapping[str, Any] | None"
    ) -> "ZoneRenderResult":
        """Render the named zone of the origin page with optional overrides."""
        return render_zone(
            self._resolve_page_path(),
            (zone,),
            self._require_request(),
            url_kwargs=self._origin_url_kwargs(),
            overrides=dict(overrides) if overrides else None,
            context_data=self._origin_render_context(),
        )

    def _serializable_names(self) -> frozenset[str]:
        """Return the serialize=True provider names of the origin page."""
        js_context = self._origin_render_context().get(JS_CONTEXT_KEY, {})
        return frozenset(js_context) if isinstance(js_context, dict) else frozenset()

    def _collect_zone_assets(self, result: "ZoneRenderResult") -> "Patches":
        """Record the URL-form and inline-form assets a zone body collected.

        The collector already holds resolved URLs, so they are recorded rather than
        resolved again, and the js-context delta stays with the builder verbs.
        """
        for kind, body in result.inline_assets():
            self._record_asset(kind, "", inline=body)
        for kind, url in result.url_assets():
            self._record_asset(kind, url)
        return self

    def absorb_zone_result(self, result: "ZoneRenderResult") -> "Patches":
        """Record a zone render's assets and js-context delta.

        A reserved init-payload key is dropped rather than raised, since the delta is a
        render by-product rather than a handler naming the key.
        """
        self._collect_zone_assets(result)
        delta = {
            name: value
            for name, value in result.js_context_delta().items()
            if name not in RESERVED_PAYLOAD_KEYS
        }
        if delta:
            self._add_context(delta)
        return self

    def _is_same_site(self, href: str) -> bool:
        """Return True when `href` targets the bound request's host and scheme.

        A path the origin rule admits is same-site past Django's 2048-character URL cap.
        """
        request = self._require_request()
        if validated_origin_path(href) is not None:
            return True
        allowed = {request.get_host()}
        return url_has_allowed_host_and_scheme(
            href, allowed_hosts=allowed, require_https=request.is_secure()
        )

    def _safe_url(self, href: str) -> str:
        """Return `href` if it is same-site, else fall back to the origin path."""
        if self._is_same_site(href):
            return href
        return self._origin_path()

    def _require_same_site(self, href: str) -> str:
        """Return `href` if it is same-site, else raise for the caller bug.

        Used by the in-app navigation sinks where a cross-site href is a
        caller mistake rather than the no-runtime defense-in-depth fallback.
        """
        if self._is_same_site(href):
            return href
        raise CrossSiteHrefError(href)


class PatchResponse(HttpResponse):
    """HTTP response that carries a serialized patch envelope, never stored by a cache.

    Many CDNs ignore `Vary`, so a public envelope would stand in for its page.
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
        self["Cache-Control"] = _NO_STORE
        set_partial_vary(self)


__all__ = ["DedupeMode", "PatchResponse", "Patches"]
