import re
from pathlib import Path

import pytest
from django.test import RequestFactory

from next.defer import Depth
from next.partial import headers
from next.partial.headers import partial_intent


CLIENT = Path(__file__).resolve().parents[2] / "next" / "client"

pytestmark = pytest.mark.skipif(
    not (CLIENT / "next.ts").exists(), reason="needs the source checkout"
)

_HEADER_NAMES = {
    "REQUEST_FLAG": "REQUEST_FLAG",
    "ZONE": "HEADER_ZONE",
    "MERGE": "HEADER_MERGE",
    "VERSION": "HEADER_VERSION",
    "REQUEST_ID": "HEADER_REQUEST_ID",
    "ORIGIN": "HEADER_ORIGIN",
    "NAVIGATE": "HEADER_NAVIGATE",
    "LAYOUTS": "HEADER_LAYOUTS",
    "PREFETCH": "HEADER_PREFETCH",
    "ROUTER": "HEADER_ROUTER",
}
_MEDIA_TYPES = ("CONTENT_TYPE", "ACCEPT", "STREAM_ACCEPT", "EVENT_STREAM")


def _protocol() -> dict[str, str]:
    """Return the exported string constants of `protocol.ts`, resolving templates."""
    source = (CLIENT / "protocol.ts").read_text(encoding="utf-8")
    found = dict(re.findall(r'export const (\w+) = "([^"]*)";', source))
    for name, template in re.findall(r"export const (\w+) = `([^`]*)`;", source):
        found[name] = re.sub(r"\$\{(\w+)\}", lambda hit: found[hit[1]], template)
    return found


def _sent_prefetch_depths() -> set[str]:
    """Return every value `prefetch.ts` sends under the prefetch header."""
    source = (CLIENT / "prefetch.ts").read_text(encoding="utf-8")
    return set(re.findall(r'\[HEADER_PREFETCH\]:\s*"([^"]+)"', source))


class TestHeaderNames:
    """The router and intent headers read the same on the server and the client."""

    @pytest.mark.parametrize(
        ("python", "typescript"), _HEADER_NAMES.items(), ids=_HEADER_NAMES
    )
    def test_the_header_name_is_the_one_the_client_sends(
        self, python: str, typescript: str
    ) -> None:
        assert _protocol()[typescript] == getattr(headers, python)

    @pytest.mark.parametrize("name", _MEDIA_TYPES)
    def test_the_media_type_is_the_one_the_client_negotiates(self, name: str) -> None:
        assert _protocol()[name] == getattr(headers, name)


class TestPrefetchDepths:
    """The depth values the client sends are the ones the server parses."""

    def test_the_client_sends_one_value_per_depth(self) -> None:
        assert len(_sent_prefetch_depths()) == len(Depth)

    def test_each_value_the_client_sends_parses_to_its_own_depth(
        self, rf: RequestFactory
    ) -> None:
        parsed = set()
        for wire in _sent_prefetch_depths():
            request = rf.get(
                "/",
                headers={
                    headers.REQUEST_FLAG: "1",
                    headers.NAVIGATE: "1",
                    headers.PREFETCH: wire,
                },
            )
            parsed.add(partial_intent(request).prefetch)
        assert parsed == set(Depth)
