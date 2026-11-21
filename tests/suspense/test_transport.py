from unittest.mock import patch

import pytest
from django.http import HttpRequest
from django.test import AsyncRequestFactory, RequestFactory

from next.defer import Delivery
from next.errors import BackendImportError
from next.suspense.manager import suspense_manager
from next.suspense.markers import Server
from next.suspense.policy import DefaultStreamPolicy, Delivered, page_facts
from next.suspense.transport import (
    DefaultStreamTransport,
    StreamTransport,
    TransportFacts,
    transport_of,
    transports,
)
from next.testing import override_next_settings


_THREADED = TransportFacts(True, True, "wsgi", "wsgi.multithread")
_SYNC = TransportFacts(True, False, "wsgi", "wsgi.multithread")
_ASGI = TransportFacts(True, True, "asgi", "asgi")


def _wsgi(*, threaded: bool = True, **extra: object) -> HttpRequest:
    return RequestFactory().get("/", **{"wsgi.multithread": threaded, **extra})


def _asgi(**scope: object) -> HttpRequest:
    request = AsyncRequestFactory().get("/")
    request.scope.update(scope)
    return request


def _facts(request: HttpRequest, **options: object) -> TransportFacts:
    return DefaultStreamTransport({"OPTIONS": options}).facts(request)


class _Buffering(StreamTransport):
    """A project transport for a host that buffers every response."""

    def facts(self, request: HttpRequest) -> TransportFacts:
        return TransportFacts(False, True, "host", "host")


class TestServerClaims:
    """With no option and no mark the server's own claims decide."""

    @pytest.mark.parametrize(
        ("request_", "expected"),
        [
            (_wsgi(threaded=True), _THREADED),
            (_wsgi(threaded=False), _SYNC),
            (RequestFactory().get("/"), _SYNC),
            (_asgi(), _ASGI),
        ],
        ids=["threaded", "single_thread", "unclaimed", "asgi"],
    )
    def test_the_claims_answer(self, request_: HttpRequest, expected) -> None:
        assert _facts(request_) == expected

    def test_an_answer_is_built_once(self) -> None:
        transport = DefaultStreamTransport()
        assert transport.facts(_wsgi()) is transport.facts(_wsgi())


_MARKS = {
    "WSGI": {"KEYS": ["adapter.event"], "PREFIXES": ["adapter.", "bridge."]},
    "ASGI": {"KEYS": ["adapter.event", "adapter.context"]},
}


class TestBufferedMarks:
    """A mark the project lists makes the transport buffered where a request has it."""

    @pytest.mark.parametrize(
        ("request_", "reason"),
        [
            (_wsgi(**{"adapter.event": {}}), "adapter.event"),
            (_wsgi(**{"adapter.full_event": {}}), "adapter."),
            (_wsgi(**{"bridge.context": {}}), "bridge."),
            (_asgi(**{"adapter.event": {}}), "adapter.event"),
            (_asgi(**{"adapter.context": {}}), "adapter.context"),
        ],
        ids=["wsgi_key", "wsgi_prefix", "wsgi_second_prefix", "asgi_key", "asgi_ctx"],
    )
    def test_a_mark_buffers(self, request_: HttpRequest, reason: str) -> None:
        facts = _facts(request_, BUFFERED_MARKS=_MARKS)
        assert (facts.incremental, facts.incremental_reason) == (False, reason)
        assert facts.reason == reason

    def test_a_mark_leaves_the_concurrency_to_the_claims(self) -> None:
        request = _wsgi(threaded=False, **{"adapter.event": {}})
        facts = _facts(request, BUFFERED_MARKS=_MARKS)
        assert (facts.concurrent, facts.concurrent_reason) == (
            False,
            "wsgi.multithread",
        )

    def test_a_key_sharing_only_the_first_character_is_no_mark(self) -> None:
        assert _facts(_wsgi(apple=1, bridge=1), BUFFERED_MARKS=_MARKS) == _THREADED

    def test_a_kind_reads_only_its_own_marks(self) -> None:
        assert _facts(_asgi(**{"bridge.id": 1}), BUFFERED_MARKS=_MARKS) == _ASGI

    @pytest.mark.parametrize("table", [None, "x", {}], ids=["none", "text", "empty"])
    def test_no_table_marks_nothing(self, table: object) -> None:
        assert _facts(_wsgi(**{"adapter.event": {}}), BUFFERED_MARKS=table) == _THREADED
        assert _facts(_asgi(**{"adapter.event": {}}), BUFFERED_MARKS=table) == _ASGI

    def test_the_default_settings_mark_nothing(self) -> None:
        request = _wsgi(**{"adapter.event": {}})
        assert DefaultStreamTransport().facts(request) == _THREADED

    def test_a_table_missing_a_kind_or_a_list_marks_nothing_there(self) -> None:
        table = {"WSGI": "adapter.event", "ASGI": {"KEYS": "adapter.event"}}
        assert _facts(_wsgi(**{"adapter.event": {}}), BUFFERED_MARKS=table) == (
            _THREADED
        )
        assert _facts(_asgi(**{"adapter.event": {}}), BUFFERED_MARKS=table) == _ASGI

    def test_a_blank_or_foreign_entry_of_a_list_is_skipped(self) -> None:
        table = {"WSGI": {"KEYS": ["", 3, "adapter.event"]}}
        facts = _facts(_wsgi(**{"adapter.event": {}}), BUFFERED_MARKS=table)
        assert facts.incremental_reason == "adapter.event"

    def test_the_settings_table_is_read(self) -> None:
        options = {"BUFFERED_MARKS": {"ASGI": {"PREFIXES": ["adapter."]}}}
        with override_next_settings(DEFER={"TRANSPORT": {"OPTIONS": options}}):
            assert transport_of(_asgi(**{"adapter.id": 1})).reason == "adapter."


class TestOptions:
    """An option states the truth, over every mark and every claim."""

    def test_incremental_true_overrides_a_mark(self) -> None:
        request = _wsgi(**{"adapter.event": {}})
        facts = _facts(request, INCREMENTAL=True, BUFFERED_MARKS=_MARKS)
        assert (facts.incremental, facts.incremental_reason) == (True, "option")

    def test_incremental_false_buffers_a_streaming_server(self) -> None:
        for request in (_wsgi(), _asgi()):
            facts = _facts(request, INCREMENTAL=False)
            assert (facts.incremental, facts.reason) == (False, "option")

    def test_concurrent_true_overrides_a_single_thread_claim(self) -> None:
        facts = _facts(_wsgi(threaded=False), CONCURRENT=True)
        assert facts == TransportFacts(True, True, "wsgi", "option")

    def test_concurrent_false_holds_back_an_asgi_server(self) -> None:
        facts = _facts(_asgi(), CONCURRENT=False)
        assert facts == TransportFacts(True, False, "asgi", "option")
        assert facts.reason == "option"

    def test_a_value_other_than_a_flag_detects(self) -> None:
        assert _facts(_wsgi(threaded=False), CONCURRENT="yes") == _SYNC

    def test_an_absent_option_reads_the_settings(self) -> None:
        options = {"CONCURRENT": True}
        with override_next_settings(DEFER={"TRANSPORT": {"OPTIONS": options}}):
            assert DefaultStreamTransport().facts(_wsgi(threaded=False)).concurrent

    def test_a_non_mapping_options_entry_is_empty(self) -> None:
        assert DefaultStreamTransport({"OPTIONS": "x"}).options == {}


class TestDelivery:
    """The policy streams what the transport lets through and inlines a buffered one."""

    def test_a_single_thread_claim_made_concurrent_streams(self) -> None:
        options = {"CONCURRENT": True}
        with override_next_settings(DEFER={"TRANSPORT": {"OPTIONS": options}}):
            request = _wsgi(threaded=False)
            facts = page_facts(request)
            delivered = suspense_manager.policy().delivery(request, facts)
        assert facts.server is Server.WSGI_THREADED
        assert delivered == Delivered(Delivery.STREAM, "stream")

    def test_a_buffering_adapter_is_inline_with_sync_workers_streaming(self) -> None:
        request = _wsgi(**{"adapter.full_event": {}})
        policy = DefaultStreamPolicy({"OPTIONS": {"SYNC_WORKERS": "stream"}})
        options = {"BUFFERED_MARKS": {"WSGI": {"PREFIXES": ["adapter."]}}}
        with override_next_settings(DEFER={"TRANSPORT": {"OPTIONS": options}}):
            delivered = policy.delivery(request, page_facts(request))
        assert delivered == Delivered(Delivery.INLINE, "buffered")

    def test_a_proxy_stated_as_buffering_is_inline(self) -> None:
        options = {"INCREMENTAL": False}
        with override_next_settings(DEFER={"TRANSPORT": {"OPTIONS": options}}):
            request = _wsgi()
            delivered = suspense_manager.policy().delivery(request, page_facts(request))
        assert delivered == Delivered(Delivery.INLINE, "buffered")


class TestBackend:
    """The settings name the backend, built once and dropped on a settings reload."""

    def test_a_project_backend_answers(self) -> None:
        path = f"{__name__}._Buffering"
        with override_next_settings(DEFER={"TRANSPORT": {"BACKEND": path}}):
            assert isinstance(suspense_manager.transport(), _Buffering)
            assert transport_of(_wsgi()).incremental_reason == "host"

    def test_a_path_that_does_not_import_is_refused(self) -> None:
        backend = {"BACKEND": "nowhere.Transport"}
        with (
            override_next_settings(DEFER={"TRANSPORT": backend}),
            pytest.raises(BackendImportError),
        ):
            transports.backend()

    def test_a_settings_reload_rebuilds_the_backend(self) -> None:
        first = transports.backend()
        assert transports.backend() is first
        options = {"INCREMENTAL": False}
        with override_next_settings(DEFER={"TRANSPORT": {"OPTIONS": options}}):
            second = transports.backend()
            assert second is not first
            assert transport_of(_wsgi()).incremental is False
        assert transports.backend() is not second

    def test_a_manager_reset_and_a_fork_drop_the_backend(self) -> None:
        first = suspense_manager.transport()
        suspense_manager.reset()
        second = suspense_manager.transport()
        assert second is not first
        suspense_manager.forget()
        assert suspense_manager.transport() is not second

    def test_a_request_asks_the_backend_once(self) -> None:
        request = _wsgi()
        backend = transports.backend()
        with patch.object(backend, "facts", wraps=backend.facts) as asked:
            first = transport_of(request)
            assert transport_of(request) is first
        asked.assert_called_once_with(request)
