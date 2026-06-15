import hashlib
import json
from unittest.mock import patch

from django.contrib.staticfiles.storage import ManifestFilesMixin
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.utils.functional import SimpleLazyObject

from next.conf.signals import settings_reloaded
from next.partial import Envelope, PartialProtocolBackend, Patches
from next.partial.backends import (
    PartialBackendManager,
    PartialProtocolFactory,
    partial_backend_manager,
)
from next.partial.headers import CONTENT_TYPE


def _sample_envelope() -> Envelope:
    return (
        Patches("9f3c2e1b")
        .replace({"zone": "list"}, "<div></div>")
        .event("saved", {"id": 7})
        .envelope()
    )


class TestSerializeEnvelope:
    """The default backend serialises envelopes as compact JSON bytes."""

    def test_content_type_is_vendor_mime(self) -> None:
        assert PartialProtocolBackend().content_type == CONTENT_TYPE

    def test_serialize_returns_bytes(self) -> None:
        body = PartialProtocolBackend().serialize_envelope(_sample_envelope())
        assert isinstance(body, bytes)

    def test_serialize_is_compact(self) -> None:
        body = PartialProtocolBackend().serialize_envelope(_sample_envelope())
        assert b", " not in body
        assert b": " not in body

    def test_serialize_round_trips(self) -> None:
        body = PartialProtocolBackend().serialize_envelope(_sample_envelope())
        data = json.loads(body)
        assert data["version"] == "9f3c2e1b"
        assert data["ops"][0]["op"] == "replace"
        assert data["ops"][1]["op"] == "event"

    def test_serialize_keeps_non_ascii(self) -> None:
        envelope = Patches("v1").event("сохранено").envelope()
        body = PartialProtocolBackend().serialize_envelope(envelope)
        assert "сохранено".encode() in body


class TestSseEvent:
    """The SSE frame wraps the same JSON envelope as a `next-patches` event."""

    def test_event_name_and_data(self) -> None:
        frame = PartialProtocolBackend().sse_event(_sample_envelope())
        assert frame.startswith("event: next-patches\n")
        assert frame.endswith("\n\n")

    def test_event_carries_same_json_as_body(self) -> None:
        backend = PartialProtocolBackend()
        envelope = _sample_envelope()
        body = backend.serialize_envelope(envelope).decode()
        frame = backend.sse_event(envelope)
        data_line = frame.splitlines()[1]
        assert data_line == f"data: {body}"


class TestBackendOptions:
    """OPTIONS from the settings entry are exposed on the backend."""

    def test_options_read_from_config(self) -> None:
        backend = PartialProtocolBackend({"OPTIONS": {"DEFAULT_SWAP": "morph"}})
        assert backend.options["DEFAULT_SWAP"] == "morph"

    def test_options_default_empty(self) -> None:
        assert PartialProtocolBackend().options == {}

    def test_non_dict_options_falls_back_to_empty(self) -> None:
        backend = PartialProtocolBackend({"OPTIONS": "bogus"})
        assert backend.options == {}


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


class TestBackendVersion:
    """The backend resolves the asset version stamped on a partial response."""

    def test_explicit_version_wins(self) -> None:
        # an explicit VERSION string wins over the manifest sentinel even when a
        # manifest storage is active, so a release tag pins the version
        storage = _RecordedHashStorage("deadbeef")
        with patch("next.partial.backends.staticfiles_storage", storage):
            backend = PartialProtocolBackend({"OPTIONS": {"VERSION": "abc123"}})
            assert backend.version() == "abc123"

    def test_manifest_sentinel_uses_recorded_hash(self) -> None:
        storage = _RecordedHashStorage("9f3c2e1bcafe")
        with patch("next.partial.backends.staticfiles_storage", storage):
            backend = PartialProtocolBackend({"OPTIONS": {"VERSION": "manifest"}})
            assert backend.version() == "9f3c2e1bcafe"

    def test_manifest_sentinel_hashes_mapping_without_recorded_hash(self) -> None:
        files = {"app/a.css": "app/a.abc.css", "app/b.js": "app/b.def.js"}
        storage = _MappingOnlyStorage(files)
        with patch("next.partial.backends.staticfiles_storage", storage):
            backend = PartialProtocolBackend({"OPTIONS": {"VERSION": "manifest"}})
            assert backend.version() == _expected_mapping_hash(files)

    def test_mapping_hash_is_stable_across_calls(self) -> None:
        files = {"x.css": "x.1.css"}
        storage = _MappingOnlyStorage(files)
        with patch("next.partial.backends.staticfiles_storage", storage):
            backend = PartialProtocolBackend({"OPTIONS": {"VERSION": "manifest"}})
            assert backend.version() == backend.version()

    def test_manifest_sentinel_falls_back_without_manifest_storage(self) -> None:
        with patch("next.partial.backends.staticfiles_storage", _PlainStorage()):
            backend = PartialProtocolBackend({"OPTIONS": {"VERSION": "manifest"}})
            assert backend.version() == "0"

    def test_missing_version_resolves_to_default(self) -> None:
        # an absent VERSION option defaults to the manifest sentinel, which
        # falls back to the stable default under the plain test storage
        with patch("next.partial.backends.staticfiles_storage", _PlainStorage()):
            assert PartialProtocolBackend().version() == "0"

    def test_unconfigured_storage_falls_back_to_default(self) -> None:
        # the lazy storage proxy raises ImproperlyConfigured when STATIC_ROOT is
        # unset, and the guard degrades to the stable default rather than 500ing

        def _unconfigured() -> object:
            raise ImproperlyConfigured

        proxy = SimpleLazyObject(_unconfigured)
        with patch("next.partial.backends.staticfiles_storage", proxy):
            backend = PartialProtocolBackend({"OPTIONS": {"VERSION": "manifest"}})
            assert backend.version() == "0"


class TestFactory:
    """The factory instantiates the backend named in a settings entry."""

    def test_create_backend(self) -> None:
        config = {"BACKEND": "next.partial.PartialProtocolBackend", "OPTIONS": {}}
        backend = PartialProtocolFactory.create_backend(config)
        assert isinstance(backend, PartialProtocolBackend)


class TestManager:
    """The manager caches the first configured backend with a reset hook."""

    def test_get_returns_default_backend(self) -> None:
        manager = PartialBackendManager()
        assert isinstance(manager.get(), PartialProtocolBackend)

    def test_get_is_cached(self) -> None:
        manager = PartialBackendManager()
        assert manager.get() is manager.get()

    def test_reset_drops_cache(self) -> None:
        manager = PartialBackendManager()
        first = manager.get()
        manager.reset()
        assert manager.get() is not first

    def test_skips_non_dict_config(self) -> None:
        manager = PartialBackendManager()
        with override_settings(NEXT_FRAMEWORK={"PARTIAL_BACKENDS": ["bogus"]}):
            settings_reloaded.send(sender=self.__class__)
            assert isinstance(manager.get(), PartialProtocolBackend)

    def test_global_manager_resets_on_settings_reloaded(self) -> None:
        first = partial_backend_manager.get()
        settings_reloaded.send(sender=self.__class__)
        assert partial_backend_manager.get() is not first
