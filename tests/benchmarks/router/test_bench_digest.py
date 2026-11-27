from __future__ import annotations

import pytest

from next.router import ChromeDigest
from next.router.digest import chrome_digest, normalise, split_head


_RESPONSE_ID = "5ea1ed00"
_LINKS = "".join(f'<a href="/docs/topic-{i}/">Topic {i}</a>' for i in range(50))
_CHROME = (
    '<!DOCTYPE html><html lang="en"><head><title>Docs</title>'
    '<link rel="stylesheet" href="/static/site.css">'
    '<script nonce="abc123" src="/static/next/next.min.js"></script></head>'
    f"<body><nav>{_LINKS}</nav>"
    '<form method="post"><input type="hidden" name="csrfmiddlewaretoken" '
    'value="GcEJTa8VOAysxWG66ldXFFxF074PCMx7d5ftY6GF2MDzD9pxl95y1jaxlHmsCnst"></form>'
    "<footer><p>Footer</p></footer></body></html>"
)


class TestBenchChromeDigest:
    """The stamp of one layout, its chrome normalised and then digested."""

    @pytest.mark.benchmark(group="router.digest")
    def test_normalise(self, benchmark) -> None:
        """The nonce and the CSRF value masked out of a root layout of fifty links."""
        normal = benchmark(normalise, _CHROME, _RESPONSE_ID)
        assert "abc123" not in normal
        assert "GcEJTa8V" not in normal

    @pytest.mark.benchmark(group="router.digest")
    def test_chrome_digest(self, benchmark) -> None:
        normal = normalise(_CHROME, _RESPONSE_ID)
        assert len(benchmark(chrome_digest, normal)) == 16

    @pytest.mark.benchmark(group="router.digest")
    def test_root_layout_stamp(self, benchmark) -> None:
        """What a root layout costs per response, its head digested apart."""
        backend = ChromeDigest()

        def stamp() -> str:
            normal = backend.normalise(_CHROME, _RESPONSE_ID)
            parts = split_head(normal)
            assert parts is not None
            return backend.digest(parts[0]) + backend.digest(parts[1])

        assert len(benchmark(stamp)) == 32
