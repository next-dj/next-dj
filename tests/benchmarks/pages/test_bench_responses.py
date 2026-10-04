from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.http import HttpResponse

from next.pages.manager import page as page_singleton
from next.pages.responses import finish_response, response_policy
from tests.benchmarks.factories import build_layout_page
from tests.support import plain_get, unified_view


if TYPE_CHECKING:
    from pathlib import Path


_TEMPLATE = "<h1>{{ request.path }}</h1>" + "".join(
    f"<p>row {i}</p>" for i in range(10)
)
_POLICY_PAGE = (
    "cache = {'public': True, 'max_age': 60, 's_maxage': 300}\n"
    "headers = {'Cross-Origin-Opener-Policy': 'same-origin'}\n"
)


class TestBenchResponsePolicy:
    """What the response layer adds to a page GET once its policy is memoised."""

    @pytest.mark.parametrize(
        "body", ["x = 1\n", _POLICY_PAGE], ids=["no-policy", "static-policy"]
    )
    @pytest.mark.benchmark(group="pages.responses")
    def test_policy_lookup(self, tmp_path: Path, body: str, benchmark) -> None:
        """A static policy answers from the memo, a page without one likewise."""
        page_file = build_layout_page(tmp_path, layouts=2, page_body=body)
        request = plain_get("/")
        response_policy(page_singleton, page_file, request, url_kwargs={})
        benchmark(response_policy, page_singleton, page_file, request, url_kwargs={})

    @pytest.mark.parametrize(
        "body", ["x = 1\n", _POLICY_PAGE], ids=["no-policy", "static-policy"]
    )
    @pytest.mark.benchmark(group="pages.responses")
    def test_finish_response(self, tmp_path: Path, body: str, benchmark) -> None:
        """Stamping the headers, the cache and the robots header on one response."""
        page_file = build_layout_page(tmp_path, layouts=2, page_body=body)
        request = plain_get("/")
        policy = response_policy(page_singleton, page_file, request, url_kwargs={})

        def finish() -> None:
            finish_response(HttpResponse("x"), policy, request, page_file)

        benchmark(finish)

    @pytest.mark.parametrize(
        "body", ["x = 1\n", _POLICY_PAGE], ids=["no-policy", "static-policy"]
    )
    @pytest.mark.benchmark(group="pages.view")
    def test_static_view_warm(self, tmp_path: Path, body: str, benchmark) -> None:
        """The warm static GET of `test_bench_view`, with and without a policy."""
        page_file = build_layout_page(
            tmp_path, layouts=2, template=_TEMPLATE, page_body=body
        )
        view = unified_view(page_singleton, page_file)
        request = plain_get("/")
        try:
            view(request)
            benchmark(view, request)
        finally:
            page_singleton.clear_template_caches()
