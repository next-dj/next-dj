from collections.abc import Callable

import pytest
from django.conf import settings
from django.core.checks.registry import registry

from next.suspense.checks import (
    check_connection_sources,
    check_pool_budget,
    check_pool_timeout,
    check_thread_sources,
)
from next.suspense.checks.connections import Pool, pools
from next.testing import override_next_settings


class TestChecks:
    """W155 and W171 name each uncovered source."""

    def test_each_uncovered_source_warns(self, deployment: Callable[..., None]) -> None:
        deployment(
            apps=("crum",),
            routers=("a.Router",),
            middleware=("tenant_schemas.middleware.TenantMiddleware",),
        )
        connection_found = check_connection_sources()
        thread_found = check_thread_sources()
        assert [message.id for message in connection_found] == ["next.W155"] * 2
        assert "'routers:a.Router'" in connection_found[0].msg
        assert [message.id for message in thread_found] == ["next.W171"]
        assert "'django-crum:crum'" in thread_found[0].msg

    def test_nothing_uncovered_warns_nothing(self) -> None:
        assert check_connection_sources() == []
        assert check_thread_sources() == []


_POSTGRES = "django.db.backends.postgresql"


def _pooled(pool: object, engine: str = _POSTGRES) -> dict[str, object]:
    return {"ENGINE": engine, "NAME": "db", "OPTIONS": {"pool": pool}}


class TestPools:
    """The pool of an alias is read from its settings with the psycopg_pool defaults."""

    @pytest.mark.parametrize(
        ("pool", "size", "timeout"),
        [
            pytest.param(True, 4, 30.0, id="defaults"),
            pytest.param({"min_size": 2}, 2, 30.0, id="min-size-only"),
            pytest.param({"min_size": 2, "max_size": 20}, 20, 30.0, id="max-size"),
            pytest.param({"max_size": None, "timeout": 5}, 4, 5.0, id="max-size-none"),
            pytest.param({"max_size": "many", "timeout": True}, None, None, id="bad"),
            pytest.param({"min_size": 2.5}, None, 30.0, id="fractional"),
        ],
    )
    def test_the_size_and_the_wait_are_read(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pool: object,
        size: int | None,
        timeout: float | None,
    ) -> None:
        monkeypatch.setattr(settings, "DATABASES", {"default": _pooled(pool)})
        assert list(pools()) == [Pool("default", size, timeout)]

    @pytest.mark.parametrize(
        "config",
        [
            pytest.param(_pooled(True, "django.db.backends.sqlite3"), id="sqlite"),
            pytest.param(_pooled(False), id="off"),
            pytest.param({"ENGINE": _POSTGRES, "OPTIONS": "x"}, id="odd-options"),
            pytest.param({"ENGINE": _POSTGRES}, id="no-options"),
            pytest.param("not a mapping", id="odd-alias"),
        ],
    )
    def test_an_alias_without_a_pool_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch, config: object
    ) -> None:
        monkeypatch.setattr(settings, "DATABASES", {"default": config})
        assert list(pools()) == []

    def test_postgis_pools_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gis = "django.contrib.gis.db.backends.postgis"
        monkeypatch.setattr(settings, "DATABASES", {"geo": _pooled(True, gis)})
        assert [pool.alias for pool in pools()] == ["geo"]


class TestPoolChecks:
    """next.W162 and next.W172 compare each pool with the budget and the deadline."""

    def test_a_default_pool_is_below_the_default_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "DATABASES", {"default": _pooled(True)})
        [message] = check_pool_budget()
        assert message.id == "next.W162"
        assert "holds at most 4 connections, below the 17" in message.msg
        assert "at least 17" in (message.hint or "")

    def test_a_pool_as_large_as_the_budget_is_quiet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pool = {"max_size": 3, "timeout": 2}
        monkeypatch.setattr(settings, "DATABASES", {"default": _pooled(pool)})
        defer = {"REQUEST_CONCURRENCY": 1, "EXECUTOR": {"OPTIONS": {"MAX_WORKERS": 2}}}
        with override_next_settings(DEFER=defer):
            assert check_pool_budget() == []
            assert check_pool_timeout() == []

    def test_an_unreadable_size_is_left_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pool = {"max_size": "many", "timeout": "soon"}
        monkeypatch.setattr(settings, "DATABASES", {"default": _pooled(pool)})
        assert check_pool_budget() == []
        assert check_pool_timeout() == []

    def test_the_default_wait_outlasts_the_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "DATABASES", {"default": _pooled(True)})
        [message] = check_pool_timeout()
        assert message.id == "next.W172"
        assert "waits up to 30 seconds" in message.msg
        assert "DEADLINE'] of 10 seconds" in message.msg


def test_the_connection_checks_are_registered() -> None:
    registered = set(registry.get_checks())
    assert {
        check_connection_sources,
        check_pool_budget,
        check_pool_timeout,
        check_thread_sources,
    } <= registered
