import json
import re
import threading
import time

import pytest
from polls.broker import (
    Change,
    PollBroker,
    Snapshot,
    broker,
    build_snapshot,
    read_snapshot,
    store_snapshot,
)
from polls.models import Choice, Poll

from next.forms.signals import action_dispatched, form_validation_failed
from next.partial import REQUEST_ID
from next.testing import (
    NextClient,
    SignalRecorder,
    build_form_for,
    envelope_of,
    resolve_action_url,
)


pytestmark = pytest.mark.django_db


@pytest.fixture()
def poll(db) -> Poll:
    """Return the first demo poll with two choices for the happy-path flow.

    The fixture dedupes against rows seeded by the data migration so the
    total poll count stays at two and assertions on the inherited
    `active_polls_count` stay deterministic.
    """
    del db
    poll, _ = Poll.objects.get_or_create(question="Tabs or spaces?")
    Choice.objects.get_or_create(poll=poll, text="Tabs", defaults={"votes": 0})
    Choice.objects.get_or_create(poll=poll, text="Spaces", defaults={"votes": 0})
    return poll


@pytest.fixture()
def second_poll(db) -> Poll:
    """Return the second seeded poll, used to exercise cross-poll validation."""
    del db
    poll, _ = Poll.objects.get_or_create(question="Vim or Emacs?")
    Choice.objects.get_or_create(poll=poll, text="Vim", defaults={"votes": 0})
    Choice.objects.get_or_create(poll=poll, text="Emacs", defaults={"votes": 0})
    return poll


def _detail_html(client: NextClient, poll: Poll) -> str:
    response = client.get(f"/polls/{poll.pk}/")
    assert response.status_code == 200
    return response.content.decode()


def _next_init_payload(html: str) -> dict:
    """Pull the JS context payload out of the rendered `Next._init(...)` call."""
    match = re.search(r"Next\._init\((\{.*?\})\)", html)
    assert match is not None, "Next._init call missing"
    return json.loads(match.group(1))


def _consume_one_change(
    target: PollBroker,
    poll_id: int,
    snapshot: Snapshot,
    *,
    request_id: str | None = None,
) -> Change | None:
    """Drain one change a worker captures after the main thread publishes.

    A consumer thread starts on the lazy `changes` generator and captures
    its baseline revision before the main thread publishes, so the wake
    is race-free without depending on the generator's start timing.
    """
    captured: list[Change] = []
    stream = target.changes(poll_id)
    worker = threading.Thread(target=lambda: captured.append(next(stream)))
    worker.start()
    time.sleep(0.05)
    target.publish(snapshot, request_id=request_id)
    worker.join(timeout=2)
    stream.close()
    return captured[0] if captured else None


@pytest.fixture()
def primed_broker(poll: Poll) -> PollBroker:
    """Stand-alone broker with `poll`'s snapshot cached for change tests.

    Tests use a local instance so revision counters do not leak across
    cases. The cache is process-wide, so seeding through `store_snapshot`
    is enough for a `changes` consumer to read the snapshot on its first
    wake.
    """
    store_snapshot(build_snapshot(poll))
    return PollBroker()


class TestRootRedirect:
    """The bare site root sends visitors to the polls list."""

    def test_root_redirects_to_polls(self, client: NextClient) -> None:
        response = client.get("/")
        assert response.status_code == 302
        assert response["Location"] == "/polls/"


class TestPollIndex:
    """The index page lists polls and renders the poll_card composite."""

    def test_renders_each_poll(
        self, client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        response = client.get("/polls/")
        body = response.content.decode()
        assert response.status_code == 200
        assert poll.question in body
        assert second_poll.question in body

    def test_poll_card_shows_choice_count_and_total(
        self, client: NextClient, poll: Poll
    ) -> None:
        Choice.objects.filter(poll=poll, text="Tabs").update(votes=4)
        Choice.objects.filter(poll=poll, text="Spaces").update(votes=3)
        response = client.get("/polls/")
        body = response.content.decode()
        assert "2 choices · 7 total votes" in body

    def test_inherit_context_renders_active_polls_count(
        self, client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        del poll, second_poll
        response = client.get("/polls/")
        body = response.content.decode()
        assert "2 open polls" in body

    def test_empty_state(self, client: NextClient) -> None:
        Poll.objects.all().delete()
        response = client.get("/polls/")
        body = response.content.decode()
        assert response.status_code == 200
        assert "No polls yet" in body


class TestPollDetailPage:
    """The detail page renders the chart, the form, and the layout chain."""

    def test_renders_question_and_choices(self, client: NextClient, poll: Poll) -> None:
        body = _detail_html(client, poll)
        assert poll.question in body
        for choice in poll.choices.all():
            assert choice.text in body

    def test_nested_layout_chain_visible(self, client: NextClient, poll: Poll) -> None:
        body = _detail_html(client, poll)
        assert "🗳️ next.dj Live polls" in body
        assert "← All polls" in body
        assert f"Poll #{poll.pk}" in body

    def test_poll_chart_mount_point_present(
        self, client: NextClient, poll: Poll
    ) -> None:
        body = _detail_html(client, poll)
        assert f'data-poll-chart="{poll.pk}"' in body
        assert "data-poll-chart-app" in body
        assert 'id="poll-chart-app"' in body
        assert "data-next-keep" in body

    def test_poll_chart_data_block_carries_fresh_counts(
        self, client: NextClient, poll: Poll
    ) -> None:
        Choice.objects.filter(poll=poll, text="Tabs").update(votes=4)
        Choice.objects.filter(poll=poll, text="Spaces").update(votes=3)
        body = _detail_html(client, poll)
        tabs = poll.choices.get(text="Tabs")
        assert 'data-poll-chart-data data-total-votes="7"' in body
        assert f'data-choice-id="{tabs.pk}"' in body
        assert 'data-choice-text="Tabs"' in body
        assert 'data-choice-votes="4"' in body

    def test_inherit_context_carries_active_polls_count(
        self, client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        del second_poll
        body = _detail_html(client, poll)
        assert "2 open polls" in body

    def test_initial_results_in_window_next_context(
        self, client: NextClient, poll: Poll
    ) -> None:
        body = _detail_html(client, poll)
        payload = _next_init_payload(body)
        assert payload["results"]["poll_id"] == poll.pk
        assert payload["results"]["total_votes"] == 0
        assert {c["text"] for c in payload["results"]["choices"]} == {"Tabs", "Spaces"}

    def test_page_declares_the_sse_connection(
        self, client: NextClient, poll: Poll
    ) -> None:
        body = _detail_html(client, poll)
        assert f'data-next-sse="/polls/{poll.pk}/stream/"' in body


class TestVoteAction:
    """Voting increments the chosen counter and emits action_dispatched."""

    def test_post_increments_choice_votes(self, client: NextClient, poll: Poll) -> None:
        choice = poll.choices.get(text="Tabs")
        response = client.post_action(
            "vote_form", {"poll": poll.pk, "choice": choice.pk}
        )
        assert response.status_code == 302
        choice.refresh_from_db()
        assert choice.votes == 1

    @pytest.mark.parametrize(
        ("rounds", "expected"),
        [(1, 1), (3, 3), (7, 7)],
        ids=["once", "three", "seven"],
    )
    def test_repeated_votes_sum_correctly(
        self, client: NextClient, poll: Poll, rounds: int, expected: int
    ) -> None:
        choice = poll.choices.get(text="Spaces")
        for _ in range(rounds):
            response = client.post_action(
                "vote_form", {"poll": poll.pk, "choice": choice.pk}
            )
            assert response.status_code == 302
        choice.refresh_from_db()
        assert choice.votes == expected

    def test_signal_payload_carries_form_and_url_kwargs(
        self, client: NextClient, poll: Poll
    ) -> None:
        choice = poll.choices.get(text="Tabs")
        with SignalRecorder(action_dispatched) as recorder:
            response = client.post_action(
                "vote_form", {"poll": poll.pk, "choice": choice.pk}
            )
        assert response.status_code == 302
        events = recorder.events_for(action_dispatched)
        assert len(events) == 1
        kwargs = events[0].kwargs
        assert kwargs["action_name"] == "vote_form"
        assert kwargs["response_status"] == 302
        assert kwargs["form"] is not None
        assert kwargs["form"].cleaned_data["poll"].pk == poll.pk
        assert kwargs["form"].cleaned_data["choice"].pk == choice.pk

    def test_cross_poll_choice_rejected(
        self, client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        """A choice from a different poll fails validation and is not counted."""
        foreign_choice = second_poll.choices.first()
        with SignalRecorder(form_validation_failed) as recorder:
            response = client.post_action(
                "vote_form", {"poll": poll.pk, "choice": foreign_choice.pk}
            )
        assert response.status_code in (200, 400)
        foreign_choice.refresh_from_db()
        assert foreign_choice.votes == 0
        events = recorder.events_for(form_validation_failed)
        assert len(events) == 1
        assert events[0].kwargs["action_name"] == "vote_form"

    def test_namespaced_action_url_resolves(self) -> None:
        url = resolve_action_url("vote_form")
        assert url.startswith("/_next/form/")


class TestPartialVote:
    """A partial vote answers the voter, the zone re-renders on a bad choice."""

    def test_valid_partial_vote_morphs_the_zone_and_pushes_context(
        self, client: NextClient, poll: Poll
    ) -> None:
        choice = poll.choices.get(text="Tabs")
        response = client.post_action(
            "vote_form",
            {"poll": poll.pk, "choice": choice.pk},
            origin=f"/polls/{poll.pk}/",
            partial=True,
            zones="poll-results",
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph", "context"]
        assert envelope.zone_targets() == ["poll-results"]
        choice.refresh_from_db()
        assert choice.votes == 1

    def test_valid_partial_vote_context_op_carries_fresh_counts(
        self, client: NextClient, poll: Poll
    ) -> None:
        choice = poll.choices.get(text="Tabs")
        response = client.post_action(
            "vote_form",
            {"poll": poll.pk, "choice": choice.pk},
            origin=f"/polls/{poll.pk}/",
            partial=True,
            zones="poll-results",
        )
        envelope = envelope_of(response)
        context_op = next(op for op in envelope.ops if op["op"] == "context")
        snapshot = context_op["data"]["live_results"]
        assert snapshot["poll_id"] == poll.pk
        assert snapshot["total_votes"] == 1
        votes_by_text = {row["text"]: row["votes"] for row in snapshot["choices"]}
        assert votes_by_text["Tabs"] == 1
        assert votes_by_text["Spaces"] == 0

    def test_pushed_context_name_is_a_serialize_provider_of_the_origin(
        self, client: NextClient, poll: Poll
    ) -> None:
        body = _detail_html(client, poll)
        payload = _next_init_payload(body)
        assert "live_results" in payload
        assert payload["live_results"]["poll_id"] == poll.pk

    def test_invalid_partial_vote_morphs_the_results_zone(
        self, client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        foreign_choice = second_poll.choices.first()
        response = client.post_action(
            "vote_form",
            {"poll": poll.pk, "choice": foreign_choice.pk},
            origin=f"/polls/{poll.pk}/",
            partial=True,
            zones="poll-results",
        )
        assert response.status_code == 200
        assert response["X-Next-Form"] == "invalid"
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.zone_targets() == ["poll-results"]
        meta = envelope.form_meta()
        assert meta is not None
        assert meta["valid"] is False
        assert "choice" in meta["errors"]
        html = envelope.html_for_zone("poll-results")
        assert 'data-poll-chart-data data-total-votes="0"' in html
        assert "Cast your vote" in html
        foreign_choice.refresh_from_db()
        assert foreign_choice.votes == 0

    def test_winning_choice_count_rides_the_zone_morph(
        self, client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        Choice.objects.filter(poll=poll, text="Tabs").update(votes=4)
        foreign_choice = second_poll.choices.first()
        response = client.post_action(
            "vote_form",
            {"poll": poll.pk, "choice": foreign_choice.pk},
            origin=f"/polls/{poll.pk}/",
            partial=True,
            zones="poll-results",
        )
        envelope = envelope_of(response)
        html = envelope.html_for_zone("poll-results")
        tabs = poll.choices.get(text="Tabs")
        assert 'data-poll-chart-data data-total-votes="4"' in html
        assert f'data-choice-id="{tabs.pk}"' in html
        assert 'data-choice-votes="4"' in html


class TestBroadcastReceiver:
    """The action_dispatched listener is the single publish point for the broker."""

    def test_snapshot_cached_after_vote(self, client: NextClient, poll: Poll) -> None:
        """A vote ends with a fresh snapshot in cache, written by the receiver alone.

        The vote handler no longer writes to the cache so the cached
        payload after a successful POST proves the receiver path
        executed and called `broker.publish`.
        """
        choice = poll.choices.get(text="Tabs")
        client.post_action("vote_form", {"poll": poll.pk, "choice": choice.pk})
        snapshot = read_snapshot(poll.pk)
        assert snapshot is not None
        votes_by_text = {row["text"]: row["votes"] for row in snapshot["choices"]}
        assert votes_by_text["Tabs"] == 1
        assert votes_by_text["Spaces"] == 0
        assert snapshot["total_votes"] == 1

    def test_receiver_invokes_broker_publish_with_new_snapshot(
        self,
        client: NextClient,
        poll: Poll,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A spy on `broker.publish` confirms the receiver is the publish source.

        The handler runs `UPDATE` only, so any wake of the SSE stream
        has to come from the `action_dispatched` receiver. Replacing
        `broker.publish` with a spy that delegates to the real method
        keeps the cache state intact while making the call site
        observable.
        """
        captured: list[Snapshot] = []
        real_publish = broker.publish

        def spy(snapshot: Snapshot, request_id: str | None = None) -> None:
            captured.append(snapshot)
            real_publish(snapshot, request_id=request_id)

        monkeypatch.setattr(broker, "publish", spy)
        choice = poll.choices.get(text="Tabs")
        client.post_action("vote_form", {"poll": poll.pk, "choice": choice.pk})
        assert len(captured) == 1
        assert captured[0].poll_id == poll.pk
        votes_by_text = {row.text: row.votes for row in captured[0].choices}
        assert votes_by_text["Tabs"] == 1
        assert votes_by_text["Spaces"] == 0

    def test_invalid_vote_does_not_publish_snapshot(
        self, client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        foreign_choice = second_poll.choices.first()
        client.post_action("vote_form", {"poll": poll.pk, "choice": foreign_choice.pk})
        assert read_snapshot(poll.pk) is None


class TestStreamEndpoint:
    """The SSE endpoint opens a polite patch event stream over the broker."""

    def test_response_uses_event_stream_content_type(
        self, client: NextClient, poll: Poll
    ) -> None:
        response = client.get(f"/polls/{poll.pk}/stream/")
        try:
            assert response.status_code == 200
            assert response["Content-Type"].startswith("text/event-stream")
            assert response["Cache-Control"] == "no-cache, no-transform"
            assert response["X-Accel-Buffering"] == "no"
        finally:
            response.close()

    def test_http_endpoint_leads_with_retry_frame(
        self, client: NextClient, poll: Poll
    ) -> None:
        """The first byte frame is the retry hint, proving the page wires the stream.

        Reading the leading frame off the actual streaming response is
        the only way to verify the HTTP path end to end without blocking
        on the broker condition. The frame is consumed in a `try/finally`
        so the response closes even if the assertion fails before the
        read.
        """
        response = client.get(f"/polls/{poll.pk}/stream/")
        try:
            first = next(iter(response.streaming_content))
            assert first.startswith(b"retry: ")
        finally:
            response.close()

    def test_change_yields_refresh_with_request_id(
        self, primed_broker: PollBroker, poll: Poll
    ) -> None:
        Choice.objects.filter(poll=poll, text="Tabs").update(votes=2)
        change = _consume_one_change(
            primed_broker, poll.pk, build_snapshot(poll), request_id="r1"
        )
        assert change is not None
        assert change.request_id == "r1"
        votes_by_text = {
            row["text"]: row["votes"] for row in change.snapshot["choices"]
        }
        assert votes_by_text["Tabs"] == 2

    def test_change_fans_out_to_multiple_subscribers(
        self, primed_broker: PollBroker, poll: Poll
    ) -> None:
        """Every open subscriber wakes on a single publish without losing events."""
        Choice.objects.filter(poll=poll, text="Tabs").update(votes=3)
        snapshot = build_snapshot(poll)
        streams = [primed_broker.changes(poll.pk) for _ in range(2)]
        captured: list[list[Change]] = [[], []]
        workers = [
            threading.Thread(target=lambda s=s, c=c: c.append(next(s)))
            for s, c in zip(streams, captured, strict=True)
        ]
        for worker in workers:
            worker.start()
        time.sleep(0.05)
        primed_broker.publish(snapshot)
        for worker in workers:
            worker.join(timeout=2)
        for stream in streams:
            stream.close()
        for bucket in captured:
            assert bucket
            votes_by_text = {
                row["text"]: row["votes"] for row in bucket[0].snapshot["choices"]
            }
            assert votes_by_text["Tabs"] == 3

    def test_unknown_poll_returns_404(self, client: NextClient) -> None:
        response = client.get("/polls/999/stream/")
        try:
            assert response.status_code == 404
        finally:
            response.close()


class TestEchoThreading:
    """The vote's request id rides the change so the initiator drops its echo."""

    def test_request_id_header_reaches_the_change(
        self, client: NextClient, poll: Poll
    ) -> None:
        choice = poll.choices.get(text="Tabs")
        captured: list[Change] = []
        stream = broker.changes(poll.pk)

        def drain() -> None:
            captured.append(next(stream))

        worker = threading.Thread(target=drain)
        worker.start()
        time.sleep(0.05)
        client.post_action(
            "vote_form",
            {"poll": poll.pk, "choice": choice.pk},
            **{f"HTTP_{REQUEST_ID.upper().replace('-', '_')}": "vote-1"},
        )
        worker.join(timeout=2)
        stream.close()
        assert captured
        assert captured[0].request_id == "vote-1"


@pytest.mark.django_db(transaction=True)
class TestStreamPatchFrame:
    """A change over the open HTTP stream yields a refresh patch with the echo.

    The poll is committed so the streaming response, consumed on a worker
    thread, builds the envelope from its own database connection. The
    refresh fan-out carries the change's request id so the initiator's
    own tab drops the echo.
    """

    def test_change_frame_carries_refresh_and_echo(self, client: NextClient) -> None:
        poll = Poll.objects.create(question="Streamed?")
        Choice.objects.create(poll=poll, text="Yes", votes=0)
        try:
            response = client.get(f"/polls/{poll.pk}/stream/")
            frames: list[bytes] = []
            iterator = iter(response.streaming_content)

            def drain() -> None:
                frames.append(next(iterator))
                frames.append(next(iterator))

            worker = threading.Thread(target=drain)
            worker.start()
            time.sleep(0.05)
            broker.publish(build_snapshot(poll), request_id="r9")
            worker.join(timeout=2)
            response.close()
            assert frames[0].startswith(b"retry: ")
            data = frames[1].split(b"data: ", 1)[1].split(b"\n\n", 1)[0]
            envelope = json.loads(data)
            assert [op["op"] for op in envelope["ops"]] == ["refresh"]
            assert envelope["ops"][0]["zone"] == "poll-results"
            assert envelope["request_id"] == "r9"
        finally:
            Choice.objects.filter(poll=poll).delete()
            poll.delete()


class TestSnapshotCache:
    """The module-level broker writes snapshots to the shared cache."""

    def test_module_level_broker_publishes_snapshot_to_cache(self, poll: Poll) -> None:
        snapshot = build_snapshot(poll)
        broker.publish(snapshot)
        cached = read_snapshot(poll.pk)
        assert cached is not None
        assert cached["poll_id"] == poll.pk

    def test_snapshot_payload_shape(self) -> None:
        snapshot = Snapshot(poll_id=1, total_votes=5, choices=())
        payload = snapshot.to_payload()
        assert payload["poll_id"] == 1
        assert payload["total_votes"] == 5
        assert payload["choices"] == []


class TestProductionStartup:
    """App startup registers vote_form through autodiscover, not a test import."""

    def test_vote_form_discovered_through_app_ready(self) -> None:
        form = build_form_for("vote_form")
        assert type(form).__name__ == "VoteForm"
        assert type(form).__module__ == "polls.forms"
