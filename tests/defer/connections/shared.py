from collections.abc import Callable
from contextlib import AbstractContextManager

from django.contrib.sessions.middleware import SessionMiddleware
from django.db import DatabaseError, connection

from next.defer.connections import ConnectionState, ThreadState
from next.testing import override_next_settings


class Tenant(ConnectionState):
    covers = frozenset({"routers"})

    def capture(self, request, alias):
        return getattr(request, "tenant", None)

    def apply(self, state, connection):
        with connection.cursor() as cursor:
            cursor.execute("SELECT %s", [f"tenant {state}"])


class Role(ConnectionState):
    covers = frozenset({f"routers:{__name__}.StatefulRouter"})

    def capture(self, request, alias):
        return None

    def apply(self, state, connection):
        msg = "a state that was never captured is never applied"
        raise AssertionError(msg)


class Refusing(ConnectionState):
    def capture(self, request, alias):
        return "refused"

    def apply(self, state, connection):
        msg = "apply failed"
        raise DatabaseError(msg)


class Crum(ThreadState):
    covers = frozenset({"django-crum"})

    def capture(self, request):
        return getattr(request, "crum", None)

    def apply(self, state):
        return state

    def reset(self, token):
        return None


class Recorder(ThreadState):
    def __init__(self, name: str, events: list[str]) -> None:
        """Record under `name` into the shared `events`."""
        self.name = name
        self.events = events

    def capture(self, request):
        return self.name

    def apply(self, state):
        self.events.append(f"apply {state}")
        return f"token {state}"

    def reset(self, token):
        self.events.append(f"reset {token}")


class Broken(Recorder):
    def apply(self, state):
        msg = "thread state failed"
        raise RuntimeError(msg)


class Unresettable(Recorder):
    def reset(self, token):
        msg = "thread state stuck"
        raise RuntimeError(msg)


class StatelessRouter:
    next_stateless = True


class StatefulRouter:
    pass


class ProjectSessions(SessionMiddleware):
    pass


def not_a_class(get_response):
    return get_response


def dotted(target: type | Callable[..., object]) -> str:
    return f"{target.__module__}.{target.__qualname__}"


def defer(**scope: object) -> AbstractContextManager[None]:
    return override_next_settings(DEFER=scope)


def executor(**options: object) -> AbstractContextManager[None]:
    return defer(EXECUTOR=options)


def query(sql: str = "SELECT 1") -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql)
