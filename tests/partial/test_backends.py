import json

from django.test import override_settings

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


class TestBackendVersion:
    """The backend resolves the asset version stamped on a partial response."""

    def test_explicit_version_wins(self) -> None:
        backend = PartialProtocolBackend({"OPTIONS": {"VERSION": "abc123"}})
        assert backend.version() == "abc123"

    def test_manifest_sentinel_resolves_to_default(self) -> None:
        backend = PartialProtocolBackend({"OPTIONS": {"VERSION": "manifest"}})
        assert backend.version() == "0"

    def test_missing_version_resolves_to_default(self) -> None:
        assert PartialProtocolBackend().version() == "0"


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
