from collections.abc import Callable
from dataclasses import dataclass

from django.http import HttpRequest

from next.requests import PrefetchDepth, Purpose
from next.testing import NextClient


@dataclass(frozen=True, slots=True)
class KindCase:
    """One request kind, its control request and what `request.next` reports."""

    id: str
    drive: Callable[[NextClient], HttpRequest]
    control: Callable[[NextClient], HttpRequest]
    purpose: Purpose = Purpose.DOCUMENT
    depth: PrefetchDepth | None = None
