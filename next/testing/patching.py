"""Context managers for test-scoped overrides.

Each helper is a plain `@contextlib.contextmanager` so the public
surface does not depend on pytest or `unittest.mock`. State is restored
on exit, including when the block raises.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from django.conf import settings as django_settings
from django.test import override_settings

from next.components.manager import components_manager
from next.deps.resolver import resolver
from next.forms.backends import ActionRegistration, file_to_dotted_module
from next.forms.manager import form_action_manager
from next.static.manager import default_manager


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from django import forms as django_forms

    from next.deps.providers import ParameterProvider
    from next.static.collector import StaticCollector


_MISSING: Any = object()


@contextlib.contextmanager
def override_next_settings(**overrides: object) -> Iterator[None]:
    """Merge `overrides` into `NEXT_FRAMEWORK` for the duration of the block.

    The merge is shallow: top-level keys supplied as kwargs replace any
    values present in the current `NEXT_FRAMEWORK`. Relies on Django's
    `override_settings` underneath, so the `setting_changed` →
    `settings_reloaded` signal chain fires automatically and framework
    managers pick up the new values.
    """
    current = getattr(django_settings, "NEXT_FRAMEWORK", None) or {}
    merged = {**current, **overrides}
    with override_settings(NEXT_FRAMEWORK=merged):
        yield


@contextlib.contextmanager
def override_dependency(name: str, value: object) -> Iterator[None]:
    """Bind `Depends(name)` to `value` for the block.

    Any previous dependency registered under `name` is restored on exit.
    """
    previous = resolver._dependency_callables.get(name, _MISSING)
    resolver._dependency_callables[name] = lambda: value
    try:
        yield
    finally:
        if previous is _MISSING:
            resolver._dependency_callables.pop(name, None)
        else:
            resolver._dependency_callables[name] = previous


@contextlib.contextmanager
def override_provider(provider: ParameterProvider) -> Iterator[None]:
    """Prepend `provider` to the resolver's provider list for the block.

    Placing the provider first means it wins over any auto-registered
    provider that would otherwise handle the same parameter.
    """
    resolver._ensure_providers()
    resolver._providers.insert(0, provider)
    try:
        yield
    finally:
        with contextlib.suppress(ValueError):
            resolver._providers.remove(provider)


@contextlib.contextmanager
def override_form_action(
    name: str,
    handler: Callable[..., Any],
    *,
    form_class: type[django_forms.Form] | Callable[..., Any] | None = None,
) -> Iterator[None]:
    """Register `handler` as the named form action for the block.

    The override wins name-based lookup for the block even when an
    action with the same name already exists. The full registry state
    is snapshotted on entry and restored on exit, so previously
    registered actions with the same name survive.
    """
    backend = form_action_manager.default_backend
    snapshots = {
        attr: dict(getattr(backend, attr, {}))
        for attr in ("_registry", "_uid_to_name", "_name_index", "_url_cache")
    }
    form_action_manager.register_action(
        ActionRegistration(
            name=name,
            file_path=__file__,
            scope="shared",
            handler=handler,
            form_class=form_class,
        )
    )
    name_index = getattr(backend, "_name_index", None)
    if name_index is not None:
        name_index[name] = (file_to_dotted_module(__file__), name)
    try:
        yield
    finally:
        for attr, snapshot in snapshots.items():
            mapping = getattr(backend, attr, None)
            if mapping is not None:
                mapping.clear()
                mapping.update(snapshot)


@contextlib.contextmanager
def override_component_backends(*configs: dict[str, Any]) -> Iterator[None]:
    """Replace `COMPONENT_BACKENDS` for the block.

    Uses `override_next_settings` so the `settings_reloaded` signal
    rebuilds `components_manager`'s backends automatically.
    """
    with override_next_settings(COMPONENT_BACKENDS=list(configs)):
        components_manager._ensure_backends()
        yield


class StaticCollectorProxy:
    """Handle that exposes the collector most recently built inside a patch."""

    def __init__(self) -> None:
        """Initialise with no captured collector."""
        self.collector: StaticCollector | None = None


@contextlib.contextmanager
def patch_static_collector(
    factory: Callable[[], StaticCollector] | None = None,
    *,
    capture: bool = False,
) -> Iterator[StaticCollectorProxy | None]:
    """Replace `default_manager.create_collector` for the block.

    When `capture` is True a `StaticCollectorProxy` is yielded. Its
    `.collector` attribute is set on each call to the patched factory
    so tests can inspect emitted styles/scripts without parsing HTML.
    """
    # Trigger lazy initialisation via an attribute access. Avoid
    # `_setup()` because that rebuilds the wrapped manager from scratch.
    _ = default_manager.create_collector
    manager = default_manager._wrapped
    original = manager.create_collector
    proxy = StaticCollectorProxy()

    def _create() -> StaticCollector:
        collector = factory() if factory is not None else original()
        proxy.collector = collector
        return collector

    manager.create_collector = _create  # type: ignore[method-assign]
    try:
        yield proxy if capture else None
    finally:
        manager.create_collector = original  # type: ignore[method-assign]


__all__ = [
    "StaticCollectorProxy",
    "override_component_backends",
    "override_dependency",
    "override_form_action",
    "override_next_settings",
    "override_provider",
    "patch_static_collector",
]
