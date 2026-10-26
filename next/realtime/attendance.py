"""The attendance that keeps one connection counted on its topics."""

import asyncio
import inspect
import logging
import math
import secrets
import time
import weakref
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from typing import cast

from .access import run_detached
from .errors import BrokerConnectionError
from .roster import Member, PresenceBroker, presence_broker


logger = logging.getLogger(__name__)

_LEAVE_FAILED = "Could not leave the presence of %r, its TTL forgets the connection."
_LEAVE_TIMED_OUT = "The broker did not confirm a leave in time, the TTL forgets it."

type UserIdentify = Callable[[object, str], Member | None]
type SubjectIdentify = Callable[[object, str, str], Member | None]
type Identify = UserIdentify | SubjectIdentify
type Subject = str | Callable[[], str]


@dataclass(frozen=True, slots=True)
class Identifier:
    """An identify callable and whether its signature, read once, takes the subject.

    `identify(user, topic)` keeps working beside `identify(user, topic, subject)`.
    """

    function: Identify
    takes_subject: bool

    @classmethod
    def of(cls, identify: "Identify | Identifier") -> "Identifier":
        """Return the identifier of a callable, an identifier as it is."""
        if isinstance(identify, Identifier):
            return identify
        try:
            takes_subject = _ARITY[identify]
        except (KeyError, TypeError):
            takes_subject = _takes_subject(identify)
            with suppress(TypeError):
                _ARITY[identify] = takes_subject
        return cls(identify, takes_subject)

    def __call__(self, user: object, topic: str, subject: str) -> Member | None:
        """Return the member of the user on the topic, passing the subject if taken."""
        if self.takes_subject:
            return cast("SubjectIdentify", self.function)(user, topic, subject)
        return cast("UserIdentify", self.function)(user, topic)


_ARITY: weakref.WeakKeyDictionary[Identify, bool] = weakref.WeakKeyDictionary()
"""Each identify's signature, read once and dropped with the callable itself."""


def _takes_subject(identify: Identify) -> bool:
    """Whether the callable accepts a third positional argument for the subject."""
    try:
        inspect.signature(identify).bind(None, "", "")
    except (TypeError, ValueError):
        return False
    return True


def _identities(
    identify: "Identify | Identifier | None",
    user: object,
    topics: Sequence[str],
    subject: Subject,
) -> dict[str, Member | None]:
    """Return the member each topic shows for a user, None everywhere for a count.

    A callable subject is read only for an identify that takes it.
    """
    if identify is None:
        return dict.fromkeys(topics)
    identifier = Identifier.of(identify)
    known = ""
    if identifier.takes_subject:
        known = subject if isinstance(subject, str) else subject()
    return {topic: identifier(user, topic, known) for topic in topics}


class Attendance:
    """One connection's presence on its topics, joined, kept fresh and left together.

    A record lapses `ttl_heartbeats` heartbeats past its touch, a pruned one rejoins.
    """

    ttl_heartbeats = 3

    def __init__(
        self,
        broker: PresenceBroker,
        members: Mapping[str, Member | None],
        *,
        heartbeat: float,
        connection: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Bind the members to one connection id, a fresh one when it is None."""
        self.broker = broker
        self.connection = (
            secrets.token_urlsafe(16) if connection is None else connection
        )
        self.members = dict(members)
        self.ttl = self.ttl_heartbeats * heartbeat
        self._interval = heartbeat
        self._clock = clock
        self._due_at = math.inf

    def join(self) -> None:
        """Record the connection on every topic."""
        for topic, member in self.members.items():
            self.broker.join(topic, self.connection, member, self.ttl)
        self._due_at = self._clock() + self._interval

    def touch(self) -> None:
        """Push every deadline out, joining again where the record was pruned."""
        for topic, member in self.members.items():
            if not self.broker.touch(topic, self.connection, self.ttl):
                self.broker.join(topic, self.connection, member, self.ttl)
        self._due_at = self._clock() + self._interval

    def leave(self) -> None:
        """Forget the connection everywhere, leaving a broker failure to the TTL."""
        for topic in self.members:
            try:
                self.broker.leave(topic, self.connection)
            except BrokerConnectionError:
                logger.warning(_LEAVE_FAILED, topic, exc_info=True)

    def refreshed[T](self, items: Iterator[T]) -> Iterator[T]:
        """Pass items through, touching once a heartbeat has passed since the last."""
        for item in items:
            if self._clock() >= self._due_at:
                self.touch()
            yield item

    async def ajoin(self) -> None:
        """Record the connection on every topic through the asynchronous API."""
        for topic, member in self.members.items():
            await self.broker.ajoin(topic, self.connection, member, self.ttl)
        self._due_at = self._clock() + self._interval

    async def atouch(self) -> None:
        """Push every deadline out through the asynchronous API."""
        for topic, member in self.members.items():
            if not await self.broker.atouch(topic, self.connection, self.ttl):
                await self.broker.ajoin(topic, self.connection, member, self.ttl)
        self._due_at = self._clock() + self._interval

    async def aleave(self) -> None:
        """Forget the connection everywhere within one heartbeat, the TTL after that."""
        try:
            async with asyncio.timeout(self._interval):
                for topic in self.members:
                    try:
                        await self.broker.aleave(topic, self.connection)
                    except BrokerConnectionError:
                        logger.warning(_LEAVE_FAILED, topic, exc_info=True)
        except TimeoutError:
            logger.warning(_LEAVE_TIMED_OUT)

    async def arefreshed[T](self, items: AsyncIterator[T]) -> AsyncIterator[T]:
        """Pass items through, touching asynchronously once a heartbeat has passed."""
        async for item in items:
            if self._clock() >= self._due_at:
                await self.atouch()
            yield item


@contextmanager
def attending[T](
    attendance: Attendance | None, items: Iterator[T]
) -> Iterator[Iterator[T]]:
    """Join for the block and yield the items kept fresh, leaving on every way out."""
    if attendance is None:
        yield items
        return
    try:
        attendance.join()
        yield attendance.refreshed(items)
    finally:
        attendance.leave()


@asynccontextmanager
async def aattending[T](
    attendance: Attendance | None, items: AsyncIterator[T]
) -> AsyncIterator[AsyncIterator[T]]:
    """Join for the block through the asynchronous API, leaving on every way out."""
    if attendance is None:
        yield items
        return
    try:
        await attendance.ajoin()
        yield attendance.arefreshed(items)
    finally:
        await attendance.aleave()


def attend(
    broker: object,
    topics: Sequence[str],
    identify: "Identify | Identifier | None",
    user: object,
    *,
    heartbeat: float,
    connection: str | None = None,
    subject: Subject = "",
) -> Attendance:
    """Resolve each topic's member in this thread for a new connection's attendance."""
    capable = presence_broker(broker)
    members = _identities(identify, user, topics, subject)
    return Attendance(capable, members, heartbeat=heartbeat, connection=connection)


async def aattend(
    broker: object,
    topics: Sequence[str],
    identify: "Identify | Identifier | None",
    user: object,
    *,
    heartbeat: float,
    connection: str | None = None,
    subject: Subject = "",
) -> Attendance:
    """Resolve each topic's member on the shared pool, where `identify` may query."""
    capable = presence_broker(broker)
    if identify is None:
        members = _identities(None, user, topics, subject)
    else:
        members = await run_detached(_identities, identify, user, topics, subject)
    return Attendance(capable, members, heartbeat=heartbeat, connection=connection)


__all__ = [
    "Attendance",
    "Identifier",
    "Identify",
    "Subject",
    "aattend",
    "aattending",
    "attend",
    "attending",
]
