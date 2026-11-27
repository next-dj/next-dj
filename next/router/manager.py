"""The façade over the cut, digest and shed backends and the `$router` entry."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from django.apps import apps
from django.conf import settings
from django.urls import NoReverseMatch, get_script_prefix, get_urlconf, reverse

from next.backends import BackendRoot, resolve_setting_class
from next.caches import FORK, FRAMEWORK, ROUTES, SETTINGS
from next.conf.merge import FROZEN_DEFAULTS
from next.conf.router import (
    SCOPE,
    RouterScope,
    router_enabled,
    router_settings,
    speculation_eagerness,
)
from next.errors import SettingShapeError
from next.pages.manager import page
from next.partial.headers import CONTENT_TYPE
from next.partial.manager import partial_backend_manager
from next.paths import home_paths
from next.urls.scope import route_scope, scope_url

from .backends import ChromeDigest, LoadShed, NavigationCut, PrefetchShed, VerifiedCut
from .signals import router_backend_loaded
from .speculation import encoded_rules


if TYPE_CHECKING:
    from collections.abc import Callable


type _Places = dict[tuple[str, str | None], tuple[str, str]]

_ADMIN_APP: Final = "django.contrib.admin"
_ADMIN_INDEX: Final = "admin:index"
_PLACES: Final = 64
_KEYS: Final = ("CUT", "DIGEST", "SHED")


def _path_prefix(url: object) -> str | None:
    """Return the path a `STATIC_URL` or `MEDIA_URL` names, `None` for none."""
    if not isinstance(url, str) or not url:
        return None
    path = urlsplit(url).path
    return path if path.startswith("/") else f"/{path}"


def _admin_prefix() -> str | None:
    """Return the URL prefix of the admin site when it is installed and mounted."""
    if not apps.is_installed(_ADMIN_APP):
        return None
    try:
        return reverse(_ADMIN_INDEX)
    except NoReverseMatch:
        return None


def asset_prefixes() -> tuple[str, ...]:
    """Return the paths of `STATIC_URL` and `MEDIA_URL`, served outside the URLconf."""
    urls = (getattr(settings, "STATIC_URL", None), getattr(settings, "MEDIA_URL", None))
    return tuple(p for url in urls if (p := _path_prefix(url)) is not None)


def exclude_prefixes() -> tuple[str, ...]:
    """Return the path prefixes the client router never owns, derived ones first.

    A prefix of the whole site is left out, as an unset `MEDIA_URL` reads as the root.
    """
    derived = (*asset_prefixes(), _admin_prefix())
    written = router_settings().exclude
    whole = {"/", get_script_prefix()}
    return tuple(dict.fromkeys(p for p in (*derived, *written) if p and p not in whole))


def _seconds(value: float) -> float | int:
    """Return a whole number of seconds as an integer, which encodes shorter."""
    return int(value) if value.is_integer() else value


def router_entry() -> str:
    """Return the encoded `$router` entry the init payload carries.

    `routes` stands in for `scope` under `pages`, `type` names a custom content type.
    """
    current = router_settings()
    owned: dict[str, object] = (
        {"routes": scope_url()}
        if current.scope is RouterScope.PAGES
        else {"scope": current.scope}
    )
    entry: dict[str, object] = {
        **owned,
        "exclude": list(exclude_prefixes()),
        "foreign": current.foreign,
        "prefetch": current.prefetch,
        "prefetch_delay": _seconds(current.prefetch_delay),
        "prefetch_ttl": _seconds(current.prefetch_ttl),
        "prefetch_shell_ttl": _seconds(current.prefetch_shell_ttl),
        "prefetch_entries": current.prefetch_entries,
        "press": current.press,
        "snapshots": current.snapshots,
        "progress": current.progress,
        "progress_delay": _seconds(current.progress_delay),
        "transitions": current.transitions,
        "intercept": current.intercept,
        "links": list(current.links_containers) if current.links_enabled else None,
        "homes": sorted(home_paths()) if current.links_enabled else None,
    }
    content_type = partial_backend_manager.get().content_type
    if content_type != CONTENT_TYPE:
        entry["type"] = content_type
    return json.dumps(entry, separators=(",", ":"))


def speculation_rules() -> str:
    """Return the encoded rules `ROUTER["SPECULATION"]` asks for, empty while unset."""
    current = router_settings()
    eagerness = speculation_eagerness(current.speculation)
    if eagerness is None:
        return ""
    if current.scope is not RouterScope.PAGES:
        return encoded_rules(eagerness, current.scope, exclude_prefixes())
    table = route_scope()
    pages = table.patterns(page=True)
    if not pages:
        return ""
    others = table.patterns(page=False)
    return encoded_rules(eagerness, current.scope, exclude_prefixes(), pages, others)


def _document() -> tuple[str, str]:
    """Return the `$router` entry and the speculation rules of a routed document."""
    return router_entry(), speculation_rules()


def _placed(held: _Places, build: Callable[[], tuple[str, str]]) -> tuple[str, str]:
    """Return what `build` gives under the script prefix and URLconf of this thread.

    The homes and the exclusions follow both, which a request may set for itself.
    """
    place = (get_script_prefix(), get_urlconf())
    found = held.get(place)
    if found is None:
        if len(held) >= _PLACES:
            held.clear()
        found = held[place] = build()
    return found


def _seam[T](
    key: str,
    dotted: str,
    *,
    base: BackendRoot[T],
    shipped: BackendRoot[T],
    base_path: str,
) -> T:
    """Build the backend a seam path names, read from `router_settings()`.

    A misfit value reads there as its default, which names the shipped class.
    """
    if dotted == FROZEN_DEFAULTS[SCOPE][key]:
        return shipped()
    return resolve_setting_class(
        key, scope=SCOPE, base=base, shipped=shipped, base_path=base_path
    )()


@dataclass(frozen=True, slots=True)
class _Seams:
    """The cut, the digest and the shed one read of the `ROUTER` settings builds."""

    cut: NavigationCut
    digest: ChromeDigest
    shed: PrefetchShed
    paths: tuple[str, str, str]

    def announce(self) -> None:
        """Send `router_backend_loaded` per backend, its `ROUTER` entry as `config`."""
        backends = (self.cut, self.digest, self.shed)
        for key, dotted, backend in zip(_KEYS, self.paths, backends, strict=True):
            router_backend_loaded.send(
                sender=type(backend), config={key: dotted}, instance=backend
            )


def _seams() -> _Seams:
    """Build the cut, the digest and the shed their `ROUTER` keys name."""
    current = router_settings()
    return _Seams(
        paths=(current.cut, current.digest, current.shed),
        cut=_seam(
            "CUT",
            current.cut,
            base=NavigationCut,
            shipped=VerifiedCut,
            base_path="next.router.NavigationCut",
        ),
        digest=_seam(
            "DIGEST",
            current.digest,
            base=ChromeDigest,
            shipped=ChromeDigest,
            base_path="next.router.ChromeDigest",
        ),
        shed=_seam(
            "SHED",
            current.shed,
            base=PrefetchShed,
            shipped=LoadShed,
            base_path="next.router.PrefetchShed",
        ),
    )


class ClientRouterManager:
    """Hold the cut, digest and shed backends, built once, and the `$router` entry.

    The backends are named by `ROUTER["CUT"]`, `ROUTER["DIGEST"]` and `ROUTER["SHED"]`.
    """

    def __init__(self) -> None:
        """Hold nothing until the first navigation or routed document."""
        self.composed: bool | None = None
        self._lock = threading.Lock()
        self._seams: _Seams | None = None
        self._documents: _Places = {}

    def forget(self) -> None:
        """Drop the backends, the entry and the rules, so a next use reads settings.

        It waits out a build under way, so no backend of the old settings outlives it.
        """
        with self._lock:
            self._seams = None
            self._documents = {}

    def forked(self) -> None:
        """Start a forked child with a fresh lock and nothing built."""
        self._lock = threading.Lock()
        self.forget()

    def _built(self) -> _Seams:
        """Build the backends under the lock unless a racer did, then announce them."""
        built = None
        with self._lock:
            seams = self._seams
            if seams is None:
                seams = self._seams = built = _seams()
        if built is not None:
            built.announce()
        return seams

    def cut(self) -> NavigationCut:
        """Return the cut `ROUTER["CUT"]` names, built on the first call."""
        return (self._seams or self._built()).cut

    def digest(self) -> ChromeDigest:
        """Return the digest `ROUTER["DIGEST"]` names, built on the first call."""
        return (self._seams or self._built()).digest

    def shed(self) -> PrefetchShed:
        """Return the prefetch shed `ROUTER["SHED"]` names, built on the first call."""
        return (self._seams or self._built()).shed

    def document(self) -> tuple[str, str]:
        """Return the `$router` entry and the rules, built once per prefix and URLconf.

        A routed document reads both, so one lookup of its prefix and URLconf serves it.
        """
        return _placed(self._documents, _document)

    def entry(self) -> str:
        """Return the encoded `$router` entry, built once per prefix and URLconf."""
        return self.document()[0]

    def speculation(self) -> str:
        """Return the encoded speculation rules, built once per prefix and URLconf."""
        return self.document()[1]


client_router = ClientRouterManager()
FORK.attach(client_router.forked)


def forget_router(**kwargs: object) -> None:
    """Reset the router on a settings reload, recomposing pages once it switches.

    Only an enabled router splices the slot tag, so a misshapen `ROUTER` recomposes too.
    """
    client_router.forget()
    try:
        enabled: bool | None = router_enabled()
    except SettingShapeError:
        enabled = None
    if client_router.composed is not enabled:
        page.clear_template_caches()
    client_router.composed = enabled


SETTINGS.attach(client_router.forget)
ROUTES.attach(client_router.forget)
FRAMEWORK.attach(forget_router)


__all__ = [
    "ClientRouterManager",
    "asset_prefixes",
    "client_router",
    "exclude_prefixes",
    "forget_router",
    "router_entry",
    "speculation_rules",
]
