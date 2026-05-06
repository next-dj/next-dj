import json
import re

import pytest
from polls.broker import (
    PollBroker,
    Snapshot,
    broker,
    build_snapshot,
    format_event,
    format_keepalive,
    read_snapshot,
    store_snapshot,
)
from polls.models import Choice, Poll

from next.forms.signals import action_dispatched, form_validation_failed
from next.testing import NextClient, SignalRecorder, resolve_action_url


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
        assert payload["results"]["stream_url"] == f"/polls/{poll.pk}/stream/"


class TestVoteAction:
    """Voting increments the chosen counter and emits action_dispatched."""

    def test_post_increments_choice_votes(self, client: NextClient, poll: Poll) -> None:
        choice = poll.choices.get(text="Tabs")
        response = client.post_action(
            "polls:vote", {"poll": poll.pk, "choice": choice.pk}
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
                "polls:vote", {"poll": poll.pk, "choice": choice.pk}
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
                "polls:vote", {"poll": poll.pk, "choice": choice.pk}
            )
        assert response.status_code == 302
        events = recorder.events_for(action_dispatched)
        assert len(events) == 1
        kwargs = events[0].kwargs
        assert kwargs["action_name"] == "polls:vote"
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
                "polls:vote", {"poll": poll.pk, "choice": foreign_choice.pk}
            )
        assert response.status_code in (200, 400)
        foreign_choice.refresh_from_db()
        assert foreign_choice.votes == 0
        events = recorder.events_for(form_validation_failed)
        assert len(events) == 1
        assert events[0].kwargs["action_name"] == "polls:vote"

    def test_namespaced_action_url_resolves(self) -> None:
        url = resolve_action_url("polls:vote")
        assert url.startswith("/_next/form/")


class TestBroadcastReceiver:
    """The action_dispatched listener is the single publish point for the broker."""

    def test_snapshot_cached_after_vote(self, client: NextClient, poll: Poll) -> None:
        """A vote ends with a fresh snapshot in cache, written by the receiver alone.

        The vote handler no longer writes to the cache so the cached
        payload after a successful POST proves the receiver path
        executed and called `broker.publish`.
        """
        choice = poll.choices.get(text="Tabs")
        client.post_action("polls:vote", {"poll": poll.pk, "choice": choice.pk})
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

        def spy(snapshot: Snapshot) -> None:
            captured.append(snapshot)
            real_publish(snapshot)

        monkeypatch.setattr(broker, "publish", spy)
        choice = poll.choices.get(text="Tabs")
        client.post_action("polls:vote", {"poll": poll.pk, "choice": choice.pk})
        assert len(captured) == 1
        assert captured[0].poll_id == poll.pk
        votes_by_text = {row.text: row.votes for row in captured[0].choices}
        assert votes_by_text["Tabs"] == 1
        assert votes_by_text["Spaces"] == 0

    def test_invalid_vote_does_not_publish_snapshot(
        self, client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        foreign_choice = second_poll.choices.first()
        client.post_action("polls:vote", {"poll": poll.pk, "choice": foreign_choice.pk})
        assert read_snapshot(poll.pk) is None


class TestStreamEndpoint:
    """The SSE endpoint streams initial snapshot then live updates."""

    def test_response_uses_event_stream_content_type(
        self, client: NextClient, poll: Poll
    ) -> None:
        response = client.get(f"/polls/{poll.pk}/stream/")
        try:
            assert response.status_code == 200
            assert response["Content-Type"].startswith("text/event-stream")
            assert response["Cache-Control"] == "no-cache"
            assert response["X-Accel-Buffering"] == "no"
        finally:
            response.close()

    def test_http_endpoint_yields_cached_snapshot_as_first_frame(
        self, client: NextClient, poll: Poll
    ) -> None:
        """Reading the streaming response proves the page module wires the broker.

        Earlier broker tests instantiate `PollBroker()` directly, which
        does not exercise `polls.screens.polls.[int:id].stream.page`.
        Reading one frame off the actual streaming response is the
        only way to verify the HTTP path end to end. The frame is
        consumed in a `try/finally` so the response closes even if
        the assertion fails before the loop reaches the read.
        """
        broker.publish(build_snapshot(poll))
        response = client.get(f"/polls/{poll.pk}/stream/")
        try:
            first = next(iter(response.streaming_content))
            assert first.startswith(b"event: snapshot")
            payload = json.loads(first.split(b"data: ", 1)[1].split(b"\n\n", 1)[0])
            assert payload["poll_id"] == poll.pk
        finally:
            response.close()

    def test_first_frame_is_cached_snapshot(self, poll: Poll) -> None:
        store_snapshot(build_snapshot(poll))
        local = PollBroker()
        stream = local.subscribe(poll.pk)
        first = next(stream)
        stream.close()
        assert first.startswith(b"event: snapshot")
        payload = json.loads(first.split(b"data: ", 1)[1].split(b"\n\n", 1)[0])
        assert payload["poll_id"] == poll.pk

    def test_publish_wakes_subscriber_with_update_event(self, poll: Poll) -> None:
        store_snapshot(build_snapshot(poll))
        local = PollBroker()
        stream = local.subscribe(poll.pk)
        next(stream)  # consume initial snapshot
        Choice.objects.filter(poll=poll, text="Tabs").update(votes=2)
        local.publish(build_snapshot(poll))
        update = next(stream)
        stream.close()
        assert update.startswith(b"event: update")
        payload = json.loads(update.split(b"data: ", 1)[1].split(b"\n\n", 1)[0])
        votes_by_text = {row["text"]: row["votes"] for row in payload["choices"]}
        assert votes_by_text["Tabs"] == 2

    def test_publish_fans_out_to_multiple_subscribers(self, poll: Poll) -> None:
        """Every open subscriber wakes on a single publish without losing events."""
        store_snapshot(build_snapshot(poll))
        local = PollBroker()
        stream_a = local.subscribe(poll.pk)
        stream_b = local.subscribe(poll.pk)
        next(stream_a)  # initial snapshot
        next(stream_b)
        Choice.objects.filter(poll=poll, text="Tabs").update(votes=3)
        local.publish(build_snapshot(poll))
        update_a = next(stream_a)
        update_b = next(stream_b)
        stream_a.close()
        stream_b.close()
        assert update_a.startswith(b"event: update")
        assert update_b.startswith(b"event: update")
        for frame in (update_a, update_b):
            payload = json.loads(frame.split(b"data: ", 1)[1].split(b"\n\n", 1)[0])
            votes_by_text = {row["text"]: row["votes"] for row in payload["choices"]}
            assert votes_by_text["Tabs"] == 3

    def test_unknown_poll_returns_404(self, client: NextClient) -> None:
        response = client.get("/polls/999/stream/")
        try:
            assert response.status_code == 404
        finally:
            response.close()


class TestSnapshotFormat:
    """Snapshot helpers produce SSE-conformant frames."""

    def test_format_event_carries_event_name_and_payload(self) -> None:
        snapshot = Snapshot(poll_id=1, total_votes=5, choices=())
        frame = format_event(snapshot.to_payload(), event="update")
        assert frame.startswith(b"event: update\n")
        assert b'"poll_id":1' in frame
        assert frame.endswith(b"\n\n")

    def test_format_keepalive_uses_comment_form(self) -> None:
        assert format_keepalive() == b": keepalive\n\n"

    def test_module_level_broker_publishes_snapshot_to_cache(self, poll: Poll) -> None:
        snapshot = build_snapshot(poll)
        broker.publish(snapshot)
        cached = read_snapshot(poll.pk)
        assert cached is not None
        assert cached["poll_id"] == poll.pk
