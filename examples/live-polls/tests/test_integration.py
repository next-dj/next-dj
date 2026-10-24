import json
from contextlib import ExitStack
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone
from polls.models import Choice, Poll

from next.forms.signals import action_dispatched, form_validation_failed
from next.partial import Patches
from next.realtime import MemoryBroker, publish
from next.realtime.signals import message_published
from next.testing import (
    NextClient,
    PartialEnvelope,
    SignalRecorder,
    assert_metadata,
    build_form_for,
    envelope_of,
    find_anchor,
    init_payload,
    override_next_settings,
    resolve_action_url,
)


pytestmark = pytest.mark.django_db


def _frame_data(frame: bytes) -> dict[str, object]:
    return json.loads(frame.split(b"data: ", 1)[1].split(b"\n\n", 1)[0])


def _detail_html(next_client: NextClient, poll: Poll) -> str:
    response = next_client.get(f"/polls/{poll.pk}/")
    assert response.status_code == 200
    return response.content.decode()


class TestRootRedirect:
    """The bare site root sends visitors to the polls list."""

    def test_root_redirects_to_polls(self, next_client: NextClient) -> None:
        response = next_client.get("/")
        assert response.status_code == 302
        assert response["Location"] == "/polls/"


class TestPollIndex:
    """The index page lists polls and renders the poll_card composite."""

    def test_renders_each_poll(
        self, next_client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        response = next_client.get("/polls/")
        body = response.content.decode()
        assert response.status_code == 200
        assert poll.question in body
        assert second_poll.question in body

    def test_poll_card_shows_choice_count_and_total(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        Choice.objects.filter(poll=poll, text="Tabs").update(votes=4)
        Choice.objects.filter(poll=poll, text="Spaces").update(votes=3)
        response = next_client.get("/polls/")
        body = response.content.decode()
        assert "2 choices · 7 total votes" in body

    def test_inherit_context_renders_active_polls_count(
        self, next_client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        response = next_client.get("/polls/")
        body = response.content.decode()
        assert "2 open polls" in body

    def test_empty_state(self, next_client: NextClient) -> None:
        response = next_client.get("/polls/")
        body = response.content.decode()
        assert response.status_code == 200
        assert "No polls yet" in body


class TestPollDetailPage:
    """The detail page renders the chart, the form, and the layout chain."""

    def test_renders_question_and_choices(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        body = _detail_html(next_client, poll)
        assert poll.question in body
        for choice in poll.choices.all():
            assert choice.text in body

    def test_nested_layout_chain_visible(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        body = _detail_html(next_client, poll)
        assert "🗳️ next.dj Live polls" in body
        assert "← All polls" in body
        assert f"Poll #{poll.pk}" in body

    def test_poll_chart_mount_point_present(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        body = _detail_html(next_client, poll)
        assert f'data-poll-chart="{poll.pk}"' in body
        assert "data-poll-chart-app" in body
        assert 'id="poll-chart-app"' in body
        assert "data-next-keep" in body

    def test_poll_chart_data_block_carries_fresh_counts(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        Choice.objects.filter(poll=poll, text="Tabs").update(votes=4)
        Choice.objects.filter(poll=poll, text="Spaces").update(votes=3)
        body = _detail_html(next_client, poll)
        tabs = poll.choices.get(text="Tabs")
        assert 'data-poll-chart-data data-total-votes="7"' in body
        assert f'data-choice-id="{tabs.pk}"' in body
        assert 'data-choice-text="Tabs"' in body
        assert 'data-choice-votes="4"' in body

    def test_inherit_context_carries_active_polls_count(
        self, next_client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        body = _detail_html(next_client, poll)
        assert "2 open polls" in body

    def test_initial_results_in_window_next_context(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        body = _detail_html(next_client, poll)
        payload = init_payload(body)
        assert payload["results"]["poll_id"] == poll.pk
        assert payload["results"]["total_votes"] == 0
        assert {c["text"] for c in payload["results"]["choices"]} == {"Tabs", "Spaces"}

    def test_page_declares_the_sse_connection(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        body = _detail_html(next_client, poll)
        assert f'data-next-sse="/polls/{poll.pk}/stream/"' in body


class TestPollHost:
    """Django admin provides a staff-only host control for poll state."""

    def test_staff_can_edit_deadline_and_anonymous_visitors_cannot(
        self, poll: Poll
    ) -> None:
        client = Client()
        change_url = f"/admin/polls/poll/{poll.pk}/change/"
        assert client.get(change_url).status_code == 302

        user_model = get_user_model()
        host = user_model.objects.create_superuser(
            username="poll-host", email="host@example.test", password="test-password"
        )
        client.force_login(host)
        response = client.get(change_url)

        assert response.status_code == 200
        assert b'name="is_open"' in response.content
        assert b'name="closes_at_0"' in response.content


class TestVoteAction:
    """Voting increments the chosen counter and emits action_dispatched."""

    def test_post_increments_choice_votes(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        choice = poll.choices.get(text="Tabs")
        response = next_client.post_action(
            "vote_form", {"poll": poll.pk, "choice": choice.pk}
        )
        assert response.status_code == 302
        choice.refresh_from_db()
        assert choice.votes == 1

    @pytest.mark.parametrize("closed_by", ["host", "deadline"])
    def test_vote_is_rejected_after_manual_close_or_deadline(
        self, next_client: NextClient, poll: Poll, closed_by: str
    ) -> None:
        poll.is_open = closed_by != "host"
        poll.closes_at = (
            timezone.now() - timedelta(seconds=1) if closed_by == "deadline" else None
        )
        poll.save(update_fields=("is_open", "closes_at"))
        choice = poll.choices.get(text="Tabs")

        response = next_client.post_action(
            "vote_form", {"poll": poll.pk, "choice": choice.pk}
        )

        assert response.status_code == 400
        choice.refresh_from_db()
        assert choice.votes == 0

    def test_future_deadline_is_in_server_rendered_chart_snapshot(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        poll.closes_at = timezone.now() + timedelta(minutes=1)
        poll.save(update_fields=("closes_at",))

        payload = init_payload(_detail_html(next_client, poll))

        assert payload["results"]["is_open"] is True
        assert payload["results"]["accepting_votes"] is True
        assert payload["results"]["deadline"] == int(poll.closes_at.timestamp() * 1000)

    @pytest.mark.parametrize("rounds", [1, 3, 7])
    def test_repeated_votes_sum_correctly(
        self, next_client: NextClient, poll: Poll, rounds: int
    ) -> None:
        choice = poll.choices.get(text="Spaces")
        for _ in range(rounds):
            response = next_client.post_action(
                "vote_form", {"poll": poll.pk, "choice": choice.pk}
            )
            assert response.status_code == 302
        choice.refresh_from_db()
        assert choice.votes == rounds

    def test_signal_payload_carries_form_and_url_kwargs(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        choice = poll.choices.get(text="Tabs")
        with SignalRecorder(action_dispatched) as recorder:
            response = next_client.post_action(
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
        self, next_client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        """A choice from a different poll fails validation and is not counted."""
        foreign_choice = second_poll.choices.first()
        with SignalRecorder(form_validation_failed) as recorder:
            response = next_client.post_action(
                "vote_form", {"poll": poll.pk, "choice": foreign_choice.pk}
            )
        assert response.status_code == 400
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
        self, next_client: NextClient, poll: Poll
    ) -> None:
        choice = poll.choices.get(text="Tabs")
        response = next_client.post_action(
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
        self, next_client: NextClient, poll: Poll
    ) -> None:
        choice = poll.choices.get(text="Tabs")
        response = next_client.post_action(
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
        self, next_client: NextClient, poll: Poll
    ) -> None:
        body = _detail_html(next_client, poll)
        payload = init_payload(body)
        assert "live_results" in payload
        assert payload["live_results"]["poll_id"] == poll.pk

    def test_invalid_partial_vote_morphs_the_results_zone(
        self, next_client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        foreign_choice = second_poll.choices.first()
        response = next_client.post_action(
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
        self, next_client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        Choice.objects.filter(poll=poll, text="Tabs").update(votes=4)
        foreign_choice = second_poll.choices.first()
        response = next_client.post_action(
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


class TestZoneAssetsCarryInsertionVerbs:
    """The asset manifest of a zone morph names how the client inserts each file."""

    def _vote_envelope(self, next_client: NextClient, poll: Poll) -> PartialEnvelope:
        choice = poll.choices.get(text="Tabs")
        return envelope_of(
            next_client.post_action(
                "vote_form",
                {"poll": poll.pk, "choice": choice.pk},
                origin=f"/polls/{poll.pk}/",
                partial=True,
                zones="poll-results",
            )
        )

    def test_every_asset_declares_a_load_verb(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        assets = self._vote_envelope(next_client, poll).assets
        assert assets
        assert all("load" in asset for asset in assets)

    def test_vue_assets_load_as_modules(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        assets = self._vote_envelope(next_client, poll).assets
        vue = [asset for asset in assets if asset["kind"] == "vue"]
        assert vue
        assert {asset["load"] for asset in vue} == {"module"}
        assert any("poll_chart/component.vue" in asset["url"] for asset in vue)

    def test_stylesheet_asset_loads_as_a_link(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        assets = self._vote_envelope(next_client, poll).assets
        css = [asset for asset in assets if asset["kind"] == "css"]
        assert [asset["load"] for asset in css] == ["link"]
        assert css[0]["url"].endswith("poll_chart.css")


class TestBroadcastReceiver:
    """A valid vote publishes a request-aware refresh to its poll topic."""

    def test_vote_publishes_refresh_after_commit(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        choice = poll.choices.get(text="Tabs")
        with (
            SignalRecorder(message_published) as recorder,
            TestCase.captureOnCommitCallbacks(execute=True),
        ):
            next_client.post_action("vote_form", {"poll": poll.pk, "choice": choice.pk})
        events = recorder.events_for(message_published)
        assert len(events) == 2
        assert events[0].kwargs["topic"] == f"poll:{poll.pk}"
        message = events[0].kwargs["message"]
        assert [patch.op for patch in message.envelope.ops] == ["refresh"]
        assert message.envelope.ops[0].extras["zone"] == "poll-results"
        event = events[1].kwargs["message"].envelope.ops[0]
        assert event.op == "event"
        assert event.extras["name"] == "poll:votes"
        assert event.extras["detail"]["total_votes"] == 1

    def test_vote_echoes_the_request_id(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        choice = poll.choices.get(text="Tabs")
        with (
            SignalRecorder(message_published) as recorder,
            TestCase.captureOnCommitCallbacks(execute=True),
        ):
            next_client.post_action(
                "vote_form",
                {"poll": poll.pk, "choice": choice.pk},
                origin=f"/polls/{poll.pk}/",
                partial=True,
                zones="poll-results",
                headers={"X-Next-Request-Id": "vote-1"},
            )
        events = recorder.events_for(message_published)
        assert len(events) == 2
        assert all(
            event.kwargs["message"].envelope.request_id == "vote-1" for event in events
        )

    def test_invalid_vote_does_not_publish_snapshot(
        self, next_client: NextClient, poll: Poll, second_poll: Poll
    ) -> None:
        foreign_choice = second_poll.choices.first()
        with (
            SignalRecorder(message_published) as recorder,
            TestCase.captureOnCommitCallbacks(execute=True),
        ):
            next_client.post_action(
                "vote_form", {"poll": poll.pk, "choice": foreign_choice.pk}
            )
        assert recorder.events_for(message_published) == []


class TestStreamEndpoint:
    """The SSE endpoint opens a polite patch event stream over the broker."""

    def test_response_uses_event_stream_content_type(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        response = next_client.get(f"/polls/{poll.pk}/stream/")
        try:
            assert response.status_code == 200
            assert response["Content-Type"].startswith("text/event-stream")
            assert response["Cache-Control"] == "no-cache, no-transform"
            assert response["X-Accel-Buffering"] == "no"
        finally:
            response.close()

    def test_http_endpoint_leads_with_retry_frame(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        """The first byte frame is the retry hint, proving the page wires the stream.

        Reading just the leading frame keeps the test from blocking on the broker.
        """
        response = next_client.get(f"/polls/{poll.pk}/stream/")
        try:
            first = next(iter(response.streaming_content))
            assert first.startswith(b"retry: ")
        finally:
            response.close()

    def test_memory_broker_fans_out_to_each_subscriber(self) -> None:
        backend = MemoryBroker()
        patches = Patches(None).refresh(zone="poll-results").envelope()
        with ExitStack() as stack:
            streams = [
                stack.enter_context(backend.subscribe(["poll:fanout"], timeout=0.1))
                for _ in range(2)
            ]
            backend.publish("poll:fanout", patches)
            messages = [next(stream) for stream in streams]
        assert [message.seq for message in messages if message is not None] == [1, 1]
        assert all(message.envelope == patches for message in messages if message)

    def test_unknown_poll_returns_404(self, next_client: NextClient) -> None:
        response = next_client.get("/polls/999/stream/")
        try:
            assert response.status_code == 404
        finally:
            response.close()


@pytest.mark.django_db(transaction=True)
class TestStreamPatchFrame:
    """A change over the open HTTP stream yields a refresh patch with the echo."""

    def test_change_frame_carries_refresh_and_echo(
        self, next_client: NextClient
    ) -> None:
        poll = Poll.objects.create(question="Streamed?")
        Choice.objects.create(poll=poll, text="Yes", votes=0)
        try:
            response = next_client.get(f"/polls/{poll.pk}/stream/")
            iterator = iter(response.streaming_content)
            retry = next(iterator)
            hello = next(iterator)
            assert retry.startswith(b"retry: ")
            assert b"event: next-hello" in hello
            position = _frame_data(hello)["topics"][f"poll:{poll.pk}"]
            assert set(position) == {"epoch", "seq"}
            assert isinstance(position["epoch"], str)
            assert isinstance(position["seq"], int)
            publish(
                f"poll:{poll.pk}",
                Patches(None, echo_of="r9").refresh(zone="poll-results"),
                on_commit=False,
            )
            frame = next(iterator)
            response.close()
            assert b"event: next-patches" in frame
            envelope = _frame_data(frame)
            assert [op["op"] for op in envelope["ops"]] == ["refresh"]
            assert envelope["ops"][0]["zone"] == "poll-results"
            assert envelope["request_id"] == "r9"
        finally:
            Choice.objects.filter(poll=poll).delete()
            poll.delete()


@pytest.mark.django_db(transaction=True)
class TestStreamRevocation:
    """Removing a poll ends its open streams at the next access recheck."""

    def test_deleted_poll_ends_the_stream_with_an_end_frame(
        self, next_client: NextClient
    ) -> None:
        poll = Poll.objects.create(question="Short-lived?")
        with override_next_settings(
            REALTIME={"TRANSPORTS": ["sse"], "HEARTBEAT": 0.05}
        ):
            response = next_client.get(f"/polls/{poll.pk}/stream/")
            frames = iter(response.streaming_content)
            assert next(frames).startswith(b"retry: ")
            assert b"event: next-hello" in next(frames)
            poll.delete()
            remaining = list(frames)
            response.close()
        assert b"event: next-end" in remaining[-1]
        assert _frame_data(remaining[-1]) == {"code": "auth"}


class TestProductionStartup:
    """App startup registers vote_form through autodiscover, not a test import."""

    def test_vote_form_discovered_through_app_ready(self) -> None:
        form = build_form_for("vote_form")
        assert type(form).__name__ == "VoteForm"
        assert type(form).__module__ == "polls.forms"


class TestPageMetadata:
    """The index names itself, a vote page is titled after its question."""

    @pytest.mark.parametrize(
        ("path", "title"),
        [
            ("/polls/", "Polls · next.dj polls"),
            ("/polls/{pk}/", "Tabs or spaces? · next.dj polls"),
        ],
        ids=["index", "detail"],
    )
    def test_each_page_carries_its_title(
        self, next_client: NextClient, poll: Poll, path: str, title: str
    ) -> None:
        assert_metadata(next_client.get(path.format(pk=poll.pk)), title=title)


class TestEmbedWidget:
    """The embed page cuts the layout chain and draws its own document shell."""

    def _embed_html(self, next_client: NextClient, poll: Poll) -> str:
        response = next_client.get(f"/polls/{poll.pk}/embed/")
        assert response.status_code == 200
        return response.content.decode()

    def test_own_shell_renders_once(self, next_client: NextClient, poll: Poll) -> None:
        body = self._embed_html(next_client, poll)
        assert body.count("<html") == 1
        assert body.count("<!DOCTYPE html>") == 1
        assert body.count("<body") == 1

    def test_studio_shell_and_parent_layouts_are_cut(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        body = self._embed_html(next_client, poll)
        assert "🗳️ next.dj Live polls" not in body
        assert "← All polls" not in body
        assert f"Poll #{poll.pk}" not in body
        assert "open polls" not in body

    def test_widget_shows_the_poll_and_its_live_wiring(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        body = self._embed_html(next_client, poll)
        assert poll.question in body
        assert f'data-poll-chart="{poll.pk}"' in body
        assert f'data-next-sse="/polls/{poll.pk}/stream/"' in body
        assert f'href="/polls/{poll.pk}/"' in body

    def test_ancestor_metadata_still_applies(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        response = next_client.get(f"/polls/{poll.pk}/embed/")
        assert_metadata(response, title=f"{poll.question} (embed) · next.dj polls")

    def test_root_layout_stylesheet_is_served(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        body = self._embed_html(next_client, poll)
        assert "/embed/layout.css" in body

    def test_detail_page_links_to_the_embed(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        body = _detail_html(next_client, poll)
        assert f'href="/polls/{poll.pk}/embed/"' in body

    def test_embed_zone_refreshes_through_the_partial_pipeline(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        choice = poll.choices.order_by("pk").first()
        assert choice is not None
        response = next_client.post_action(
            "vote_form",
            {"poll": poll.pk, "choice": choice.pk},
            origin=f"/polls/{poll.pk}/embed/",
            partial=True,
            zones="poll-results",
        )
        assert response.status_code == 200
        assert envelope_of(response).zone_targets() == ["poll-results"]


class TestEmbedCode:
    """`[int:id]/(.)embed/` serves the `embed-code` zone."""

    def test_the_embed_link_opens_the_snippet_over_the_honest_url(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        anchor = find_anchor(
            _detail_html(next_client, poll), href=f"/polls/{poll.pk}/embed/"
        )
        assert 'data-next-layer="embed-code"' in anchor

    def test_a_plain_get_renders_the_real_widget(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        body = next_client.get(f"/polls/{poll.pk}/embed/").content.decode()
        assert "data-embed-code" not in body
        assert f'data-poll-chart="{poll.pk}"' in body

    def test_the_zone_get_serves_the_snippet_with_its_title(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        response = next_client.get_zones(f"/polls/{poll.pk}/embed/", "embed-code")
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph", "meta"]
        html = envelope.html_for_zone("embed-code")
        assert "data-embed-code" in html
        assert f"&lt;iframe src=&quot;http://testserver/polls/{poll.pk}/embed/" in html
        meta = next(op for op in envelope.ops if op["op"] == "meta")
        assert meta["title"] == f"Embed · {poll.question} · next.dj polls"

    def test_the_snippet_links_to_the_real_widget(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        response = next_client.get_zones(f"/polls/{poll.pk}/embed/", "embed-code")
        anchor = find_anchor(
            envelope_of(response).html_for_zone("embed-code"), text="Preview the widget"
        )
        assert f'href="/polls/{poll.pk}/embed/"' in anchor
        assert "data-next-layer" not in anchor

    def test_an_unknown_poll_is_a_404(
        self, next_client: NextClient, poll: Poll
    ) -> None:
        response = next_client.get_zones("/polls/9999/embed/", "embed-code")
        assert response.status_code == 404
