import math

import pytest
from django.core.checks import Error
from django.test import override_settings

from next.contrib.channels.broker import ChannelsLayerBroker
from next.realtime import MemoryBroker, checks
from next.realtime.checks import check_realtime_settings


class CustomBroker(MemoryBroker):
    pass


def test_check_custom_broker_own_options_report_nothing() -> None:
    config = {
        "REALTIME": {
            "BROKER": {
                "BACKEND": "tests.realtime.test_checks.CustomBroker",
                "OPTIONS": {"SERVERS": ["broker.internal:4222"]},
            }
        }
    }
    with override_settings(NEXT_FRAMEWORK=config):
        assert check_realtime_settings() == []


@pytest.mark.parametrize("backend", ["next.realtime.BrokerBackend", [], False])
def test_check_rejects_abstract_or_invalid_backend(backend) -> None:
    config = {"REALTIME": {"BROKER": {"BACKEND": backend}}}
    with override_settings(NEXT_FRAMEWORK=config):
        assert {item.id for item in check_realtime_settings()} == {"next.E186"}


def test_check_builtin_backend_path_bad_options_report_errors() -> None:
    config = {
        "REALTIME": {
            "BROKER": {
                "BACKEND": "next.realtime.backends.MemoryBroker",
                "OPTIONS": {"QUEUE_SIZE": 0, "URL": "redis://unused"},
            }
        }
    }
    with override_settings(NEXT_FRAMEWORK=config):
        assert {item.id for item in check_realtime_settings()} == {
            "next.E035",
            "next.E178",
        }


def test_check_ignores_missing_or_non_mapping_realtime_setting() -> None:
    with override_settings(NEXT_FRAMEWORK={}):
        assert check_realtime_settings() == []
    with override_settings(NEXT_FRAMEWORK={"REALTIME": "invalid"}):
        assert check_realtime_settings() == []


def test_check_reports_unknown_realtime_and_broker_keys() -> None:
    config = {
        "REALTIME": {
            "UNKNOWN": True,
            "BROKER": {"UNKNOWN": True, "OPTIONS": {"UNKNOWN": True}},
        }
    }
    with override_settings(NEXT_FRAMEWORK=config):
        messages = check_realtime_settings()
    assert len(messages) == 3
    assert {message.id for message in messages} == {"next.E035"}


def test_check_reports_unknown_realtime_keys_without_broker() -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"UNKNOWN": True}}):
        messages = check_realtime_settings()
    assert len(messages) == 1
    assert messages[0].id == "next.E035"


def test_check_reports_broker_options_and_queue_size_errors() -> None:
    config = {
        "REALTIME": {
            "BROKER": {
                "BACKEND": "next.realtime.MemoryBroker",
                "OPTIONS": {"QUEUE_SIZE": False},
            }
        }
    }
    with override_settings(NEXT_FRAMEWORK=config):
        messages = check_realtime_settings()
    assert len(messages) == 1
    assert isinstance(messages[0], Error)
    assert messages[0].id == "next.E178"


def test_check_reports_invalid_broker_and_options_shapes() -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"BROKER": "invalid"}}):
        assert {message.id for message in check_realtime_settings()} == {"next.E177"}
    config = {"REALTIME": {"BROKER": {"OPTIONS": "invalid"}}}
    with override_settings(NEXT_FRAMEWORK=config):
        assert {message.id for message in check_realtime_settings()} == {"next.E180"}


def test_check_reports_missing_redis_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checks.importlib.util, "find_spec", lambda _name: None)
    config = {"REALTIME": {"BROKER": {"BACKEND": "next.realtime.RedisBroker"}}}
    with override_settings(NEXT_FRAMEWORK=config):
        assert {message.id for message in check_realtime_settings()} == {"next.E179"}


def test_check_reports_non_backend_path() -> None:
    config = {"REALTIME": {"BROKER": {"BACKEND": "django.http.HttpRequest"}}}
    with override_settings(NEXT_FRAMEWORK=config):
        assert {message.id for message in check_realtime_settings()} == {"next.E186"}


def test_check_reports_unimportable_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_backend(_path: str):
        raise ImportError

    monkeypatch.setattr(checks, "import_class_cached", missing_backend)
    config = {"REALTIME": {"BROKER": {"BACKEND": "not.installed.Backend"}}}
    with override_settings(NEXT_FRAMEWORK=config):
        messages = check_realtime_settings()
    assert {message.id for message in messages} == {"next.E186"}


def test_check_valid_realtime_settings_report_nothing() -> None:
    config = {
        "REALTIME": {
            "BROKER": {"BACKEND": "next.realtime.MemoryBroker"},
            "TRANSPORTS": None,
            "TICKET_MAX_AGE": 60,
        }
    }
    with override_settings(NEXT_FRAMEWORK=config):
        assert check_realtime_settings() == []


class ChannelsSubclassBroker(ChannelsLayerBroker):
    pass


@pytest.fixture()
def channels_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checks.apps, "is_installed", lambda _name: True)


def _ids(**overrides: object) -> set[str]:
    with override_settings(**overrides):
        return {message.id for message in check_realtime_settings()}


def _channels_broker(backend: str = "next.contrib.channels.ChannelsLayerBroker"):
    return {"BACKEND": backend, "OPTIONS": {"CACHE_ALIAS": "realtime"}}


_SHARED_LAYER = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer"}}
_SHARED_CACHE = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "realtime": {"BACKEND": "django.core.cache.backends.redis.RedisCache"},
}


def test_check_reports_missing_channels_for_the_channels_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(checks.importlib.util, "find_spec", lambda _name: None)
    config = {"REALTIME": {"BROKER": _channels_broker()}}
    ids = _ids(
        NEXT_FRAMEWORK=config, CHANNEL_LAYERS=_SHARED_LAYER, CACHES=_SHARED_CACHE
    )
    assert ids == {"next.E181"}


@pytest.mark.parametrize("transports", [[], ["sse", "sse"], ["sse", "smoke"], "ws"])
def test_check_rejects_invalid_transport_lists(transports: object) -> None:
    assert _ids(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": transports}}) == {
        "next.E182"
    }


def test_check_reports_ws_without_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checks.importlib.util, "find_spec", lambda _name: None)
    ids = _ids(
        NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}, ASGI_APPLICATION="x"
    )
    assert ids == {"next.E183"}


@pytest.mark.parametrize("age", [0, 601, True, "60"])
def test_check_rejects_ticket_age_outside_bounds(age: object) -> None:
    assert _ids(NEXT_FRAMEWORK={"REALTIME": {"TICKET_MAX_AGE": age}}) == {"next.E184"}


@pytest.mark.parametrize("path", ["ws/", "/ws/?x=1", "/ws/#x", 5])
def test_check_rejects_unusable_ws_path(path: object) -> None:
    assert _ids(NEXT_FRAMEWORK={"REALTIME": {"WS_PATH": path}}) == {"next.E185"}


@pytest.mark.parametrize("heartbeat", [0, -1, math.inf, math.nan, True, "25"])
def test_check_rejects_invalid_heartbeat(heartbeat: object) -> None:
    assert _ids(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": heartbeat}}) == {"next.E187"}


@pytest.mark.parametrize("heartbeat", [1, 0.5, 25.0])
def test_check_accepts_a_positive_heartbeat(heartbeat: object) -> None:
    assert _ids(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": heartbeat}}) == set()


@pytest.mark.usefixtures("channels_app")
def test_check_warns_ws_without_asgi_application() -> None:
    assert _ids(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}) == {"next.W141"}


@pytest.mark.usefixtures("channels_app")
def test_check_warns_ws_without_session_middleware() -> None:
    ids = _ids(
        NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}},
        ASGI_APPLICATION="config.asgi.application",
        MIDDLEWARE=[],
    )
    assert ids == {"next.W142"}


@pytest.mark.usefixtures("channels_app")
def test_check_warns_ws_with_signed_cookie_sessions() -> None:
    ids = _ids(
        NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}},
        ASGI_APPLICATION="config.asgi.application",
        SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
    )
    assert ids == {"next.W145"}


@pytest.mark.parametrize("engine", ["django.contrib.sessions.backends.db", "missing"])
@pytest.mark.usefixtures("channels_app")
def test_check_ignores_server_side_or_unimportable_session_engines(engine: str) -> None:
    ids = _ids(
        NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}},
        ASGI_APPLICATION="config.asgi.application",
        SESSION_ENGINE=engine,
    )
    assert ids == set()


def test_check_skips_ws_warnings_when_ws_is_disabled() -> None:
    ids = _ids(
        NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["sse"]}},
        MIDDLEWARE=[],
        SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
    )
    assert ids == set()


def test_check_infers_ws_from_an_installed_channels_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(checks.apps, "is_installed", lambda _name: True)
    assert _ids(NEXT_FRAMEWORK={"REALTIME": {}}) == {"next.W141"}


@pytest.mark.parametrize(
    "backend",
    [
        "next.contrib.channels.ChannelsLayerBroker",
        "tests.realtime.test_checks.ChannelsSubclassBroker",
    ],
)
def test_check_channels_broker_with_sse_only_still_warns(backend: str) -> None:
    config = {"REALTIME": {"TRANSPORTS": ["sse"], "BROKER": {"BACKEND": backend}}}
    assert _ids(NEXT_FRAMEWORK=config, CHANNEL_LAYERS={}) == {"next.W143", "next.W144"}


@pytest.mark.parametrize(
    "cache",
    [
        "django.core.cache.backends.locmem.LocMemCache",
        "django.core.cache.backends.dummy.DummyCache",
        "django.core.cache.backends.db.DatabaseCache",
        "django.core.cache.backends.filebased.FileBasedCache",
    ],
)
def test_check_channels_broker_non_atomic_counter_cache_warns(cache: str) -> None:
    config = {"REALTIME": {"TRANSPORTS": ["sse"], "BROKER": _channels_broker()}}
    caches = {"default": {"BACKEND": cache}, "realtime": {"BACKEND": cache}}
    ids = _ids(NEXT_FRAMEWORK=config, CHANNEL_LAYERS=_SHARED_LAYER, CACHES=caches)
    assert ids == {"next.W144"}


@pytest.mark.parametrize(
    "cache",
    [
        "django.core.cache.backends.redis.RedisCache",
        "django.core.cache.backends.memcached.PyMemcacheCache",
        "missing.cache.Backend",
    ],
)
def test_check_channels_broker_shared_or_unknown_counter_cache_stays_quiet(
    cache: str,
) -> None:
    config = {"REALTIME": {"TRANSPORTS": ["sse"], "BROKER": _channels_broker()}}
    caches = {"default": {"BACKEND": cache}, "realtime": {"BACKEND": cache}}
    assert _ids(NEXT_FRAMEWORK=config, CHANNEL_LAYERS=_SHARED_LAYER, CACHES=caches) == (
        set()
    )


def test_check_channels_broker_malformed_layer_and_cache_settings_do_not_raise() -> (
    None
):
    config = {
        "REALTIME": {
            "TRANSPORTS": ["sse"],
            "BROKER": {
                "BACKEND": "next.contrib.channels.ChannelsLayerBroker",
                "OPTIONS": {"CACHE_ALIAS": 5},
            },
        }
    }
    ids = _ids(
        NEXT_FRAMEWORK=config,
        CHANNEL_LAYERS={"default": "invalid"},
        CACHES={"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache"}},
    )
    assert ids == {"next.W143", "next.W144"}


def test_check_memory_broker_sse_only_reports_no_storage_warnings() -> None:
    config = {"REALTIME": {"TRANSPORTS": ["sse"]}}
    assert _ids(NEXT_FRAMEWORK=config, CHANNEL_LAYERS={}) == set()


@pytest.mark.parametrize("interval", [-1, math.inf, True, "10"])
def test_check_rejects_an_invalid_redis_health_check_interval(interval: object) -> None:
    config = {
        "REALTIME": {
            "BROKER": {
                "BACKEND": "next.realtime.RedisBroker",
                "OPTIONS": {"HEALTH_CHECK_INTERVAL": interval},
            }
        }
    }
    assert _ids(NEXT_FRAMEWORK=config) == {"next.E188"}


@pytest.mark.parametrize("interval", [0, 2.5])
def test_check_accepts_a_redis_health_check_interval(interval: object) -> None:
    config = {
        "REALTIME": {
            "BROKER": {
                "BACKEND": "next.realtime.RedisBroker",
                "OPTIONS": {"HEALTH_CHECK_INTERVAL": interval},
            }
        }
    }
    assert _ids(NEXT_FRAMEWORK=config) == set()


def test_check_reports_ws_without_the_channels_app() -> None:
    ids = _ids(
        NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}, ASGI_APPLICATION="x"
    )
    assert ids == {"next.E189"}


@pytest.mark.usefixtures("channels_app")
@pytest.mark.parametrize("config", [{}, {"REALTIME": None}, None])
def test_check_evaluates_defaults_without_a_realtime_key(config: object) -> None:
    ids = _ids(
        NEXT_FRAMEWORK=config,
        MIDDLEWARE=[],
        SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
    )
    assert ids == {"next.W141", "next.W142", "next.W145"}


def test_check_defaults_stay_quiet_without_the_channels_app() -> None:
    assert _ids(NEXT_FRAMEWORK={}) == set()
