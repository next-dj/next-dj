"""Patch envelope value objects, the request-bound builder, and PatchResponse."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme

from next.forms.origin import resolve_origin, resolve_url_to_page
from next.pages import page
from next.static.serializers import resolve_serializer

from . import keys
from .headers import (
    CONTENT_TYPE,
    RESPONSE_VERSION,
    is_partial_request,
    set_partial_vary,
)
from .manager import partial_backend_manager
from .registry import BUILTIN_OPS, patch_op_registry
from .render import render_zone


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from django.http import HttpResponseBase

    from next.forms.origin import OriginMatch

    from .render import ZoneRenderResult


_SEE_OTHER = 303

# Each morph() route owns its selector keywords, so a stray key is refused.
_ZONE_MORPH_KEYS: frozenset[str] = frozenset({"zone", "overrides"})
_FOREIGN_ZONE_MORPH_KEYS: frozenset[str] = frozenset({"zone", "page", "url_kwargs"})
_FORM_MORPH_KEYS: frozenset[str] = frozenset({"form"})

# Framework-owned bus events, refused to event() so an app cannot forge one.
_RESERVED_EVENT_NAMES: frozenset[str] = frozenset({"ready", "context-updated"})
_RESERVED_EVENT_PREFIXES: tuple[str, ...] = ("partial:", "next:")

DedupeMode = Literal["key", "id"]
_DEDUPE_MODES: frozenset[str] = frozenset({"key", "id"})


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


class ReservedPatchKeyError(ValueError):
    """Raised when a custom op payload names a structural wire key.

    The `op`, `target`, and `html` keys carry the patch structure, so a
    payload that names one of them is refused rather than overwriting it.
    """

    def __init__(self, op: str, keys: frozenset[str]) -> None:
        """Store the offending verb and the reserved keys it collided with."""
        self.op = op
        self.keys = keys
        names = ", ".join(sorted(keys))
        super().__init__(
            f'Patch op "{op}" payload names the reserved wire key(s) {names}. '
            "Use a different payload key, op/target/html are structural."
        )


class BuiltinPatchOpError(ValueError):
    """Raised when the generic `op()` channel names a built-in verb.

    A built-in verb owns typed wire keys, so it must travel through its
    typed builder method rather than the raw `op()` payload channel.
    """

    def __init__(self, name: str) -> None:
        """Store the built-in verb name and build a readable message."""
        self.name = name
        super().__init__(
            f'Patch op "{name}" is built in, emit it through its typed '
            "builder method rather than the generic op() channel."
        )


class ReservedEventNameError(ValueError):
    """Raised when `event()` names a framework-owned client-bus event.

    The `ready` and `context-updated` events and the `partial:` and
    `next:` prefixes belong to the runtime lifecycle, so an app event under
    one of those names is refused rather than forging a framework signal.
    """

    def __init__(self, name: str) -> None:
        """Store the reserved name and build a readable message."""
        self.name = name
        super().__init__(
            f'Event name "{name}" is reserved by the framework client bus. '
            "Use your own application event name instead."
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


class DynamicForeignPageError(ValueError):
    """Raised when an OOB morph names a foreign page with a `render()` body.

    A `render()` string body never reaches the composed-template cache, so
    it has no compiled source to render a standalone zone against. The OOB
    view branch refuses the same shape with a 400, so the builder refuses
    it here rather than morphing the page's stale static template.
    """

    def __init__(self, page_path: "Path") -> None:
        """Store the page path and build a readable message."""
        self.page_path = page_path
        super().__init__(
            f"Page {page_path} resolves a dynamic render() body, which has no "
            "zone to morph out of band. A foreign zone morph needs a page "
            "whose body is a static template."
        )


class UnknownContextNameError(LookupError):
    """Raised when `context()` names a value that is not a serialize provider.

    Only the names of registered `serialize=True` context providers may
    travel in a context patch, so an arbitrary mapping is rejected at the
    builder rather than serialized blind. The message names the available
    providers so a typo points at what is registered.
    """

    def __init__(self, name: str, available: tuple[str, ...] = ()) -> None:
        """Store the rejected name and the available serialize provider names."""
        self.name = name
        self.available = available
        message = (
            f'Context name "{name}" is not a registered serialize=True '
            "provider on the origin page. Mark its @context provider "
            "serialize=True or drop it from the patch."
        )
        if available:
            names = ", ".join(repr(provider) for provider in available)
            message = f"{message} Available serialize providers: {names}."
        super().__init__(message)


class UnknownDedupeError(ValueError):
    """Raised when a merge op names a dedupe strategy the client cannot apply.

    The client keys a merge row by `data-next-key` then `id`, so only
    `key` and `id` mean anything on the wire, an unknown value is refused
    at the builder rather than dropped to a silent no-dedup downstream.
    """

    def __init__(self, dedupe: str) -> None:
        """Store the rejected dedupe value and build a readable message."""
        self.dedupe = dedupe
        super().__init__(
            f'Dedupe strategy "{dedupe}" is not supported, use "key" or "id".'
        )


class CrossSiteHrefError(ValueError):
    """Raised when a builder href sink names a cross-site URL.

    The `push_url`, `layer_open(href=)`, and internal `redirect` sinks
    author an in-app navigation, so a cross-site href is a caller bug
    refused at the builder rather than masked as a fallback to the origin
    path. A server-authored external destination travels through
    `redirect(external=True)` instead.
    """

    def __init__(self, href: str) -> None:
        """Store the rejected href and build a readable message."""
        self.href = href
        super().__init__(
            f'href "{href}" is not same-site, for a server-authored external '
            "destination use redirect(external=True)."
        )


def _is_reserved_event(name: str) -> bool:
    """Return True when the name belongs to the framework client-bus channel."""
    return name in _RESERVED_EVENT_NAMES or name.startswith(_RESERVED_EVENT_PREFIXES)


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

    def __post_init__(self) -> None:
        """Refuse an extras payload that names a structural wire key.

        The `op`, `target`, and `html` keys carry the patch structure, so a
        payload that names one of them is refused at construction rather than
        silently overwriting the structural key on serialization.
        """
        collision = keys.RESERVED_PATCH_KEYS & self.extras.keys()
        if collision:
            raise ReservedPatchKeyError(self.op, frozenset(collision))

    def as_dict(self) -> dict[str, Any]:
        """Return the wire form of the patch as an ordered mapping."""
        data: dict[str, Any] = {keys.OP: self.op}
        if self.target is not None:
            data[keys.TARGET] = dict(self.target)
        if self.html is not None:
            data[keys.HTML] = self.html
        data.update(self.extras)
        return data


@dataclass(frozen=True, slots=True)
class Asset:
    """One co-located asset of a rendered target, by kind and URL."""

    kind: str
    url: str

    def as_dict(self) -> dict[str, str]:
        """Return the wire form of the asset entry."""
        return {keys.KIND: self.kind, keys.URL: self.url}


@dataclass(frozen=True, slots=True)
class FormMeta:
    """Machine-readable state of a form built from its field specs."""

    uid: str
    valid: bool
    errors: "Mapping[str, Sequence[str]]" = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the wire form of the form meta object."""
        return {
            keys.UID: self.uid,
            keys.VALID: self.valid,
            keys.ERRORS: {name: list(msgs) for name, msgs in self.errors.items()},
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
    form: "FormMeta | None" = None
    csrf: "Mapping[str, Any] | None" = None
    request_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the wire form of the envelope as an ordered mapping."""
        data: dict[str, Any] = {
            keys.VERSION: self.version,
            keys.OPS: [op.as_dict() for op in self.ops],
            keys.ASSETS: [asset.as_dict() for asset in self.assets],
            keys.FORM: self.form.as_dict() if self.form is not None else None,
        }
        if self.csrf is not None:
            data[keys.CSRF] = dict(self.csrf)
        if self.request_id is not None:
            data[keys.REQUEST_ID] = self.request_id
        return data


class Patches:
    """Request-bound builder of a patch envelope.

    Built from a request, the builder takes its asset version from the
    active protocol backend and resolves the origin page lazily, so a
    `morph(zone=...)` renders against the page that owns the request.
    Built from a bare version string the builder
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
            self._version = partial_backend_manager.version()
        self._ops: list[Patch] = []
        self._assets: list[Asset] = []
        self._form: FormMeta | None = None
        self._csrf: Mapping[str, Any] | None = None
        self._request_id: str | None = echo_of
        self._origin: OriginMatch | None = None
        self._origin_resolved = False
        self._render_context: dict[str, object] | None = None

    @property
    def version(self) -> str:
        """Return the asset version stamped on the envelope."""
        return self._version

    def morph(
        self,
        target: "Mapping[str, Any] | None" = None,
        html: str | None = None,
        *,
        extract: bool = False,
        **select: object,
    ) -> "Patches":
        """Morph a target into HTML, the default verb.

        A thin facade over the typed per-verb morph methods that keeps the
        single-verb mental model. The facade routes the selector keyword to
        the method that owns its contract, so two selectors in one call or
        an unknown selector raises rather than being silently dropped.
        """
        if not select:
            return self._append_morph(dict(target or {}), html or "", extract=extract)
        return self._dispatch_morph(html, select)

    def _dispatch_morph(
        self,
        html: str | None,
        select: "Mapping[str, object]",
    ) -> "Patches":
        """Route a keyword-selected morph to its typed per-verb method."""
        zone = select.get("zone")
        form = select.get("form")
        if isinstance(zone, str) and isinstance(form, str):
            msg = "morph() got conflicting selector keywords ['zone', 'form']."
            raise TypeError(msg)
        if isinstance(zone, str):
            return self._dispatch_zone_morph(zone, select)
        if isinstance(form, str):
            self._reject_extra_morph_keys(select, _FORM_MORPH_KEYS)
            return self.morph_form(form, html or "")
        msg = f"morph() got unexpected selector keywords {sorted(select)}."
        raise TypeError(msg)

    def _dispatch_zone_morph(
        self,
        zone: str,
        select: "Mapping[str, object]",
    ) -> "Patches":
        """Route a zone-selected morph to its local or foreign per-verb method.

        A `url_kwargs` without a `page` names a foreign page's URL with no
        page to render, so it is refused rather than dropped into a local
        zone render that ignores it.
        """
        if "page" in select:
            self._reject_extra_morph_keys(select, _FOREIGN_ZONE_MORPH_KEYS)
            return self.morph_foreign_zone(
                zone,
                cast("Path | str", select["page"]),
                url_kwargs=cast("Mapping[str, Any] | None", select.get("url_kwargs")),
            )
        self._reject_extra_morph_keys(select, _ZONE_MORPH_KEYS)
        overrides = cast("Mapping[str, Any] | None", select.get("overrides"))
        return self.morph_zone(zone, overrides=overrides)

    @staticmethod
    def _reject_extra_morph_keys(
        select: "Mapping[str, object]",
        allowed: frozenset[str],
    ) -> None:
        """Raise when the selector mapping carries keys the route does not own."""
        extra = sorted(select.keys() - allowed)
        if extra:
            msg = (
                f"morph() got unexpected keyword(s) {extra}, "
                f"this route accepts {sorted(allowed)}."
            )
            raise TypeError(msg)

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

    def morph_zone(
        self,
        zone: str,
        *,
        overrides: "Mapping[str, Any] | None" = None,
    ) -> "Patches":
        """Render the named zone of the origin page and morph it in place."""
        result = self._render_zone(zone, overrides)
        self._collect_assets(result)
        return self._append_morph({keys.ZONE: zone}, result.html[zone], extract=False)

    def morph_foreign_zone(
        self,
        zone: str,
        page: "Path | str",
        *,
        url_kwargs: "Mapping[str, Any] | None" = None,
    ) -> "Patches":
        """Render a zone of a foreign page out of band, re-running its guards.

        The page is named by its page path or by a URL of it, which is
        resolved through the URLconf to the page that serves it. The
        foreign page's body resolution runs first, so a redirect or a
        denial short-circuits before any zone renders and raises instead
        of morphing an empty body. A `render()` string body has no zone to
        render standalone, so it is refused the same way the OOB view
        branch refuses it. With the page authorized, the named zone renders
        standalone with the foreign page's URL kwargs and morphs in place
        addressed by zone name.
        """
        request = self._require_request()
        foreign_path = self._foreign_page_path(page)
        kwargs = dict(url_kwargs or {})
        denial, dynamic = self._foreign_authorization(foreign_path, request, kwargs)
        if denial is not None:
            raise ForeignPageNotAuthorizedError(foreign_path, denial.status_code)
        if dynamic:
            raise DynamicForeignPageError(foreign_path)
        result = self._render_foreign_zone(foreign_path, zone, request, kwargs)
        self._collect_assets(result)
        return self._append_morph({keys.ZONE: zone}, result.html[zone], extract=False)

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
    ) -> "tuple[HttpResponseBase | None, bool]":
        """Re-run the foreign page's body resolution once for guard and kind.

        The short-circuit response and the dynamic-body flag come from one
        resolution so the foreign page's `render()` runs exactly once.
        """
        return page.authorization_outcome(foreign_path, request, **url_kwargs)

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
        self,
        target: "Mapping[str, Any]",
        html: str,
        *,
        dedupe: DedupeMode = "key",
    ) -> "Patches":
        """Append children to the target, deduplicating by key or id."""
        return self._merge("append", target, html, dedupe)

    def prepend(
        self,
        target: "Mapping[str, Any]",
        html: str,
        *,
        dedupe: DedupeMode = "key",
    ) -> "Patches":
        """Prepend children to the target, deduplicating by key or id."""
        return self._merge("prepend", target, html, dedupe)

    def _merge(
        self,
        op: str,
        target: "Mapping[str, Any]",
        html: str,
        dedupe: DedupeMode,
    ) -> "Patches":
        """Record a merge op appending or prepending deduplicated children."""
        if dedupe not in _DEDUPE_MODES:
            raise UnknownDedupeError(dedupe)
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
        self._ops.append(Patch(op="refresh", extras={keys.ZONE: zone}))
        return self

    def context(self, **names: object) -> "Patches":
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
                raise UnknownContextNameError(name, tuple(sorted(allowed)))
            data[name] = json.loads(serializer.dumps(value))
        self._ops.append(Patch(op="context", extras={"data": data}))
        return self

    def layer_open(
        self,
        *,
        zone: str | None = None,
        href: str | None = None,
    ) -> "Patches":
        """Open a server-initiated layer, optionally seeding a zone or href.

        A seeded href must be same-site, a cross-site value raises
        `CrossSiteHrefError` rather than being masked as the origin path.
        """
        extras: dict[str, Any] = {}
        if zone is not None:
            extras["zone"] = zone
        if href is not None:
            extras["href"] = self._require_same_site(href)
        self._ops.append(Patch(op="layer.open", extras=extras))
        return self

    def layer_close(
        self,
        *,
        result: object = None,
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
        """Dispatch a CustomEvent on document and the `Next.on` bus.

        A framework-owned event name raises `ReservedEventNameError` so an
        app cannot forge a runtime lifecycle event.
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

    def redirect(self, href: str, *, external: bool = False) -> "Patches":
        """Drive a full client navigation to a server-authored href.

        An internal href must be same-site, a cross-site value raises
        `CrossSiteHrefError`. An external href is sent with a
        full-navigation marker so a server-authored redirect like OAuth or
        a payment gateway is not rejected by the same-host validator.

        The `external=True` escape hatch bypasses same-host validation, so
        the href must be server authored. Never pass user-supplied input
        through it, or the page becomes an open redirect.
        """
        if external:
            self._ops.append(Patch(op="visit", extras={"href": href, "external": True}))
        else:
            self._ops.append(
                Patch(op="visit", extras={"href": self._require_same_site(href)})
            )
        return self

    def op(self, name: str, **payload: object) -> "Patches":
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

    def add_asset(self, kind: str, url: str) -> "Patches":
        """Record a co-located asset in the envelope manifest."""
        self._assets.append(Asset(kind=kind, url=url))
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
        target = self._fallback_target(fallback)
        return HttpResponseRedirect(target, status=_SEE_OTHER)

    def _fallback_target(self, fallback: str | None) -> str:
        """Return the validated no-runtime redirect target.

        A bound request validates the fallback against its host like every
        other href sink. A request-free builder has no host to validate
        against, so the server-authored fallback passes through.
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

    def _origin_match(self) -> "OriginMatch | None":
        """Resolve the request's posted origin once, memoised on the builder."""
        if not self._origin_resolved:
            self._origin = resolve_origin(self._require_request())
            self._origin_resolved = True
        return self._origin

    def _resolve_page_path(self) -> "Path":
        """Return the origin page path of the request, raising when it has none."""
        match = self._origin_match()
        if match is None or match.page_path is None:
            msg = "The request origin does not resolve to a page."
            raise RuntimeError(msg)
        return match.page_path

    def _origin_url_kwargs(self) -> dict[str, object]:
        """Return the URL kwargs of the origin page for a zone or component render."""
        match = self._origin_match()
        return dict(match.url_kwargs) if match is not None else {}

    def _origin_render_context(self) -> dict[str, object]:
        """Return the origin page render context, built once per builder.

        A handler that pairs `context()` with `morph(zone=...)` resolves the
        origin context through both paths, so the build is memoised and a
        fresh copy is handed out because consumers mutate the mapping.
        """
        if self._render_context is None:
            self._render_context = page.build_render_context(
                self._resolve_page_path(),
                self._require_request(),
                **self._origin_url_kwargs(),
            )
        return dict(self._render_context)

    def _render_zone(
        self,
        zone: str,
        overrides: "Mapping[str, Any] | None",
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
        js_context = self._origin_render_context().get("_next_js_context", {})
        return frozenset(js_context) if isinstance(js_context, dict) else frozenset()

    def _collect_assets(self, result: "ZoneRenderResult") -> None:
        """Record the assets a rendered zone body collected on the envelope."""
        for kind, url in result.url_assets():
            self.add_asset(kind, url)

    def _is_same_site(self, href: str) -> bool:
        """Return True when `href` targets the bound request's host and scheme."""
        request = self._require_request()
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
    "BuiltinPatchOpError",
    "CrossSiteHrefError",
    "DedupeMode",
    "DynamicForeignPageError",
    "Envelope",
    "ForeignPageNotAuthorizedError",
    "FormMeta",
    "Patch",
    "PatchResponse",
    "Patches",
    "ReservedEventNameError",
    "ReservedPatchKeyError",
    "UnknownContextNameError",
    "UnknownDedupeError",
    "UnknownPatchOpError",
]
