"""Where the client puts a boundary token and where the server reads it from."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Final, override

from django.http import HttpRequest, QueryDict


HEADER: Final = "X-Next-Boundary"
"""The request header `HeaderTransport` carries a token in."""

FIELD: Final = "_nb"
"""The query or form field `QueryTransport` and `BodyTransport` carry a token in."""


@dataclass(frozen=True, slots=True)
class TransportDescriptor:
    """How a transport tells the client where a token goes, `place` and `name`."""

    place: str
    name: str

    @property
    def attribute(self) -> str:
        """Return the value of the wrapper attribute that names the transport."""
        return f"{self.place}:{self.name}"

    def as_dict(self) -> dict[str, str]:
        """Return the descriptor as the mapping a JSON payload carries."""
        return {"place": self.place, "name": self.name}


def _joined(values: list[str]) -> str | None:
    """Return the value sent, several joined by a comma that no token parses with."""
    if not values:
        return None
    return ",".join(values)


class TokenTransport(ABC):
    """Read a token from a request and describe its place to the client."""

    place: ClassVar[str]
    name: ClassVar[str]

    @abstractmethod
    def extract(self, request: HttpRequest) -> str | None:
        """Return the token the request carries, `None` when it carries none."""

    def describe(self) -> TransportDescriptor:
        """Return the descriptor the wrapper markup carries."""
        return TransportDescriptor(self.place, self.name)


class HeaderTransport(TokenTransport):
    """Carry the token in `X-Next-Boundary`, off access logs and `Referer`."""

    place = "header"
    name = HEADER

    @override
    def extract(self, request: HttpRequest) -> str | None:
        value = request.headers.get(self.name)
        return value if isinstance(value, str) and value else None


class _FieldTransport(TokenTransport):
    """Carry the token in one field of a query string or a form body."""

    name = FIELD

    @abstractmethod
    def fields(self, request: HttpRequest) -> QueryDict:
        """Return the fields the token is read from."""

    @override
    def extract(self, request: HttpRequest) -> str | None:
        return _joined(self.fields(request).getlist(self.name))


class QueryTransport(_FieldTransport):
    """Carry the token in `?_nb=`, where access logs and `Referer` headers see it."""

    place = "query"

    @override
    def fields(self, request: HttpRequest) -> QueryDict:
        return request.GET


class BodyTransport(_FieldTransport):
    """Carry the token in the `_nb` field of a form body, which has no size limit."""

    place = "body"

    @override
    def fields(self, request: HttpRequest) -> QueryDict:
        return request.POST


__all__ = [
    "FIELD",
    "HEADER",
    "BodyTransport",
    "HeaderTransport",
    "QueryTransport",
    "TokenTransport",
    "TransportDescriptor",
]
