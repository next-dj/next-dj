from __future__ import annotations

import inspect
from unittest import mock

import pytest
from django.db import IntegrityError
from django.http import Http404
from shortener import cache as cache_module
from shortener.cache import flush_clicks, increment_clicks
from shortener.models import Link
from shortener.providers import DLink, LinkProvider
from shortener.receivers import action_counts
from shortener.routes.page import (
    SLUG_ATTEMPTS_PER_LENGTH,
    SLUG_MAX_LENGTH,
    _create_link_with_unique_slug,
)

from next.forms.signals import action_dispatched
from next.testing import make_resolution_context


pytestmark = pytest.mark.django_db


class TestLinkModel:
    """Pure-Python behaviour of the `Link` model."""

    def test_str_contains_slug_and_url(self) -> None:
        link = Link(slug="abc", url="https://example.com/a")
        assert str(link) == "abc → https://example.com/a"


class TestFlushClicksNoPending:
    """`flush_clicks` is a no-op when the cache has nothing to persist."""

    def test_returns_zero_when_cache_empty(self, make_link) -> None:
        make_link("idle")
        assert flush_clicks() == 0
        assert Link.objects.get(slug="idle").clicks == 0


class TestFlushClicksDecrMissing:
    """`flush_clicks` tolerates a cache key vanishing between snapshot and decr."""

    def test_missing_key_is_swallowed(self, make_link) -> None:
        make_link("gone")
        increment_clicks("gone")
        with mock.patch.object(
            cache_module.cache, "decr", side_effect=ValueError("missing")
        ):
            assert flush_clicks() == 1
        assert Link.objects.get(slug="gone").clicks == 1


class TestGenerateSlugIntegrityRetry:
    """`_create_link_with_unique_slug` retries on IntegrityError."""

    def test_retries_until_unique(self, make_link) -> None:
        make_link("abcdef")
        # Force the first attempt to collide with the existing slug so the
        # retry uses the next random slug from `secrets.choice`.
        candidates = iter(["abcdef", "xyz123"])
        with mock.patch(
            "shortener.routes.page._random_slug",
            side_effect=lambda _length: next(candidates),
        ):
            link = _create_link_with_unique_slug("https://example.com/second")
        assert link.slug == "xyz123"

    def test_raises_when_slug_space_exhausted(self, make_link) -> None:
        make_link("fixed")
        total = SLUG_ATTEMPTS_PER_LENGTH * (SLUG_MAX_LENGTH - 6 + 1)
        with (
            mock.patch("shortener.routes.page._random_slug", return_value="fixed"),
            mock.patch.object(
                Link.objects, "create", side_effect=IntegrityError("dup")
            ),
            pytest.raises(RuntimeError, match="Could not allocate"),
        ):
            _create_link_with_unique_slug("https://example.com/x")
        assert total > 0


class TestReceivers:
    """`shortener.receivers` observes `action_dispatched` and exposes counts."""

    def test_action_counts_empty_when_nothing_dispatched(self) -> None:
        assert action_counts() == {}

    def test_remember_is_idempotent(self) -> None:
        action_dispatched.send(sender=None, action_name="noop")
        action_dispatched.send(sender=None, action_name="noop")
        assert action_counts() == {"noop": 2}


def _link_param(annotation: object) -> inspect.Parameter:
    """Build the parameter a page would declare with the given annotation."""
    return inspect.Parameter(
        "link", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation
    )


class TestLinkProvider:
    """`LinkProvider` settles `DLink[...]` on the signature and compiles the lookup."""

    @pytest.mark.parametrize(
        ("annotation", "expected"),
        [(DLink[Link], True), (Link, False), (inspect.Parameter.empty, False)],
        ids=["marker", "plain_model", "no_annotation"],
    )
    def test_static_verdict_agrees_with_can_handle(self, annotation, expected) -> None:
        provider = LinkProvider()
        param = _link_param(annotation)
        ctx = make_resolution_context(url_kwargs={"slug": "abc123"})
        assert provider.static_can_handle(param) is expected
        assert provider.can_handle(param, ctx) is expected

    def test_compiled_filler_returns_what_resolve_returns(self, make_link) -> None:
        link = make_link("compiled")
        provider = LinkProvider()
        param = _link_param(DLink[Link])
        ctx = make_resolution_context(url_kwargs={"slug": "compiled"})
        fill = provider.compile_resolve(param)
        assert fill(ctx) == link
        assert provider.resolve(param, ctx) == link

    def test_both_paths_raise_404_for_an_unknown_slug(self) -> None:
        provider = LinkProvider()
        param = _link_param(DLink[Link])
        ctx = make_resolution_context(url_kwargs={"slug": "missing"})
        fill = provider.compile_resolve(param)
        with pytest.raises(Http404):
            fill(ctx)
        with pytest.raises(Http404):
            provider.resolve(param, ctx)

    def test_both_paths_need_the_slug_kwarg(self) -> None:
        provider = LinkProvider()
        param = _link_param(DLink[Link])
        ctx = make_resolution_context()
        fill = provider.compile_resolve(param)
        with pytest.raises(KeyError):
            fill(ctx)
        with pytest.raises(KeyError):
            provider.resolve(param, ctx)
