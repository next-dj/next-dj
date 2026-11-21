"""The façade over the cut and digest backends and the `$router` payload entry."""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from django.apps import apps
from django.conf import settings
from django.urls import NoReverseMatch, get_script_prefix, get_urlconf, reverse

from next.backends import resolve_setting_class
from next.conf.router import (
    SCOPE,
    router_enabled,
    router_settings,
    speculation_eagerness,
)
from next.pages.manager import page
from next.partial.headers import CONTENT_TYPE
from next.partial.manager import partial_backend_manager
from next.paths import home_paths
from next.utils import after_fork

from .backends import ChromeDigest, NavigationCut, VerifiedCut
from .speculation import encoded_rules


if TYPE_CHECKING:
    from collections.abc import Callable


type _Places = dict[tuple[str, str | None], str]

_ADMIN_APP: Final = "django.contrib.admin"
_ADMIN_INDEX: Final = "admin:index"
_ENTRY_SETTINGS: Final = frozenset(
    {
        "STATIC_URL",
        "MEDIA_URL",
        "ROOT_URLCONF",
        "INSTALLED_APPS",
        "LANGUAGES",
        "LANGUAGE_CODE",
        "FORCE_SCRIPT_NAME",
    }
)
_PLACES: Final = 64


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
    """Return the path prefixes the client router never owns, derived ones first."""
    derived = (*asset_prefixes(), _admin_prefix())
    written = router_settings().exclude
    return tuple(dict.fromkeys(p for p in (*derived, *written) if p and p != "/"))


def router_entry() -> str:
    """Return the encoded `$router` entry the init payload carries.

    `type` names the content type of a partial backend whose answers are not the stock.
    """
    current = router_settings()
    entry: dict[str, object] = {
        "scope": current.scope,
        "exclude": list(exclude_prefixes()),
        "foreign": current.foreign,
        "prefetch": current.prefetch,
        "prefetch_delay": current.prefetch_delay,
        "prefetch_ttl": current.prefetch_ttl,
        "prefetch_shell_ttl": current.prefetch_shell_ttl,
        "prefetch_entries": current.prefetch_entries,
        "snapshots": current.snapshots,
        "progress_delay": current.progress_delay,
        "transitions": current.transitions,
        "intercept": current.intercept,
        "max_layouts": current.max_layouts,
        "current": list(current.current_containers)
        if current.current_enabled
        else None,
        "homes": sorted(home_paths()) if current.current_enabled else None,
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
    return encoded_rules(eagerness, current.scope, exclude_prefixes())


def _placed(held: _Places, build: Callable[[], str]) -> str:
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


class RouterManager:
    """Hold the cut and digest backends, built once, and the `$router` entry.

    The backends are named by `ROUTER["CUT"]` and `ROUTER["DIGEST"]`.
    """

    def __init__(self) -> None:
        """Hold nothing until the first navigation or routed document."""
        self.composed: bool | None = None
        self.forget()

    def forget(self) -> None:
        """Drop the backends, the entry and the rules, so a next use reads settings."""
        self._lock = threading.Lock()
        self._cut: NavigationCut | None = None
        self._digest: ChromeDigest | None = None
        self._entries: _Places = {}
        self._speculations: _Places = {}

    def cut(self) -> NavigationCut:
        """Return the cut `ROUTER["CUT"]` names, built on the first call."""
        held = self._cut
        if held is None:
            with self._lock:
                held = (
                    self._cut
                    or resolve_setting_class(
                        "CUT",
                        scope=SCOPE,
                        base=NavigationCut,
                        shipped=VerifiedCut,
                        base_path="next.router.NavigationCut",
                    )()
                )
                self._cut = held
        return held

    def digest(self) -> ChromeDigest:
        """Return the digest `ROUTER["DIGEST"]` names, built on the first call."""
        held = self._digest
        if held is None:
            with self._lock:
                held = (
                    self._digest
                    or resolve_setting_class(
                        "DIGEST",
                        scope=SCOPE,
                        base=ChromeDigest,
                        shipped=ChromeDigest,
                        base_path="next.router.ChromeDigest",
                    )()
                )
                self._digest = held
        return held

    def entry(self) -> str:
        """Return the encoded `$router` entry, built once per prefix and URLconf."""
        return _placed(self._entries, router_entry)

    def speculation(self) -> str:
        """Return the encoded speculation rules, built once per prefix and URLconf."""
        return _placed(self._speculations, speculation_rules)


router_manager = RouterManager()
after_fork(router_manager.forget)


def forget_router(**kwargs: object) -> None:
    """Reset the router on a settings reload, recomposing pages once it switches.

    Composition splices the slot tag only while the router is on.
    """
    router_manager.forget()
    enabled = router_enabled()
    if router_manager.composed is not enabled:
        page.clear_template_caches()
    router_manager.composed = enabled


def forget_router_entry(*, setting: str, **kwargs: object) -> None:
    """Drop the `$router` entry and the rules once a setting they read moves."""
    if setting in _ENTRY_SETTINGS:
        router_manager.forget()


__all__ = [
    "RouterManager",
    "asset_prefixes",
    "exclude_prefixes",
    "forget_router",
    "forget_router_entry",
    "router_entry",
    "router_manager",
    "speculation_rules",
]
