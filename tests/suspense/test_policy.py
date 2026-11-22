import gc
import logging
from unittest.mock import patch

import django
import pytest
from django.http import HttpRequest
from django.test import override_settings

from next.defer import Delivery
from next.pages.responses import mark_shared_render
from next.static import ScriptInjectionPolicy
from next.suspense.manager import suspense_manager
from next.suspense.markers import Server
from next.suspense.policy import (
    DefaultStreamPolicy,
    Delivered,
    InlineNotice,
    PageFacts,
    StreamPolicy,
    StreamSlots,
    declined,
    inline_notice,
    open_streams,
    page_facts,
    stream_limit,
    streaming,
    transported,
)
from next.suspense.signals import page_stream_declined
from next.suspense.transport import TransportFacts
from next.testing import capture_signals, override_next_settings


_GZIP = "django.middleware.gzip.GZipMiddleware"
_CSP = "django.middleware.csp.ContentSecurityPolicyMiddleware"
_THREADED = TransportFacts(True, True, "wsgi", "wsgi.multithread")
_SYNC = TransportFacts(True, False, "wsgi", "wsgi.multithread")
_BUFFERED = TransportFacts(False, True, "adapter.event", "wsgi.multithread")


def _facts(
    *,
    server: Server = Server.WSGI_THREADED,
    method: str = "GET",
    transport: TransportFacts = _THREADED,
    compression: str | None = None,
    buffering: bool = False,
    shared_cache: bool = False,
    partial: bool = False,
    accepts_stream: bool = False,
    agent: str = "",
    runtime: bool = True,
    speculative: bool = False,
    capacity: bool = True,
) -> PageFacts:
    return PageFacts(
        server=server,
        method=method,
        transport=transport,
        compression=compression,
        buffering=buffering,
        shared_cache=shared_cache,
        partial=partial,
        accepts_stream=accepts_stream,
        agent=agent,
        runtime=runtime,
        speculative=speculative,
        capacity=capacity,
    )


def _decide(facts: PageFacts, **options: object) -> Delivered:
    policy = DefaultStreamPolicy({"OPTIONS": options})
    return policy.delivery(HttpRequest(), facts)


class TestPageFacts:
    """The facts read the method, the compressor, the CSP and the cache of a request."""

    def test_a_plain_get(self, rf) -> None:
        facts = page_facts(rf.get("/"))
        assert facts == PageFacts(
            server=Server.WSGI_SYNC, method="GET", transport=_SYNC
        )

    @override_settings(MIDDLEWARE=[_GZIP, _CSP])
    def test_a_gzip_request_behind_a_csp_on_a_shared_page(self, rf) -> None:
        request = rf.get(
            "/",
            HTTP_ACCEPT_ENCODING="gzip, br",
            HTTP_ACCEPT="text/event-stream",
            HTTP_USER_AGENT="Agent",
            HTTP_X_NEXT_REQUEST="1",
        )
        mark_shared_render(request)
        with override_next_settings(COMPRESSION={"ENABLED": False}):
            facts = page_facts(request)
        assert facts.compression == "gzip"
        assert facts.csp is True
        assert facts.shared_cache is True
        assert facts.partial is True
        assert facts.accepts_stream is True
        assert facts.agent == "Agent"

    @override_settings(MIDDLEWARE=[_GZIP])
    def test_gzip_listed_but_not_accepted_negotiates_nothing(self, rf) -> None:
        facts = page_facts(rf.get("/"))
        assert facts.compression is None
        assert facts.buffering is False

    def test_a_browser_prefetch_is_speculative(self, rf) -> None:
        facts = page_facts(rf.get("/", HTTP_SEC_PURPOSE="prefetch"))
        assert facts.speculative is True

    def test_a_disabled_runtime_script_is_read(self, rf) -> None:
        with patch("next.suspense.policy.get_static_manager") as manager:
            builder = manager.return_value.script_builder.return_value
            builder.policy = ScriptInjectionPolicy.DISABLED
            facts = page_facts(rf.get("/"))
        assert facts.runtime is False

    def test_a_long_user_agent_is_cut(self, rf) -> None:
        facts = page_facts(rf.get("/", HTTP_USER_AGENT="x" * 600))
        assert len(facts.agent) == 512

    @override_settings(MIDDLEWARE=[_GZIP])
    def test_a_self_compressing_stream_leaves_gzip_unbuffered(self, rf) -> None:
        request = rf.get("/", HTTP_ACCEPT_ENCODING="gzip")
        facts = page_facts(request)
        assert (facts.compression, facts.buffering) == ("gzip", False)
        with override_next_settings(COMPRESSION={"ENABLED": False}):
            facts = page_facts(request)
        assert facts.compression == "gzip"
        assert facts.buffering is (django.VERSION[:2] < (6, 1))

    def test_the_streaming_block_reads_a_sync_worker_as_threaded(self, rf) -> None:
        request = rf.get("/")
        with streaming():
            facts = page_facts(request)
        assert facts.server is Server.WSGI_THREADED
        assert facts.transport.concurrent_reason == "streaming"
        assert page_facts(request).server is Server.WSGI_SYNC

    def test_the_streaming_block_leaves_a_concurrent_transport_as_is(
        self, wsgi, rf
    ) -> None:
        request = rf.get("/", **{"wsgi.multithread": True})
        with streaming():
            assert transported(request) == _THREADED

    def test_a_full_process_has_no_capacity(self, wsgi, rf) -> None:
        request = rf.get("/", **{"wsgi.multithread": True})
        with override_next_settings(DEFER={"MAX_STREAMS": 1}):
            slot = open_streams.acquire(1)
            assert slot is not None
            try:
                assert page_facts(request).capacity is False
            finally:
                slot.release()
            assert page_facts(request).capacity is True


class TestCspListed:
    """Whether a CSP middleware is listed is read once per `MIDDLEWARE` list."""

    def test_the_list_is_scanned_once_while_it_stays(self, rf) -> None:
        with override_settings(MIDDLEWARE=[_CSP]):
            assert page_facts(rf.get("/")).csp is True
            with patch("next.utils.middleware_listed") as listed:
                assert page_facts(rf.get("/")).csp is True
            listed.assert_not_called()

    def test_another_list_is_scanned_again(self, rf) -> None:
        with override_settings(MIDDLEWARE=[_CSP]):
            assert page_facts(rf.get("/")).csp is True
        with override_settings(MIDDLEWARE=[]):
            assert page_facts(rf.get("/")).csp is False


class TestBotMemo:
    """A user agent is matched against the pattern once, then answered from a memo."""

    def test_an_agent_seen_before_runs_no_pattern(self) -> None:
        agent = "Mozilla/5.0 (compatible; memo-probe-bot/1.0)"
        assert _decide(_facts(agent=agent)).reason == "bot"
        with patch("next.suspense.policy._bots") as compiled:
            assert _decide(_facts(agent=agent)).reason == "bot"
        compiled.assert_not_called()

    def test_the_pattern_of_the_options_keys_the_answer(self) -> None:
        agent = "Mozilla/5.0 (compatible; memo-probe-bot/1.0)"
        assert _decide(_facts(agent=agent), BOTS="never-matches").reason == "stream"
        assert _decide(_facts(agent=agent)).reason == "bot"

    def test_an_earlier_row_answers_before_the_agent_is_read(self) -> None:
        facts = _facts(method="POST", agent="Googlebot")
        policy = DefaultStreamPolicy()
        with patch.object(DefaultStreamPolicy, "_bot") as bot:
            assert policy.delivery(HttpRequest(), facts).reason == "method"
        bot.assert_not_called()


class TestDefaultStreamPolicy:
    """`HEAD` gets the shell, a request no row holds back the stream."""

    def test_head_gets_the_shell(self) -> None:
        assert _decide(_facts(method="HEAD")) == Delivered(Delivery.SHELL, "head")

    @pytest.mark.parametrize(
        ("facts", "reason"),
        [
            (_facts(speculative=True), "prefetch"),
            (_facts(speculative=True, method="POST"), "prefetch"),
            (_facts(method="POST"), "method"),
            (_facts(agent="Mozilla/5.0 (compatible; Googlebot/2.1)"), "bot"),
            (_facts(shared_cache=True), "cache"),
            (_facts(compression="gzip", buffering=True), "gzip"),
            (_facts(transport=_BUFFERED), "buffered"),
            (_facts(transport=_SYNC), "sync-worker"),
            (_facts(runtime=False), "runtime"),
            (_facts(runtime=False, partial=True), "runtime"),
            (_facts(capacity=False), "capacity"),
            (_facts(capacity=False, partial=True), "capacity"),
            (_facts(partial=True), "partial"),
        ],
    )
    def test_the_first_row_that_holds_names_the_reason(
        self, facts: PageFacts, reason: str
    ) -> None:
        assert _decide(facts) == Delivered(Delivery.INLINE, reason)

    @pytest.mark.parametrize(
        "facts",
        [
            _facts(),
            _facts(server=Server.ASGI),
            _facts(compression="gzip"),
            _facts(compression="gzip", buffering=False),
            _facts(partial=True, accepts_stream=True),
        ],
    )
    def test_a_request_no_row_holds_back_streams(self, facts: PageFacts) -> None:
        assert _decide(facts) == Delivered(Delivery.STREAM, "stream")

    def test_a_stream_worker_and_a_stream_option_fall_through(self) -> None:
        facts = _facts(server=Server.WSGI_SYNC, transport=_SYNC)
        assert _decide(facts, SYNC_WORKERS="stream").delivery is Delivery.STREAM
        assert _decide(_facts(), STREAM=False) == Delivered(Delivery.INLINE, "option")

    def test_a_buffered_transport_is_inline_whatever_the_workers(self) -> None:
        facts = _facts(transport=_BUFFERED)
        assert _decide(facts, SYNC_WORKERS="stream") == Delivered(
            Delivery.INLINE, "buffered"
        )

    def test_a_bots_pattern_replaces_the_default(self) -> None:
        crawler = _decide(_facts(agent="Crawler"), BOTS="nothing-here")
        assert crawler.delivery is Delivery.STREAM
        assert _decide(_facts(agent="my-agent"), BOTS="my-agent").reason == "bot"

    def test_an_absent_option_reads_the_settings(self) -> None:
        with override_next_settings(DEFER={"POLICY": {"OPTIONS": {"STREAM": False}}}):
            policy = DefaultStreamPolicy()
            assert policy.delivery(HttpRequest(), _facts()).reason == "option"

    def test_a_non_mapping_options_entry_is_empty(self) -> None:
        assert DefaultStreamPolicy({"OPTIONS": "x"}).options == {}


class _Always(StreamPolicy):
    """A project policy that streams every request."""

    def delivery(self, request: HttpRequest, facts: PageFacts) -> Delivered:
        return Delivered(Delivery.STREAM, "always")


class TestStreamsPartials:
    """A page tells the runtime its partials may stream unless no row ever allows it."""

    def test_a_threaded_server_may_stream(self, wsgi) -> None:
        assert DefaultStreamPolicy().streams_partials(wsgi(threaded=True)) is True

    def test_a_sync_worker_kept_inline_may_not(self, wsgi) -> None:
        policy = DefaultStreamPolicy()
        assert policy.streams_partials(wsgi(threaded=False)) is False
        with streaming():
            assert policy.streams_partials(wsgi(threaded=False)) is True

    def test_a_sync_worker_set_to_stream_may(self, wsgi) -> None:
        policy = DefaultStreamPolicy({"OPTIONS": {"SYNC_WORKERS": "stream"}})
        assert policy.streams_partials(wsgi(threaded=False)) is True

    def test_a_buffering_adapter_never_may(self, wsgi) -> None:
        policy = DefaultStreamPolicy({"OPTIONS": {"SYNC_WORKERS": "stream"}})
        request = wsgi(threaded=True, **{"adapter.event": {}})
        options = {"BUFFERED_MARKS": {"WSGI": {"KEYS": ["adapter.event"]}}}
        with override_next_settings(DEFER={"TRANSPORT": {"OPTIONS": options}}):
            assert policy.streams_partials(request) is False

    def test_streaming_turned_off_never_may(self, wsgi) -> None:
        policy = DefaultStreamPolicy({"OPTIONS": {"STREAM": False}})
        assert policy.streams_partials(wsgi(threaded=True)) is False

    def test_a_project_policy_may_by_default(self, wsgi) -> None:
        assert _Always().streams_partials(wsgi(threaded=False)) is True


class _Owner:
    """An object a slot can watch for collection."""


class TestStreamSlots:
    """The process counts its open streams against a limit, released once each."""

    def test_a_slot_is_taken_up_to_the_limit(self) -> None:
        slots = StreamSlots()
        first = slots.acquire(2)
        assert first is not None
        assert slots.acquire(2) is not None
        assert slots.acquire(2) is None
        assert slots.open == 2
        first.release()
        assert slots.open == 1

    def test_a_slot_is_given_back_once(self) -> None:
        slots = StreamSlots()
        witness, slot = slots.acquire(5), slots.acquire(5)
        assert witness is not None
        assert slot is not None
        slot.release()
        slot.release()
        assert slot.held is False
        assert slots.open == 1

    def test_a_collected_owner_gives_its_slot_back(self) -> None:
        slots = StreamSlots()
        slot = slots.acquire(1)
        assert slot is not None
        owner = _Owner()
        slot.bind(owner)
        del owner
        gc.collect()
        assert slot.held is False
        assert slots.open == 0

    def test_a_new_owner_replaces_the_old_one(self) -> None:
        slots = StreamSlots()
        slot = slots.acquire(1)
        assert slot is not None
        first, second = _Owner(), _Owner()
        slot.bind(first)
        slot.bind(second)
        del first
        gc.collect()
        assert slots.open == 1
        slot.release()
        del second
        gc.collect()
        assert slots.open == 0

    def test_binding_a_released_slot_holds_nothing(self) -> None:
        slots = StreamSlots()
        slot = slots.acquire(1)
        assert slot is not None
        slot.release()
        owner = _Owner()
        slot.bind(owner)
        del owner
        gc.collect()
        assert slots.open == 0

    def test_a_forked_child_holds_no_slot(self) -> None:
        slots = StreamSlots()
        held = slots.acquire(1)
        assert held is not None
        slots.forget()
        assert slots.open == 0
        fresh = slots.acquire(1)
        held.release()
        assert slots.open == 1
        assert fresh is not None


class TestStreamLimit:
    """`MAX_STREAMS` is a count, or `auto` derived from the kind of server."""

    def test_a_count_is_taken_as_is(self) -> None:
        with override_next_settings(DEFER={"MAX_STREAMS": 3}):
            assert stream_limit(Server.ASGI) == 3

    def test_auto_under_asgi_is_four_per_pool_worker(self) -> None:
        options = {"MAX_WORKERS": 5}
        with override_next_settings(DEFER={"EXECUTOR": {"OPTIONS": options}}):
            assert stream_limit(Server.ASGI) == 20

    @pytest.mark.parametrize(("threads", "limit"), [(1, 1), (8, 4)])
    def test_auto_under_wsgi_is_half_the_request_threads(
        self, threads: int, limit: int
    ) -> None:
        with override_next_settings(DEFER={"REQUEST_CONCURRENCY": threads}):
            assert stream_limit(Server.WSGI_THREADED) == limit


class TestManagerPolicy:
    """The manager builds the policy once and drops it with its backends."""

    def test_the_policy_is_built_once(self) -> None:
        try:
            first = suspense_manager.policy()
            assert isinstance(first, StreamPolicy)
            assert suspense_manager.policy() is first
            suspense_manager.reset()
            assert suspense_manager.policy() is not first
        finally:
            suspense_manager.reset()


class TestRouterPrefetch:
    """A router prefetch answers the shell before every row but `HEAD`."""

    def test_a_prefetch_is_decided_first(self) -> None:
        facts = PageFacts(
            server=Server.WSGI_SYNC,
            method="POST",
            transport=_SYNC,
            capacity=False,
            prefetch=True,
        )
        assert _decide(facts) == Delivered(Delivery.PREFETCH, "prefetch")

    def test_head_still_comes_first(self) -> None:
        facts = PageFacts(
            server=Server.WSGI_THREADED,
            method="HEAD",
            transport=_THREADED,
            prefetch=True,
        )
        assert _decide(facts) == Delivered(Delivery.SHELL, "head")

    @pytest.mark.parametrize(
        ("depth", "shell"), [("1", True), ("full", False), ("deep", False)]
    )
    def test_only_the_shell_depth_is_the_prefetch_fact(self, rf, depth, shell) -> None:
        request = rf.get(
            "/",
            headers={
                "X-Next-Request": "1",
                "X-Next-Navigate": "1",
                "X-Next-Prefetch": depth,
            },
        )
        with override_next_settings(ROUTER={"ENABLED": True}):
            assert page_facts(request).prefetch is shell

    @pytest.mark.parametrize("enabled", [True, False])
    def test_the_fact_needs_the_router(self, rf, enabled) -> None:
        request = rf.get(
            "/",
            headers={
                "X-Next-Request": "1",
                "X-Next-Navigate": "1",
                "X-Next-Prefetch": "1",
            },
        )
        with override_next_settings(ROUTER={"ENABLED": enabled}):
            assert page_facts(request).prefetch is enabled


class TestDeclined:
    """A response assembled inline is announced each time and logged once a process."""

    def test_the_signal_names_the_delivery_and_both_reasons(self, rf) -> None:
        request = rf.get("/")
        facts = _facts(transport=_BUFFERED, partial=True)
        with capture_signals(page_stream_declined) as recorder:
            declined(request, Delivered(Delivery.INLINE, "buffered"), facts)
        assert [(event.sender, event.kwargs) for event in recorder] == [
            (
                StreamPolicy,
                {
                    "delivery": Delivery.INLINE,
                    "reason": "buffered",
                    "transport_reason": "adapter.event",
                    "partial": True,
                    "request": request,
                },
            )
        ]

    def test_only_the_first_response_is_logged(
        self, wsgi, rf, caplog: pytest.LogCaptureFixture
    ) -> None:
        request = rf.get("/")
        inline_notice.forget()
        with caplog.at_level(logging.INFO, logger="next.suspense"):
            declined(request, Delivered(Delivery.INLINE, "sync-worker"), _facts())
            declined(request, Delivered(Delivery.INLINE, "bot"), _facts())
        messages = [record.getMessage() for record in caplog.records]
        assert len(messages) == 1
        assert "reason sync-worker, transport wsgi" in messages[0]

    def test_no_listener_sends_nothing(self, rf) -> None:
        with patch.object(page_stream_declined, "send_robust") as send:
            declined(rf.get("/"), Delivered(Delivery.INLINE, "bot"), _facts())
        send.assert_not_called()

    def test_a_reset_lets_the_next_response_through(self) -> None:
        notice = InlineNotice()
        assert notice.take() is True
        assert notice.take() is False
        notice.forget()
        assert notice.take() is True
