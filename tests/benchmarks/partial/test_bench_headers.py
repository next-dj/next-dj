from __future__ import annotations

import pytest

from next.partial.headers import _parse_intent, partial_intent
from tests.support import partial_meta, plain_get


class TestBenchIntentParse:
    """Header parsing, the first thing every request pays on the shaper gate.

    The parse is benched through the private entry point, because the public
    one memoises on the request and would measure a dict lookup instead.
    """

    @pytest.mark.benchmark(group="partial.intent")
    def test_parse_plain_request(self, benchmark) -> None:
        request = plain_get("/zoned/")
        assert _parse_intent(request).partial is False
        benchmark(_parse_intent, request)

    @pytest.mark.benchmark(group="partial.intent")
    def test_parse_partial_request(self, benchmark) -> None:
        request = plain_get("/zoned/")
        request.META.update(
            partial_meta(zones="results,feed", validate="title,slug", merge="append")
        )
        assert _parse_intent(request).partial is True
        benchmark(_parse_intent, request)

    @pytest.mark.benchmark(group="partial.intent")
    def test_memoised_read(self, benchmark) -> None:
        request = plain_get("/zoned/")
        partial_intent(request)
        benchmark(partial_intent, request)
