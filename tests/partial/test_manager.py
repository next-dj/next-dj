import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

from django.contrib.staticfiles.storage import ManifestFilesMixin
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.utils.functional import SimpleLazyObject

from next.conf.signals import settings_reloaded
from next.partial import PartialProtocolBackend
from next.partial.manager import asset_version, partial_backend_manager


_DEFAULT_BACKEND = "next.partial.PartialProtocolBackend"


@contextmanager
def _backend_options(options: dict[str, object]) -> Iterator[None]:
    """Run the body against a protocol backend configured with `options`.

    Entering and leaving `override_settings` reloads the framework
    settings, which resets the shared manager on both edges, so the body
    sees exactly the entry declared here.
    """
    config = {"BACKEND": _DEFAULT_BACKEND, "OPTIONS": options}
    with override_settings(NEXT_FRAMEWORK={"PARTIAL_BACKENDS": [config]}):
        yield


class _RecordedHashStorage(ManifestFilesMixin):
    """A manifest storage exposing a precomputed manifest hash."""

    def __init__(self, manifest_hash: str) -> None:
        self.manifest_hash = manifest_hash
        self.hashed_files: dict[str, str] = {}


class _MappingOnlyStorage(ManifestFilesMixin):
    """A manifest storage with a path mapping but no recorded hash."""

    def __init__(self, hashed_files: dict[str, str]) -> None:
        self.manifest_hash = ""
        self.hashed_files = hashed_files


class _PlainStorage:
    """A non-manifest storage with no version source."""


def _expected_mapping_hash(hashed_files: dict[str, str]) -> str:
    payload = json.dumps(sorted(hashed_files.items()), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


class TestAssetVersion:
    """`asset_version` resolves the version stamped on a partial response."""

    def test_explicit_version_wins(self) -> None:
        # an explicit VERSION string wins over the manifest sentinel even when a
        # manifest storage is active, so a release tag pins the version
        storage = _RecordedHashStorage("deadbeef")
        with (
            patch("next.partial.manager.staticfiles_storage", storage),
            _backend_options({"VERSION": "abc123"}),
        ):
            assert asset_version() == "abc123"

    def test_manifest_sentinel_uses_recorded_hash(self) -> None:
        storage = _RecordedHashStorage("9f3c2e1bcafe")
        with (
            patch("next.partial.manager.staticfiles_storage", storage),
            _backend_options({"VERSION": "manifest"}),
        ):
            assert asset_version() == "9f3c2e1bcafe"

    def test_manifest_sentinel_hashes_mapping_without_recorded_hash(self) -> None:
        files = {"app/a.css": "app/a.abc.css", "app/b.js": "app/b.def.js"}
        storage = _MappingOnlyStorage(files)
        with (
            patch("next.partial.manager.staticfiles_storage", storage),
            _backend_options({"VERSION": "manifest"}),
        ):
            assert asset_version() == _expected_mapping_hash(files)

    def test_mapping_hash_is_stable_across_calls(self) -> None:
        storage = _MappingOnlyStorage({"x.css": "x.1.css"})
        with (
            patch("next.partial.manager.staticfiles_storage", storage),
            _backend_options({"VERSION": "manifest"}),
        ):
            assert asset_version() == asset_version()

    def test_manifest_sentinel_falls_back_without_manifest_storage(self) -> None:
        with (
            patch("next.partial.manager.staticfiles_storage", _PlainStorage()),
            _backend_options({"VERSION": "manifest"}),
        ):
            assert asset_version() == "0"

    def test_missing_version_resolves_to_default(self) -> None:
        # an absent VERSION option defaults to the manifest sentinel, which
        # falls back to the stable default under the plain test storage
        with (
            patch("next.partial.manager.staticfiles_storage", _PlainStorage()),
            _backend_options({}),
        ):
            assert asset_version() == "0"

    def test_unconfigured_storage_falls_back_to_default(self) -> None:
        # the lazy storage proxy raises ImproperlyConfigured when STATIC_ROOT is
        # unset, and the guard degrades to the stable default rather than 500ing

        def _unconfigured() -> object:
            raise ImproperlyConfigured

        proxy = SimpleLazyObject(_unconfigured)
        with (
            patch("next.partial.manager.staticfiles_storage", proxy),
            _backend_options({"VERSION": "manifest"}),
        ):
            assert asset_version() == "0"


class TestPartialBackendSelection:
    """The shared manager serves the backend named by `PARTIAL_BACKENDS`."""

    def test_get_returns_the_configured_backend(self) -> None:
        with _backend_options({"VERSION": "pinned"}):
            backend = partial_backend_manager.get()

            assert isinstance(backend, PartialProtocolBackend)
            assert backend.options == {"VERSION": "pinned"}

    def test_default_backend_serves_an_unconfigured_project(self) -> None:
        # the bundled entry names the default protocol backend, so a project
        # that lists no entry of its own still gets a working wire format
        with override_settings(NEXT_FRAMEWORK={"PARTIAL_BACKENDS": []}):
            assert isinstance(partial_backend_manager.get(), PartialProtocolBackend)

    def test_reset_rebuilds_the_backend(self) -> None:
        first = partial_backend_manager.get()

        partial_backend_manager.reset()

        assert partial_backend_manager.get() is not first

    def test_reset_lets_a_new_config_take_effect(self) -> None:
        with _backend_options({"VERSION": "first"}):
            partial_backend_manager.get()
        with _backend_options({"VERSION": "second"}):
            partial_backend_manager.reset()

            assert partial_backend_manager.get().options == {"VERSION": "second"}

    def test_settings_reloaded_drops_the_cached_backend(self) -> None:
        first = partial_backend_manager.get()

        settings_reloaded.send(sender=self.__class__)

        assert partial_backend_manager.get() is not first
