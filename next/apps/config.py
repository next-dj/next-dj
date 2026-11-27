"""`AppConfig` for next-dj that applies every area manifest, then installs the hooks."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, override

from django.apps import AppConfig
from django.core.signals import setting_changed

from next.conf.signals import settings_reloaded
from next.lifecycle import register_participant
from next.pages.declarations import register_declaration
from next.ports import page_scripts

from . import autodiscover, autoreload, components, staticfiles, templates
from .areas import CHECK_MODULES, MANIFESTS


if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from django.dispatch import Signal

    from next.manifest import Manifest


def _connect(signal: Signal, receiver: Callable[..., None]) -> None:
    """Connect `receiver` once, however often `ready` runs."""
    signal.connect(
        receiver, dispatch_uid=f"{receiver.__module__}.{receiver.__qualname__}"
    )


def _receivers(manifest: Manifest) -> None:
    """Connect every receiver `manifest` names, in its order."""
    for receiver in manifest.setting_changed:
        _connect(setting_changed, receiver)
    for receiver in manifest.settings_reloaded:
        _connect(settings_reloaded, receiver)
    for signal, receiver in manifest.connects:
        _connect(signal, receiver)


def _composed(manifest: Manifest) -> None:
    """Bind the ports of `manifest`, run its startup and register what it declares."""
    for binding in manifest.ports:
        binding.apply()
    for step in manifest.startup:
        step()
    for declaration in manifest.declarations:
        register_declaration(declaration)
    for participant in manifest.participants:
        register_participant(participant)
    for provider in manifest.providers:
        page_scripts.register(provider)


class NextFrameworkConfig(AppConfig):
    """Apply the area manifests, then connect autoreload, templates and finders."""

    name = "next"
    verbose_name = "Next Django Framework"
    check_modules: ClassVar[tuple[ModuleType, ...]] = CHECK_MODULES

    @override
    def ready(self) -> None:
        """Connect every receiver, compose every area, then install the hooks.

        The resolver and the ports come before the installs, which import user modules.
        """
        for manifest in MANIFESTS:
            _receivers(manifest)
        for manifest in MANIFESTS:
            _composed(manifest)
        autoreload.install()
        templates.install()
        staticfiles.install()
        autodiscover.install()
        components.install()


__all__ = ["NextFrameworkConfig"]
