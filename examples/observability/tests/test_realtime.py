import asyncio
import logging

import pytest
from django.core.asgi import get_asgi_application
from django.test import override_settings
from obs.exporter import (
    MESSAGES_PUBLISHED,
    OTHER_FAMILY,
    PRESENCE_SNAPSHOTS,
    STREAM_DURATION,
    STREAM_UNSUBSCRIBES,
    STREAMS_OPEN,
    UPSTREAM_EVENTS,
    UPSTREAM_REJECTED,
    seed_series,
    topic_family,
)
from obs.pages.realtime.page import pulse_topic, realtime
from obs.realtime import PULSE_TOPIC, REALTIME_TOTALS_ZONE, can_watch
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY

from next.partial import Patches
from next.realtime import application, publish
from next.realtime.frames import UpstreamError
from next.realtime.signals import UnsubscribeReason, stream_subscribed
from next.testing import (
    PartialEnvelope,
    SseReader,
    WebSocketCommunicator,
    aissue_socket_ticket,
    capture_published,
    envelope_of,
    memory_broker,
    override_websocket_mount,
)


pytestmark = pytest.mark.django_db

STREAM_URL = "/realtime/stream/"
METRICS_URL = "/metrics/"
SCRAPE_TOKEN = "scrape-secret"
WS_HEADERS = [(b"origin", b"http://testserver"), (b"host", b"testserver")]


def sample(series: str, **labels: str) -> float | None:
    return REGISTRY.get_sample_value(series, labels)


def family_labels() -> set[str]:
    return {
        child.labels["family"]
        for metric in MESSAGES_PUBLISHED.collect()
        for child in metric.samples
    }


@pytest.fixture(autouse=True)
def fresh_series() -> None:
    for metric in (
        STREAMS_OPEN,
        STREAM_UNSUBSCRIBES,
        STREAM_DURATION,
        MESSAGES_PUBLISHED,
        PRESENCE_SNAPSHOTS,
        UPSTREAM_EVENTS,
        UPSTREAM_REJECTED,
    ):
        metric.clear()
    seed_series()


def boom(**kwargs) -> None:
    msg = "a broken metrics receiver"
    raise RuntimeError(msg)


class TestSeededSeries:
    def test_every_bounded_series_exports_zero_before_any_stream(self) -> None:
        assert sample("next_realtime_streams_open", transport="ws") == 0
        assert (
            sample("next_realtime_stream_duration_seconds_count", transport="sse") == 0
        )
        for reason in UnsubscribeReason:
            assert (
                sample(
                    "next_realtime_stream_unsubscribes_total",
                    transport="sse",
                    reason=reason.value,
                )
                == 0
            )
        assert family_labels() == {"obs", OTHER_FAMILY}


class TestSseStream:
    def test_an_open_stream_moves_the_gauge_and_its_close_is_counted(
        self, next_client
    ) -> None:
        with memory_broker():
            response = next_client.get(STREAM_URL)
            with SseReader(response) as events:
                assert next(events).event == "next-hello"
                assert sample("next_realtime_streams_open", transport="sse") == 1
        assert sample("next_realtime_streams_open", transport="sse") == 0
        assert (
            sample(
                "next_realtime_stream_unsubscribes_total",
                transport="sse",
                reason="client",
            )
            == 1
        )
        assert (
            sample("next_realtime_stream_duration_seconds_count", transport="sse") == 1
        )

    def test_a_raising_receiver_beside_ours_leaves_the_stream_open(
        self, next_client, caplog
    ) -> None:
        stream_subscribed.connect(boom, dispatch_uid="obs-test-boom")
        try:
            with (
                caplog.at_level(logging.ERROR, logger="next.realtime.signals"),
                memory_broker(),
            ):
                response = next_client.get(STREAM_URL)
                with SseReader(response) as events:
                    assert next(events).event == "next-hello"
                    publish(
                        PULSE_TOPIC,
                        Patches(None).refresh(zone=REALTIME_TOTALS_ZONE),
                        on_commit=False,
                    )
                    assert next(events).event == "next-patches"
                    assert sample("next_realtime_streams_open", transport="sse") == 1
        finally:
            stream_subscribed.disconnect(dispatch_uid="obs-test-boom")
        assert "a broken metrics receiver" in caplog.text
        assert sample("next_realtime_streams_open", transport="sse") == 0


@pytest.mark.django_db(transaction=True)
class TestWebSocket:
    def run(self, scenario) -> None:
        with override_websocket_mount(), memory_broker():
            asyncio.run(scenario())

    async def connected(self, *, presence: bool = False) -> WebSocketCommunicator:
        ticket = await aissue_socket_ticket(
            (PULSE_TOPIC,), authorizer_ref="obs.realtime.can_watch", presence=presence
        )
        app = application(get_asgi_application())
        communicator = WebSocketCommunicator(
            app, "/_next/ws/", [*WS_HEADERS, ticket.cookie], timeout=5
        )
        connected, _ = await communicator.connect()
        assert connected
        assert (await communicator.subscribe(ticket))["type"] == "hello"
        return communicator

    def test_a_nudge_is_counted_and_republished_on_the_pulse_family(self) -> None:
        async def scenario() -> None:
            communicator = await self.connected()
            assert sample("next_realtime_streams_open", transport="ws") == 1
            assert await communicator.send_up(PULSE_TOPIC, "nudge") is None
            await communicator.disconnect()

        with capture_published(PULSE_TOPIC) as published:
            self.run(scenario)
        assert len(published) == 1
        assert sample("next_realtime_upstream_events_total", name="nudge") == 1
        assert sample("next_realtime_messages_published_total", family="obs") == 1
        assert sample("next_realtime_streams_open", transport="ws") == 0
        assert (
            sample(
                "next_realtime_stream_unsubscribes_total",
                transport="ws",
                reason="client",
            )
            == 1
        )

    def test_a_presence_snapshot_is_counted_apart_from_publications(self) -> None:
        async def scenario() -> None:
            communicator = await self.connected(presence=True)
            await communicator.disconnect()

        self.run(scenario)
        assert sample("next_realtime_presence_snapshots_total", family="obs") >= 1
        assert sample("next_realtime_messages_published_total", family="obs") == 0

    def test_an_event_without_a_handler_adds_no_series(self) -> None:
        async def scenario() -> None:
            communicator = await self.connected()
            error = await communicator.send_up(PULSE_TOPIC, "shout")
            assert isinstance(error, UpstreamError)
            await communicator.disconnect()

        self.run(scenario)
        assert sample("next_realtime_upstream_events_total", name="shout") is None
        assert sample("next_realtime_upstream_rejected_total", reason="unknown") == 1

    def test_a_topic_the_ticket_does_not_grant_is_counted_without_its_name(
        self,
    ) -> None:
        async def scenario() -> None:
            communicator = await self.connected()
            error = await communicator.send_up("obs:elsewhere", "nudge")
            assert error is UpstreamError.TOPIC
            await communicator.disconnect()

        self.run(scenario)
        assert sample("next_realtime_upstream_rejected_total", reason="topic") == 1
        assert sample("next_realtime_upstream_events_total", name="nudge") is None
        reasons = {
            child.labels["reason"]
            for metric in UPSTREAM_REJECTED.collect()
            for child in metric.samples
        }
        assert reasons == {error.value for error in UpstreamError}


class TestTopicCardinality:
    @pytest.mark.parametrize(
        ("topic", "family"),
        [
            ("obs:pulse", "obs"),
            ("obs:pulse:42", "obs"),
            ("poll:17", OTHER_FAMILY),
            ("obs", "obs"),
            ("observer:1", OTHER_FAMILY),
        ],
    )
    def test_family_of(self, topic: str, family: str) -> None:
        assert topic_family(topic) == family

    def test_a_topic_per_object_stays_two_series(self) -> None:
        with memory_broker():
            for index in range(30):
                publish(f"obs:item-{index}", Patches(None).event("e"), on_commit=False)
                publish(f"poll:{index}", Patches(None).event("e"), on_commit=False)
                publish(f"user{index}", Patches(None).event("e"), on_commit=False)
        assert family_labels() == {"obs", OTHER_FAMILY}
        assert sample("next_realtime_messages_published_total", family="obs") == 30
        assert sample("next_realtime_messages_published_total", family="other") == 60


class TestMetricsEndpoint:
    def test_hidden_without_a_token_outside_debug(self, next_client) -> None:
        assert next_client.get(METRICS_URL).status_code == 404

    @override_settings(DEBUG=True)
    def test_debug_serves_the_exposition(self, next_client) -> None:
        response = next_client.get(METRICS_URL)
        body = response.content.decode()
        assert response.status_code == 200
        assert response["Content-Type"] == CONTENT_TYPE_LATEST
        assert 'next_realtime_streams_open{transport="sse"} 0.0' in body
        assert (
            'next_realtime_stream_unsubscribes_total{reason="revoked",transport="ws"} 0.0'
            in body
        )
        assert (
            'next_realtime_stream_duration_seconds_bucket{le="60.0",transport="ws"}'
            in (body)
        )
        assert 'next_realtime_messages_published_total{family="other"} 0.0' in body

    @override_settings(OBS_METRICS_TOKEN=SCRAPE_TOKEN)
    @pytest.mark.parametrize("header", [None, "Bearer wrong", SCRAPE_TOKEN])
    def test_a_token_refuses_every_other_credential(self, next_client, header) -> None:
        headers = {} if header is None else {"Authorization": header}
        assert next_client.get(METRICS_URL, headers=headers).status_code == 403

    @override_settings(OBS_METRICS_TOKEN=SCRAPE_TOKEN)
    @pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
    def test_the_bearer_token_admits_a_scrape(self, next_client, scheme) -> None:
        response = next_client.get(
            METRICS_URL, headers={"Authorization": f"{scheme} {SCRAPE_TOKEN}"}
        )
        assert response.status_code == 200
        assert b"next_realtime_streams_open" in response.content


class TestRealtimePage:
    def test_the_page_binds_its_stream_and_lists_every_reason(
        self, next_client
    ) -> None:
        response = next_client.get("/realtime/")
        html = response.content.decode()
        assert response.status_code == 200
        assert f'data-next-stream="{STREAM_URL}"' in html
        assert f'data-obs-nudge="{PULSE_TOPIC}"' in html
        for reason in UnsubscribeReason:
            assert f"<span>{reason.value}</span>" in html

    def test_the_pulse_form_publishes_a_refresh_and_redirects(
        self, next_client
    ) -> None:
        with memory_broker(), capture_published(PULSE_TOPIC) as published:
            response = next_client.post_action("pulse_form", origin="/realtime/")
        assert response.status_code == 302
        assert response["Location"] == "/realtime/"
        (message,) = published.messages
        envelope = PartialEnvelope(message.envelope.as_dict())
        assert envelope.ops == [{"op": "refresh", "zone": REALTIME_TOTALS_ZONE}]
        assert sample("next_realtime_messages_published_total", family="obs") == 1

    def test_the_totals_zone_rerenders_alone(self, next_client) -> None:
        STREAMS_OPEN.labels(transport="ws").inc(3)
        response = next_client.get_zones("/realtime/", REALTIME_TOTALS_ZONE)
        envelope = envelope_of(response)
        assert envelope.zone_targets() == [REALTIME_TOTALS_ZONE]
        assert "Open WebSocket" in envelope.html_for_zone(REALTIME_TOTALS_ZONE)


class TestRealtimeContext:
    def test_an_idle_process_reads_zeroes(self) -> None:
        totals = realtime()
        assert totals["open_sse"] == totals["open_ws"] == totals["published"] == 0
        assert totals["mean_seconds"] == 0

    def test_the_mean_divides_every_closed_stream(self) -> None:
        STREAM_DURATION.labels(transport="sse").observe(3)
        STREAM_DURATION.labels(transport="ws").observe(6)
        STREAM_UNSUBSCRIBES.labels(transport="ws", reason="error").inc(2)
        totals = realtime()
        assert totals["mean_seconds"] == 4.5
        assert totals["reasons"][0] == ("error", 2)

    def test_the_nudge_topic_is_the_pulse(self) -> None:
        assert pulse_topic() == PULSE_TOPIC


class TestAuthorize:
    def test_the_pulse_topic_is_granted(self) -> None:
        assert can_watch(None, PULSE_TOPIC) is True

    def test_another_obs_topic_is_refused(self) -> None:
        assert can_watch(None, "obs:other") is False
