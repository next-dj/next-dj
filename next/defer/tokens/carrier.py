"""Where the client puts a boundary token and where the server reads it from.

The client runtime knows these three places alone, so `CARRIER` names one of them.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Final, override

from django.http import HttpRequest, QueryDict

from next.partial.headers import BOUNDARY
from next.utils import request_header


FIELD: Final = "_nb"
"""The query or form field the `query` and `body` carriers hold a token in."""


@dataclass(frozen=True, slots=True)
class CarrierPlace:
    """How a carrier tells the client where a token goes, `place` and `name`."""

    place: str
    name: str

    @property
    def attribute(self) -> str:
        """Return the value of the wrapper attribute that names the carrier."""
        return f"{self.place}:{self.name}"

    def as_dict(self) -> dict[str, str]:
        """Return the descriptor as the mapping a JSON payload carries."""
        return {"place": self.place, "name": self.name}


def _joined(values: list[str]) -> str | None:
    """Return the value sent, several joined by a comma that no token parses with."""
    if not values:
        return None
    return ",".join(values)


class TokenCarrier(ABC):
    """Read a token from a request and describe its place to the client."""

    place: ClassVar[str]
    name: ClassVar[str]
    methods: ClassVar[tuple[str, ...]] = ("GET", "HEAD")
    """The methods a reload carrying the token in this place is sent with."""

    @abstractmethod
    def extract(self, request: HttpRequest) -> str | None:
        """Return the token the request carries, `None` when it carries none."""

    def describe(self) -> CarrierPlace:
        """Return the descriptor the wrapper markup carries."""
        return CarrierPlace(self.place, self.name)


class HeaderCarrier(TokenCarrier):
    """Carry the token in `X-Next-Boundary`, off access logs and `Referer`."""

    place = "header"
    name = BOUNDARY

    @override
    def extract(self, request: HttpRequest) -> str | None:
        return request_header(request, self.name) or None


class _FieldCarrier(TokenCarrier):
    """Carry the token in one field of a query string or a form body."""

    name = FIELD

    @abstractmethod
    def fields(self, request: HttpRequest) -> QueryDict:
        """Return the fields the token is read from."""

    @override
    def extract(self, request: HttpRequest) -> str | None:
        return _joined(self.fields(request).getlist(self.name))


class QueryCarrier(_FieldCarrier):
    """Carry the token in `?_nb=`, where access logs and `Referer` headers see it."""

    place = "query"

    @override
    def fields(self, request: HttpRequest) -> QueryDict:
        return request.GET


class BodyCarrier(_FieldCarrier):
    """Carry the token in the `_nb` field of a form body, which has no size limit."""

    place = "body"
    methods = ("GET", "HEAD", "POST")

    @override
    def fields(self, request: HttpRequest) -> QueryDict:
        return request.POST


CARRIERS: Final[dict[str, TokenCarrier]] = {
    carrier.place: carrier
    for carrier in (HeaderCarrier(), QueryCarrier(), BodyCarrier())
}
"""The carriers `TOKENS["OPTIONS"]["CARRIER"]` names, by the place they use."""


__all__ = [
    "CARRIERS",
    "FIELD",
    "BodyCarrier",
    "CarrierPlace",
    "HeaderCarrier",
    "QueryCarrier",
    "TokenCarrier",
]
