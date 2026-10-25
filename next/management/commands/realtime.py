"""`manage.py realtime`, which publishes to topics and reads what the broker holds."""

import json
from datetime import UTC, datetime
from typing import Any, override

from django.core.management.base import BaseCommand, CommandError, CommandParser

from next.management.table import write_table
from next.partial.envelope import Patch
from next.partial.keys import EVENT_NAME, ZONE
from next.realtime.console import (
    process_local,
    publish_patches,
    read_positions,
    read_presence,
    read_replay,
)
from next.realtime.errors import ConsoleError
from next.realtime.messages import Message, Position


_PROCESS_LOCAL = (
    "MemoryBroker keeps its topics inside this process, so the command reads no "
    "server's state."
)


class Command(BaseCommand):
    """Publish a refresh or an event, or read positions, presence and the replay log."""

    help = "Publish to realtime topics and read their positions, presence and log."
    requires_system_checks = ()

    @override
    def add_arguments(self, parser: CommandParser) -> None:
        """Take one action, its topics and options, and `--json` on each."""
        actions = parser.add_subparsers(dest="action", required=True, metavar="action")
        publish = actions.add_parser(
            "publish", help="Publish zone refreshes or one event on a topic."
        )
        publish.add_argument("topic")
        payload = publish.add_mutually_exclusive_group(required=True)
        payload.add_argument(
            "--refresh",
            nargs="+",
            action="extend",
            default=[],
            metavar="zone",
            help="The zones every subscriber refetches.",
        )
        payload.add_argument("--event", metavar="name", help="The event to dispatch.")
        publish.add_argument(
            "--data", metavar="json", help="The event detail, a JSON object."
        )
        positions = actions.add_parser(
            "positions", help="Print the latest position of each topic."
        )
        positions.add_argument("topics", nargs="+", metavar="topic")
        presence = actions.add_parser(
            "presence", help="Print the attendee count and members of a topic."
        )
        presence.add_argument("topic")
        replay = actions.add_parser(
            "replay", help="Print the logged messages after a cursor."
        )
        replay.add_argument("topic")
        replay.add_argument(
            "--after",
            required=True,
            metavar="epoch:seq",
            help="The position the log is read after, as `positions` prints it.",
        )
        for action in (publish, positions, presence, replay):
            action.add_argument(
                "--json", action="store_true", help="Print JSON instead of text."
            )

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        """Run the action and print its answer, a refusal ending it with its reason."""
        try:
            self._handle(options)
        except ConsoleError as exc:
            raise CommandError(str(exc)) from exc

    def _handle(self, options: dict[str, Any]) -> None:
        action = options["action"]
        if action != "publish" and process_local():
            self.stderr.write(_PROCESS_LOCAL)
        if action == "publish":
            message = publish_patches(
                options["topic"],
                zones=options["refresh"],
                event=options["event"],
                data=options["data"],
            )
            self._publish(message, as_json=options["json"])
        elif action == "positions":
            self._positions(read_positions(options["topics"]), as_json=options["json"])
        elif action == "presence":
            self._presence(options["topic"], as_json=options["json"])
        else:
            self._replay(options["topic"], options["after"], as_json=options["json"])

    def _publish(self, message: Message, *, as_json: bool) -> None:
        if as_json:
            self._json(message.as_dict())
            return
        self.stdout.write(
            f"Published {_patches(message)} to {message.topic} at {_cursor(message)}."
        )

    def _positions(self, positions: dict[str, Position], *, as_json: bool) -> None:
        if as_json:
            self._json(
                {
                    topic: {"epoch": p.epoch, "seq": p.seq} if p.epoch else None
                    for topic, p in positions.items()
                }
            )
            return
        rows = [
            (topic, f"{p.epoch}:{p.seq}" if p.epoch else "-")
            for topic, p in positions.items()
        ]
        write_table(self, ("Topic", "Position"), rows)

    def _presence(self, topic: str, *, as_json: bool) -> None:
        presence = read_presence(topic)
        members = [dict(member) for member in presence.members]
        if as_json:
            self._json({"topic": topic, "count": presence.count, "members": members})
            return
        self.stdout.write(f"{presence.count} present on {topic}")
        for member in members:
            self.stdout.write(f"  {json.dumps(member, sort_keys=True)}")

    def _replay(self, topic: str, after: str, *, as_json: bool) -> None:
        replay = read_replay(topic, after)
        if as_json:
            self._json(
                {
                    "topic": topic,
                    "after": after,
                    "complete": replay.complete,
                    "messages": [message.as_dict() for message in replay.messages],
                }
            )
        elif not replay.complete:
            self.stdout.write(
                f"The log of {topic} cannot close the gap after {after}, so a "
                "subscriber there resyncs."
            )
        elif not replay.messages:
            self.stdout.write(f"Nothing on {topic} after {after}.")
        else:
            rows = [
                (_cursor(message), _published_at(message), _patches(message))
                for message in replay.messages
            ]
            write_table(self, ("Position", "Published", "Patches"), rows)

    def _json(self, value: object) -> None:
        self.stdout.write(json.dumps(value, sort_keys=True))


def _cursor(message: Message) -> str:
    return f"{message.epoch}:{message.seq}"


def _published_at(message: Message) -> str:
    moment = datetime.fromtimestamp(message.published_at, tz=UTC)
    return moment.isoformat(timespec="seconds")


def _patches(message: Message) -> str:
    return ", ".join(_patch(patch) for patch in message.envelope.ops)


def _patch(patch: Patch) -> str:
    """Name a patch by its operation and the zone or event it addresses."""
    subject = patch.extras.get(ZONE) or patch.extras.get(EVENT_NAME)
    return patch.op if subject is None else f"{patch.op} {subject}"
