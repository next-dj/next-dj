import asyncio
import inspect
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from next.realtime import MemoryBroker
from next.realtime.attendance import Identifier, aattend, attend
from tests.support.realtime import silent_schedule


def _by_user(user: object, topic: str) -> dict[str, object]:
    return {"user": user, "topic": topic}


def _by_subject(user: object, topic: str, subject: str) -> dict[str, object]:
    return {"user": user, "subject": subject}


def _optional_subject(
    user: object, topic: str, subject: str | None = None
) -> dict[str, object]:
    return {"subject": subject}


def _variadic(*args: object) -> dict[str, object]:
    return {"args": len(args)}


class _Unsigned:
    __signature__ = "not a signature"

    def __call__(self, user: object, topic: str) -> dict[str, object]:
        return {"topic": topic}


@dataclass
class _Unhashable:
    room: str

    def __call__(self, user: object, topic: str, subject: str) -> dict[str, object]:
        return {"room": self.room}


def _cached(user: object, topic: str) -> dict[str, object]:
    return {"topic": topic}


class TestIdentifierSignature:
    """`Identifier.of` reads once whether identify takes the subject."""

    @pytest.mark.parametrize(
        "identify",
        [_by_subject, _optional_subject, _variadic],
        ids=["three", "default", "variadic"],
    )
    def test_a_third_positional_takes_the_subject(self, identify: object) -> None:
        assert Identifier.of(identify).takes_subject

    @pytest.mark.parametrize(
        "identify", [_by_user, _Unsigned()], ids=["two", "no-signature"]
    )
    def test_two_positionals_take_no_subject(self, identify: object) -> None:
        assert not Identifier.of(identify).takes_subject

    def test_a_callable_signature_is_read_once(self) -> None:
        with patch.object(inspect, "signature", wraps=inspect.signature) as read:
            first = Identifier.of(_cached)
            second = Identifier.of(_cached)
        assert first == second
        assert read.call_count == 1

    def test_an_unhashable_callable_is_read_every_time(self) -> None:
        identify = _Unhashable("room:1")
        with patch.object(inspect, "signature", wraps=inspect.signature) as read:
            assert Identifier.of(identify).takes_subject
            assert Identifier.of(identify).takes_subject
        assert read.call_count == 2

    def test_an_identifier_passes_through(self) -> None:
        identifier = Identifier.of(_by_user)
        assert Identifier.of(identifier) is identifier

    def test_the_old_signature_gets_no_subject(self) -> None:
        assert Identifier.of(_by_user)("ada", "room:1", "s") == {
            "user": "ada",
            "topic": "room:1",
        }

    def test_the_new_signature_gets_the_subject(self) -> None:
        assert Identifier.of(_by_subject)(None, "room:1", "abcd") == {
            "user": None,
            "subject": "abcd",
        }


class TestSubjectReading:
    """A callable subject is read only for an identify that takes it."""

    def test_the_old_signature_never_reads_the_subject(self) -> None:
        reads: list[str] = []

        def subject() -> str:
            reads.append("read")
            return "s"

        broker = MemoryBroker(schedule=silent_schedule)
        attendance = attend(
            broker, ("room:1",), _by_user, "ada", heartbeat=5, subject=subject
        )
        assert attendance.members == {"room:1": {"user": "ada", "topic": "room:1"}}
        assert reads == []

    def test_the_new_signature_reads_it_once_for_every_topic(self) -> None:
        reads: list[str] = []

        def subject() -> str:
            reads.append("read")
            return "s1"

        broker = MemoryBroker(schedule=silent_schedule)
        attendance = attend(
            broker,
            ("room:1", "room:2"),
            _by_subject,
            None,
            heartbeat=5,
            subject=subject,
        )
        assert [m["subject"] for m in attendance.members.values()] == ["s1", "s1"]
        assert reads == ["read"]

    def test_the_async_path_passes_a_literal_subject(self) -> None:
        broker = MemoryBroker(schedule=silent_schedule)
        attendance = asyncio.run(
            aattend(broker, ("room:1",), _by_subject, None, heartbeat=5, subject="s2")
        )
        assert attendance.members == {"room:1": {"user": None, "subject": "s2"}}
