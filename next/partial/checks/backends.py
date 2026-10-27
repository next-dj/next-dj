"""System checks for the `PARTIAL_BACKENDS` entries and what they ask of staticfiles."""

from typing import Final

from django.conf import settings
from django.contrib.staticfiles.storage import ManifestFilesMixin
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT
from next.conf import import_class_cached, next_framework_settings
from next.partial.backends import PartialProtocolBackend
from next.partial.manager import MANIFEST_VERSION, PARTIAL_BACKENDS_KEY, VERSION_OPTION
from next.partial.sse import (
    HEARTBEAT_OPTION,
    RETRY_OPTION,
    SSE_OPTION,
    heartbeat_seconds_valid,
    retry_ms_valid,
)

from .codes import (
    E_BACKEND_STALE_CONTRACT,
    E_BACKEND_WITHOUT_PATH,
    E_BACKENDS_NOT_A_LIST,
    E_SSE_OPTION_INVALID,
    W_ASSET_VERSION_FROZEN,
    W_MANIFEST_VERSION_NO_STORAGE,
    W_TOO_MANY_BACKENDS,
)


_STATICFILES_ALIAS: Final = "staticfiles"
_RETIRED_HOOK: Final = "sse_event"
_STREAM_HOOK: Final = "stream_data"


def partial_backend_configs() -> list[object]:
    """Return PARTIAL_BACKENDS as a list, tolerating any malformed shape."""
    configs = getattr(next_framework_settings, PARTIAL_BACKENDS_KEY, ())
    if isinstance(configs, list | tuple):
        return list(configs)
    return []


def partial_backends_active() -> bool:
    """Return True when at least one partial protocol backend is configured."""
    return any(isinstance(config, dict) for config in partial_backend_configs())


@register(NEXT)
def check_partial_backends_is_a_list(*args, **kwargs) -> list[CheckMessage]:
    """Error when `PARTIAL_BACKENDS` is not a list (`next.E067`).

    The settings layer silently drops a non-list value, so only this reports it.
    """
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if not isinstance(raw, dict):
        return []
    configs = raw.get(PARTIAL_BACKENDS_KEY)
    if configs is None or isinstance(configs, list):
        return []
    return [
        Error(
            f"NEXT_FRAMEWORK[{PARTIAL_BACKENDS_KEY!r}] must be a list. The "
            "value is ignored, so the default protocol backend loads instead "
            "of the configured one.",
            obj=settings,
            id=E_BACKENDS_NOT_A_LIST,
        )
    ]


@register(NEXT)
def check_single_partial_backend(*args, **kwargs) -> list[CheckMessage]:
    """Warn when more than one partial protocol backend is configured (`next.W071`).

    Only the first valid PARTIAL_BACKENDS entry runs, any other is dead config.
    """
    valid = [config for config in partial_backend_configs() if isinstance(config, dict)]
    if len(valid) <= 1:
        return []
    return [
        DjangoWarning(
            "PARTIAL_BACKENDS has more than one backend entry, but partial "
            "rendering uses a single protocol backend. Only the first entry "
            "runs, the rest are ignored. Keep one PARTIAL_BACKENDS entry.",
            id=W_TOO_MANY_BACKENDS,
        )
    ]


@register(NEXT)
def check_partial_backend_names_a_path(*args, **kwargs) -> list[CheckMessage]:
    """Error when a PARTIAL_BACKENDS entry omits its BACKEND key (`next.E073`).

    Such an entry falls back to the default backend, so the wire format never loads.
    """
    messages: list[CheckMessage] = []
    for index, config in enumerate(partial_backend_configs()):
        if not isinstance(config, dict) or "BACKEND" in config:
            continue
        messages.append(
            Error(
                f"PARTIAL_BACKENDS entry {index} has no BACKEND key. Every "
                "entry names its protocol backend by dotted path under "
                "BACKEND, so add it or drop the entry.",
                id=E_BACKEND_WITHOUT_PATH,
            )
        )
    return messages


@register(NEXT)
def check_partial_backend_sse_options(*args, **kwargs) -> list[CheckMessage]:
    """Error when a backend's SSE options hold an unusable value (`next.E214`).

    The stream falls back to the default for such a value, so only this reports it.
    """
    messages: list[CheckMessage] = []
    for index, config in enumerate(partial_backend_configs()):
        if not isinstance(config, dict):
            continue
        options = config.get("OPTIONS")
        sse = options.get(SSE_OPTION) if isinstance(options, dict) else None
        if sse is None:
            continue
        if not isinstance(sse, dict):
            messages.append(
                Error(
                    f"PARTIAL_BACKENDS entry {index} sets OPTIONS[{SSE_OPTION!r}] "
                    f"to {sse!r}, which is not a dict, so every SSE option falls "
                    "back to its default.",
                    hint=f"Set {SSE_OPTION} to a dict of {RETRY_OPTION} and "
                    f"{HEARTBEAT_OPTION}.",
                    id=E_SSE_OPTION_INVALID,
                )
            )
            continue
        messages.extend(_sse_option_errors(index, sse))
    return messages


def _sse_option_errors(index: int, sse: dict[str, object]) -> list[CheckMessage]:
    """Return one error per SSE option of an entry that the stream cannot use."""
    rules = (
        (RETRY_OPTION, retry_ms_valid, "an int of milliseconds from 0"),
        (HEARTBEAT_OPTION, heartbeat_seconds_valid, "a finite number above 0"),
    )
    messages: list[CheckMessage] = []
    for option, valid, expected in rules:
        value = sse.get(option)
        if value is None or valid(value):
            continue
        messages.append(
            Error(
                f"PARTIAL_BACKENDS entry {index} sets SSE {option} to {value!r}, "
                "which the stream ignores in favour of the default.",
                hint=f"Set SSE {option} to {expected}.",
                id=E_SSE_OPTION_INVALID,
            )
        )
    return messages


@register(NEXT)
def check_partial_backend_implements_stream_data(*args, **kwargs) -> list[CheckMessage]:
    """Error when a backend keeps the retired `sse_event` contract (`next.E215`).

    Such a class is abstract or has its override ignored, and nothing else says why.
    """
    messages: list[CheckMessage] = []
    for index, config in enumerate(partial_backend_configs()):
        backend_class = _backend_class(config)
        if backend_class is None:
            continue
        retired = any(_RETIRED_HOOK in vars(klass) for klass in backend_class.__mro__)
        missing = _STREAM_HOOK in getattr(backend_class, "__abstractmethods__", ())
        if not (retired or missing):
            continue
        problem = (
            f"defines {_RETIRED_HOOK}(), which no stream calls any more"
            if retired
            else f"does not implement {_STREAM_HOOK}()"
        )
        messages.append(
            Error(
                f"PARTIAL_BACKENDS entry {index} names "
                f"{backend_class.__qualname__}, which {problem}.",
                hint=f"Replace {_RETIRED_HOOK}(envelope) with "
                f"{_STREAM_HOOK}(envelope, meta), which returns the frame as a "
                "JSON object or as the codec's text.",
                obj=backend_class,
                id=E_BACKEND_STALE_CONTRACT,
            )
        )
    return messages


def _backend_class(config: object) -> type[PartialProtocolBackend] | None:
    """Return the protocol backend class an entry names, or None when it names none."""
    dotted = config.get("BACKEND") if isinstance(config, dict) else None
    if not isinstance(dotted, str) or not dotted:
        return None
    try:
        klass = import_class_cached(dotted)
    except ImportError:
        return None
    if isinstance(klass, type) and issubclass(klass, PartialProtocolBackend):
        return klass
    return None


@register(NEXT)
def check_manifest_version_has_manifest_storage(*args, **kwargs) -> list[CheckMessage]:
    """Warn when manifest versioning has no manifest storage (`next.W069`).

    Without a hashing storage the guard silently never asks a client to reload.
    """
    if not _manifest_version_requested():
        return []
    if _staticfiles_storage_is_manifest():
        return []
    return [
        DjangoWarning(
            'A partial backend sets VERSION: "manifest", but the staticfiles '
            "storage does not hash files into a manifest. The asset-version "
            "guard stays silent, so a deploy of new assets cannot ask clients "
            "to reload. Use a ManifestStaticFilesStorage, or set an explicit "
            "VERSION string to pin the version yourself.",
            id=W_MANIFEST_VERSION_NO_STORAGE,
        )
    ]


@register(NEXT, deploy=True)
def check_asset_version_moves_between_deploys(*args, **kwargs) -> list[CheckMessage]:
    """Warn when no deploy can move the partial asset version (`next.W083`).

    A development checkout matches by design, so it is a deployment audit only.
    """
    if not partial_backends_active() or _version_option_named():
        return []
    if next_framework_settings.STATIC_VERSION or _staticfiles_storage_is_manifest():
        return []
    return [
        DjangoWarning(
            "No PARTIAL_BACKENDS entry names a VERSION, STATIC_VERSION is "
            "unset, and the staticfiles storage does not hash files into a "
            "manifest, so every deploy stamps the same asset version and the "
            "guard cannot ask an open client to reload stale assets. Set "
            "STATIC_VERSION to the build id the deploy carries, or use a "
            "ManifestStaticFilesStorage.",
            id=W_ASSET_VERSION_FROZEN,
        )
    ]


def _configured_versions() -> list[object]:
    """Return the `VERSION` option every well-shaped backend entry names."""
    versions: list[object] = []
    for config in partial_backend_configs():
        if not isinstance(config, dict):
            continue
        options = config.get("OPTIONS")
        options = options if isinstance(options, dict) else {}
        versions.append(options.get(VERSION_OPTION))
    return versions


def _manifest_version_requested() -> bool:
    """Return True when a partial backend names the manifest sentinel itself."""
    return any(version == MANIFEST_VERSION for version in _configured_versions())


def _version_option_named() -> bool:
    """Return True when a partial backend states a version source of its own."""
    return any(isinstance(version, str) for version in _configured_versions())


def _staticfiles_storage_is_manifest() -> bool:
    """Return True when the configured staticfiles storage hashes its files.

    Reads the dotted path, not the proxy, so a missing STATIC_ROOT is safe.
    """
    backend_path = _staticfiles_storage_path()
    if backend_path is None:
        return False
    try:
        storage_class = import_class_cached(backend_path)
    except ImportError:
        return False
    return isinstance(storage_class, type) and issubclass(
        storage_class, ManifestFilesMixin
    )


def _staticfiles_storage_path() -> str | None:
    """Return the dotted path of the configured staticfiles storage backend."""
    storages = getattr(settings, "STORAGES", None)
    if isinstance(storages, dict):
        entry = storages.get(_STATICFILES_ALIAS)
        if isinstance(entry, dict):
            backend = entry.get("BACKEND")
            return backend if isinstance(backend, str) else None
    return None


__all__ = [
    "check_asset_version_moves_between_deploys",
    "check_manifest_version_has_manifest_storage",
    "check_partial_backend_implements_stream_data",
    "check_partial_backend_names_a_path",
    "check_partial_backend_sse_options",
    "check_partial_backends_is_a_list",
    "check_single_partial_backend",
    "partial_backend_configs",
    "partial_backends_active",
]
