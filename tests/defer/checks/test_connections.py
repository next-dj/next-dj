from collections.abc import Callable

import pytest
from django.conf import settings
from django.core.checks.registry import registry

from next.conf.defer import defer_count
from next.defer.checks import (
    check_connection_budget,
    check_connection_sources,
    check_pool_budget,
    check_pool_timeout,
    check_thread_sources,
)
from next.defer.checks.connections import Pool, pools
from next.testing import override_next_settings


def _default_budget() -> int:
    """Return the connections one process holds at the default settings."""
    workers = defer_count("EXECUTOR", "OPTIONS", "MAX_WORKERS")
    return defer_count("REQUEST_CONCURRENCY") + workers


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
        budget = _default_budget()
        assert message.id == "next.W162"
        assert f"holds at most 4 connections, below the {budget}" in message.msg
        assert f"at least {budget}" in (message.hint or "")

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


def _plain(**config: object) -> dict[str, object]:
    return {"ENGINE": _POSTGRES, "NAME": "db", **config}


class TestConnectionBudget:
    """next.W189 states the connections one process may open on an unpooled alias."""

    def test_an_unpooled_alias_gets_the_formula_and_the_number(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "DATABASES", {"default": _plain()})
        defer = {"REQUEST_CONCURRENCY": 8, "EXECUTOR": {"OPTIONS": {"MAX_WORKERS": 16}}}
        with override_next_settings(DEFER=defer):
            [message] = check_connection_budget()
        assert message.id == "next.W189"
        assert "up to 24 connections per process" in message.msg
        assert "REQUEST_CONCURRENCY 8 plus" in message.msg
        assert "MAX_WORKERS'] 16" in message.msg
        assert "worker processes" in message.msg
        assert "opens a new connection" in message.msg
        assert "MAX_WORKERS" in (message.hint or "")

    def test_persistent_connections_stay_open_instead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = _plain(CONN_MAX_AGE=60)
        monkeypatch.setattr(settings, "DATABASES", {"default": config})
        [message] = check_connection_budget()
        assert f"up to {_default_budget()} connections per process" in message.msg
        assert "opens a new connection" not in message.msg

    @pytest.mark.parametrize(
        "databases",
        [
            pytest.param({"default": _pooled(True)}, id="pooled"),
            pytest.param({"default": _pooled(True, "django.db.backends.sqlite3")}),
            pytest.param({"default": "not a mapping"}, id="odd-alias"),
        ],
    )
    def test_a_pooled_or_other_alias_is_left_to_its_own_checks(
        self, monkeypatch: pytest.MonkeyPatch, databases: dict[str, object]
    ) -> None:
        monkeypatch.setattr(settings, "DATABASES", databases)
        assert check_connection_budget() == []

    def test_no_pool_threads_add_no_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "DATABASES", {"default": _plain()})
        with override_next_settings(DEFER={"EXECUTOR": {"PARALLEL": False}}):
            assert check_connection_budget() == []

    def test_it_runs_under_deploy_only(self) -> None:
        assert check_connection_budget in registry.get_checks(
            include_deployment_checks=True
        )
        assert check_connection_budget not in registry.get_checks()


def test_the_connection_checks_are_registered() -> None:
    registered = set(registry.get_checks())
    assert {
        check_connection_sources,
        check_pool_budget,
        check_pool_timeout,
        check_thread_sources,
    } <= registered
