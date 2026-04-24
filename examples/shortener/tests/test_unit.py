from __future__ import annotations

from unittest import mock

import pytest
from django.core.cache import cache
from django.db import IntegrityError
from shortener import cache as cache_module
from shortener.cache import flush_clicks, increment_clicks
from shortener.models import Link
from shortener.receivers import action_counts
from shortener.routes.page import (
    SLUG_ATTEMPTS_PER_LENGTH,
    SLUG_MAX_LENGTH,
    _create_link_with_unique_slug,
)

from next.forms.signals import action_dispatched


class TestLinkModel:
    """Pure-Python behaviour of the `Link` model."""

    def test_str_contains_slug_and_url(self) -> None:
        link = Link(slug="abc", url="https://example.com/a")
        assert str(link) == "abc → https://example.com/a"


class TestFlushClicksNoPending:
    """`flush_clicks` is a no-op when the cache has nothing to persist."""

    def test_returns_zero_when_cache_empty(self) -> None:
        Link.objects.create(slug="idle", url="https://example.com/i")
        assert flush_clicks() == 0
        assert Link.objects.get(slug="idle").clicks == 0


class TestFlushClicksDecrMissing:
    """`flush_clicks` tolerates a cache key vanishing between snapshot and decr."""

    def test_missing_key_is_swallowed(self) -> None:
        Link.objects.create(slug="gone", url="https://example.com/g")
        increment_clicks("gone")
        with mock.patch.object(
            cache_module.cache,
            "decr",
            side_effect=ValueError("missing"),
        ):
            assert flush_clicks() == 1
        assert Link.objects.get(slug="gone").clicks == 1


class TestGenerateSlugIntegrityRetry:
    """`_create_link_with_unique_slug` retries on IntegrityError."""

    def test_retries_until_unique(self) -> None:
        Link.objects.create(slug="abcdef", url="https://example.com/first")
        # Force the first attempt to collide with the existing slug; the
        # retry uses the next random slug from `secrets.choice`.
        candidates = iter(["abcdef", "xyz123"])
        with mock.patch(
            "shortener.routes.page._random_slug",
            side_effect=lambda _length: next(candidates),
        ):
            link = _create_link_with_unique_slug("https://example.com/second")
        assert link.slug == "xyz123"

    def test_raises_when_slug_space_exhausted(self) -> None:
        Link.objects.create(slug="fixed", url="https://example.com/s")
        total = SLUG_ATTEMPTS_PER_LENGTH * (SLUG_MAX_LENGTH - 6 + 1)
        with (
            mock.patch(
                "shortener.routes.page._random_slug",
                return_value="fixed",
            ),
            mock.patch.object(
                Link.objects,
                "create",
                side_effect=IntegrityError("dup"),
            ),
            pytest.raises(RuntimeError, match="Could not allocate"),
        ):
            _create_link_with_unique_slug("https://example.com/x")
        assert total > 0


class TestReceivers:
    """`shortener.receivers` observes `action_dispatched` and exposes counts."""

    def setup_method(self) -> None:
        """Clear the action counter cache before each test."""
        cache.clear()

    def test_action_counts_empty_when_nothing_dispatched(self) -> None:
        assert action_counts() == {}

    def test_remember_is_idempotent(self) -> None:
        action_dispatched.send(sender=None, action_name="noop")
        action_dispatched.send(sender=None, action_name="noop")
        assert action_counts() == {"noop": 2}
