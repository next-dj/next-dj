from unittest.mock import patch

import pytest
from django.http import HttpRequest

from next.defer.manager import defer_manager
from next.defer.markers import Delivery, ServerKind
from next.defer.policy import DefaultDeliveryPolicy, DeliveryDecision, delivery_facts
from next.defer.probe import (
    DefaultServerProbe,
    ServerFacts,
    ServerProbe,
    server_facts,
    server_probes,
)
from next.errors import BackendImportError
from next.testing import override_next_settings


_THREADED = ServerFacts(True, True, "wsgi", "wsgi.multithread")
_SYNC = ServerFacts(True, False, "wsgi", "wsgi.multithread")
_ASGI = ServerFacts(True, True, "asgi", "asgi")


def _facts(request: HttpRequest, **options: object) -> ServerFacts:
    return DefaultServerProbe({"OPTIONS": options}).facts(request)


class _Buffering(ServerProbe):
    """A project probe for a host that buffers every response."""

    def facts(self, request: HttpRequest) -> ServerFacts:
        return ServerFacts(False, True, "host", "host")


class TestServerClaims:
    """With no option and no mark the server's own claims decide."""

    @pytest.mark.parametrize(
        ("kind", "scope", "expected"),
        [
            ("wsgi", {"threaded": True}, _THREADED),
            ("wsgi", {"threaded": False}, _SYNC),
            ("wsgi", {"threaded": None}, _SYNC),
            ("asgi", {}, _ASGI),
        ],
        ids=["threaded", "single_thread", "unclaimed", "asgi"],
    )
    def test_the_claims_answer(
        self, request: pytest.FixtureRequest, kind: str, scope: dict, expected
    ) -> None:
        assert _facts(request.getfixturevalue(kind)(**scope)) == expected

    def test_an_answer_is_built_once(self, wsgi) -> None:
        probe = DefaultServerProbe()
        assert probe.facts(wsgi()) is probe.facts(wsgi())


_MARKS = {
    "WSGI": {"KEYS": ["adapter.event"], "PREFIXES": ["adapter.", "bridge."]},
    "ASGI": {"KEYS": ["adapter.event", "adapter.context"]},
}


class TestBufferedMarks:
    """A mark the project lists makes the server buffered where a request has it."""

    @pytest.mark.parametrize(
        ("kind", "key", "reason"),
        [
            ("wsgi", "adapter.event", "adapter.event"),
            ("wsgi", "adapter.full_event", "adapter."),
            ("wsgi", "bridge.context", "bridge."),
            ("asgi", "adapter.event", "adapter.event"),
            ("asgi", "adapter.context", "adapter.context"),
        ],
        ids=["wsgi_key", "wsgi_prefix", "wsgi_second_prefix", "asgi_key", "asgi_ctx"],
    )
    def test_a_mark_buffers(
        self, request: pytest.FixtureRequest, kind: str, key: str, reason: str
    ) -> None:
        built = request.getfixturevalue(kind)(**{key: {}})
        facts = _facts(built, BUFFERED_MARKS=_MARKS)
        assert (facts.incremental, facts.incremental_reason) == (False, reason)
        assert facts.reason == reason

    def test_a_mark_leaves_the_concurrency_to_the_claims(self, wsgi) -> None:
        request = wsgi(threaded=False, **{"adapter.event": {}})
        facts = _facts(request, BUFFERED_MARKS=_MARKS)
        assert (facts.concurrent, facts.concurrent_reason) == (
            False,
            "wsgi.multithread",
        )

    def test_a_key_sharing_only_the_first_character_is_no_mark(self, wsgi) -> None:
        assert _facts(wsgi(apple=1, bridge=1), BUFFERED_MARKS=_MARKS) == _THREADED

    def test_a_kind_reads_only_its_own_marks(self, asgi) -> None:
        assert _facts(asgi(**{"bridge.id": 1}), BUFFERED_MARKS=_MARKS) == _ASGI

    @pytest.mark.parametrize("table", [None, "x", {}], ids=["none", "text", "empty"])
    def test_no_table_marks_nothing(self, wsgi, asgi, table: object) -> None:
        assert _facts(wsgi(**{"adapter.event": {}}), BUFFERED_MARKS=table) == _THREADED
        assert _facts(asgi(**{"adapter.event": {}}), BUFFERED_MARKS=table) == _ASGI

    def test_the_default_settings_mark_nothing(self, wsgi) -> None:
        request = wsgi(**{"adapter.event": {}})
        assert DefaultServerProbe().facts(request) == _THREADED

    def test_a_table_missing_a_kind_or_a_list_marks_nothing_there(
        self, wsgi, asgi
    ) -> None:
        table = {"WSGI": "adapter.event", "ASGI": {"KEYS": "adapter.event"}}
        assert _facts(wsgi(**{"adapter.event": {}}), BUFFERED_MARKS=table) == (
            _THREADED
        )
        assert _facts(asgi(**{"adapter.event": {}}), BUFFERED_MARKS=table) == _ASGI

    def test_a_blank_or_foreign_entry_of_a_list_is_skipped(self, wsgi) -> None:
        table = {"WSGI": {"KEYS": ["", 3, "adapter.event"]}}
        facts = _facts(wsgi(**{"adapter.event": {}}), BUFFERED_MARKS=table)
        assert facts.incremental_reason == "adapter.event"

    def test_the_settings_table_is_read(self, asgi) -> None:
        options = {"BUFFERED_MARKS": {"ASGI": {"PREFIXES": ["adapter."]}}}
        with override_next_settings(DEFER={"SERVER": {"OPTIONS": options}}):
            assert server_facts(asgi(**{"adapter.id": 1})).reason == "adapter."


class TestOptions:
    """An option states the truth, over every mark and every claim."""

    def test_incremental_true_overrides_a_mark(self, wsgi) -> None:
        request = wsgi(**{"adapter.event": {}})
        facts = _facts(request, INCREMENTAL=True, BUFFERED_MARKS=_MARKS)
        assert (facts.incremental, facts.incremental_reason) == (True, "option")

    def test_incremental_false_buffers_a_streaming_server(self, wsgi, asgi) -> None:
        for request in (wsgi(), asgi()):
            facts = _facts(request, INCREMENTAL=False)
            assert (facts.incremental, facts.reason) == (False, "option")

    def test_concurrent_true_overrides_a_single_thread_claim(self, wsgi) -> None:
        facts = _facts(wsgi(threaded=False), CONCURRENT=True)
        assert facts == ServerFacts(True, True, "wsgi", "option")

    def test_concurrent_false_holds_back_an_asgi_server(self, asgi) -> None:
        facts = _facts(asgi(), CONCURRENT=False)
        assert facts == ServerFacts(True, False, "asgi", "option")
        assert facts.reason == "option"

    def test_a_value_other_than_a_flag_detects(self, wsgi) -> None:
        assert _facts(wsgi(threaded=False), CONCURRENT="yes") == _SYNC

    def test_an_absent_option_reads_the_settings(self, wsgi) -> None:
        options = {"CONCURRENT": True}
        with override_next_settings(DEFER={"SERVER": {"OPTIONS": options}}):
            assert DefaultServerProbe().facts(wsgi(threaded=False)).concurrent

    def test_a_non_mapping_options_entry_is_empty(self) -> None:
        assert DefaultServerProbe({"OPTIONS": "x"}).options == {}


class TestDelivery:
    """The policy streams what the server lets through and inlines a buffered one."""

    def test_a_single_thread_claim_made_concurrent_streams(self, wsgi) -> None:
        options = {"CONCURRENT": True}
        with override_next_settings(DEFER={"SERVER": {"OPTIONS": options}}):
            request = wsgi(threaded=False)
            facts = delivery_facts(request)
            delivered = defer_manager.policy().delivery(request, facts)
        assert facts.server is ServerKind.WSGI_THREADED
        assert delivered == DeliveryDecision(Delivery.STREAM, "stream")

    def test_a_buffering_adapter_is_inline_with_sync_workers_streaming(
        self, wsgi
    ) -> None:
        request = wsgi(**{"adapter.full_event": {}})
        policy = DefaultDeliveryPolicy({"OPTIONS": {"SYNC_WORKERS": "stream"}})
        options = {"BUFFERED_MARKS": {"WSGI": {"PREFIXES": ["adapter."]}}}
        with override_next_settings(DEFER={"SERVER": {"OPTIONS": options}}):
            delivered = policy.delivery(request, delivery_facts(request))
        assert delivered == DeliveryDecision(Delivery.INLINE, "buffered")

    def test_a_proxy_stated_as_buffering_is_inline(self, wsgi) -> None:
        options = {"INCREMENTAL": False}
        with override_next_settings(DEFER={"SERVER": {"OPTIONS": options}}):
            request = wsgi()
            delivered = defer_manager.policy().delivery(
                request, delivery_facts(request)
            )
        assert delivered == DeliveryDecision(Delivery.INLINE, "buffered")


class TestBackend:
    """The settings name the backend, built once and dropped on a settings reload."""

    def test_a_project_backend_answers(self, wsgi) -> None:
        path = f"{__name__}._Buffering"
        with override_next_settings(DEFER={"SERVER": {"BACKEND": path}}):
            assert isinstance(defer_manager.probe(), _Buffering)
            assert server_facts(wsgi()).incremental_reason == "host"

    def test_a_path_that_does_not_import_is_refused(self) -> None:
        backend = {"BACKEND": "nowhere.Probe"}
        with (
            override_next_settings(DEFER={"SERVER": backend}),
            pytest.raises(BackendImportError),
        ):
            server_probes.get()

    def test_a_settings_reload_rebuilds_the_backend(self, wsgi) -> None:
        first = server_probes.get()
        assert server_probes.get() is first
        options = {"INCREMENTAL": False}
        with override_next_settings(DEFER={"SERVER": {"OPTIONS": options}}):
            second = server_probes.get()
            assert second is not first
            assert server_facts(wsgi()).incremental is False
        assert server_probes.get() is not second

    def test_a_manager_reset_and_a_fork_drop_the_backend(self) -> None:
        first = defer_manager.probe()
        defer_manager.reset()
        second = defer_manager.probe()
        assert second is not first
        defer_manager.forget()
        assert defer_manager.probe() is not second

    def test_a_request_asks_the_backend_once(self, wsgi) -> None:
        request = wsgi()
        backend = server_probes.get()
        with patch.object(backend, "facts", wraps=backend.facts) as asked:
            first = server_facts(request)
            assert server_facts(request) is first
        asked.assert_called_once_with(request)
