import importlib.util
import math
from types import ModuleType

import pytest
from django.core.checks import Error
from django.core.checks.registry import registry as check_registry
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.test import override_settings
from django.urls import URLPattern, include, path

from next.realtime import (
    MemoryBroker,
    SessionBinding,
    SignedCookieBinding,
    TicketBinding,
)
from next.realtime.checks import (
    check_realtime_settings,
    check_shared_broker,
    check_ticket_introspection,
)
from next.realtime.manager import BrokerManager
from next.realtime.replay import MAX_SIZE, MAX_TTL
from next.realtime.transports import active_transports
from next.realtime.views import TicketIntrospectionView


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
        assert {item.id for item in check_realtime_settings()} == {"next.E184"}


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
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
    config = {"REALTIME": {"BROKER": {"BACKEND": "next.realtime.RedisBroker"}}}
    with override_settings(NEXT_FRAMEWORK=config):
        assert {message.id for message in check_realtime_settings()} == {"next.E179"}


def test_check_reports_non_backend_path() -> None:
    config = {"REALTIME": {"BROKER": {"BACKEND": "django.http.HttpRequest"}}}
    with override_settings(NEXT_FRAMEWORK=config):
        assert {message.id for message in check_realtime_settings()} == {"next.E184"}


def test_check_reports_unimportable_backend() -> None:
    config = {"REALTIME": {"BROKER": {"BACKEND": "not.installed.Backend"}}}
    with override_settings(NEXT_FRAMEWORK=config):
        messages = check_realtime_settings()
    assert {message.id for message in messages} == {"next.E184"}


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


def _ids(**overrides: object) -> set[str]:
    with override_settings(**overrides):
        return {message.id for message in check_realtime_settings()}


_WS = {"REALTIME": {"TRANSPORTS": ["ws"]}}
_SIGNED = "django.contrib.sessions.backends.signed_cookies"
_SIGNED_BINDING = "next.realtime.SignedCookieBinding"


def _redis(**options: object) -> dict[str, object]:
    return {
        "REALTIME": {
            "BROKER": {"BACKEND": "next.realtime.RedisBroker", "OPTIONS": options}
        }
    }


@pytest.mark.parametrize("transports", [[], ["sse", "sse"], ["sse", "smoke"], "ws"])
def test_check_rejects_invalid_transport_lists(transports: object) -> None:
    assert _ids(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": transports}}) == {
        "next.E181"
    }


@pytest.mark.parametrize("age", [0, 601, True, "60"])
def test_check_rejects_ticket_age_outside_bounds(age: object) -> None:
    assert _ids(NEXT_FRAMEWORK={"REALTIME": {"TICKET_MAX_AGE": age}}) == {"next.E182"}


@pytest.mark.parametrize("path", ["ws/", "/ws/?x=1", "/ws/#x", 5])
def test_check_rejects_unusable_ws_path(path: object) -> None:
    assert _ids(NEXT_FRAMEWORK={"REALTIME": {"WS_PATH": path}}) == {"next.E183"}


@pytest.mark.parametrize("heartbeat", [0, -1, math.inf, math.nan, True, "25"])
def test_check_rejects_invalid_heartbeat(heartbeat: object) -> None:
    assert _ids(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": heartbeat}}) == {"next.E185"}


@pytest.mark.parametrize("heartbeat", [1, 0.5, 25.0])
def test_check_accepts_a_positive_heartbeat(heartbeat: object) -> None:
    assert _ids(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": heartbeat}}) == set()


@pytest.mark.parametrize(
    ("replay", "expected"),
    [
        ({"SIZE": 0, "TTL": 1}, set()),
        ({"SIZE": 500}, set()),
        ({"TTL": 0.5}, set()),
        (None, {"next.E191"}),
        ({"SIZE": -1}, {"next.E189"}),
        ({"SIZE": 1.5}, {"next.E189"}),
        ({"SIZE": True}, {"next.E189"}),
        ({"SIZE": MAX_SIZE + 1}, {"next.E189"}),
        ({"SIZE": MAX_SIZE}, set()),
        ({"TTL": 0}, {"next.E190"}),
        ({"TTL": math.inf}, {"next.E190"}),
        ({"TTL": "300"}, {"next.E190"}),
        ({"TTL": 1e300}, {"next.E190"}),
        ({"TTL": MAX_TTL + 0.5}, {"next.E190"}),
        ({"TTL": MAX_TTL}, set()),
        ({"SIZE": -1, "TTL": 0}, {"next.E189", "next.E190"}),
        ({"LIMIT": 1}, {"next.E035"}),
        ([100, 300], {"next.E191"}),
    ],
)
def test_check_validates_the_replay_log(replay: object, expected: set[str]) -> None:
    assert _ids(NEXT_FRAMEWORK={"REALTIME": {"REPLAY": replay}}) == expected


def test_check_explicit_ws_accepts_a_configured_deployment() -> None:
    assert _ids(NEXT_FRAMEWORK=_WS) == set()


def test_check_warns_ws_without_session_middleware() -> None:
    assert _ids(NEXT_FRAMEWORK=_WS, MIDDLEWARE=[]) == {"next.W141"}


def test_check_warns_a_ws_tuple_without_session_middleware() -> None:
    config = {"REALTIME": {"TRANSPORTS": ("ws",)}}
    assert _ids(NEXT_FRAMEWORK=config, MIDDLEWARE=[]) == {"next.W141"}


@pytest.mark.parametrize(
    ("binding", "expected"),
    [
        ("next.realtime.SessionBinding", {"next.W141"}),
        ("tests.realtime.test_checks.SessionKeyBinding", {"next.W141"}),
        ("next.realtime.SignedCookieBinding", {"next.W147"}),
        ("tests.realtime.test_binding.DeviceBinding", set()),
    ],
)
def test_check_w141_follows_the_ticket_binding(binding: str, expected: set) -> None:
    config = {"REALTIME": {**_WS["REALTIME"], "TICKET_BINDING": binding}}
    assert _ids(NEXT_FRAMEWORK=config, MIDDLEWARE=[]) == expected


def test_check_warns_ws_with_signed_cookie_sessions() -> None:
    with override_settings(NEXT_FRAMEWORK=_WS, SESSION_ENGINE=_SIGNED):
        (warning,) = check_realtime_settings()
    assert warning.id == "next.W142"
    assert "re-signs on every session save" in warning.msg
    assert "4401" in warning.msg
    assert "outlives logout" in warning.hint


class SessionKeyBinding(SessionBinding):
    pass


class AbstractBinding(TicketBinding):
    pass


def test_check_w142_points_at_the_signed_cookie_binding() -> None:
    with override_settings(NEXT_FRAMEWORK=_WS, SESSION_ENGINE=_SIGNED):
        (warning,) = check_realtime_settings()
    assert "next.realtime.SignedCookieBinding" in warning.hint


@pytest.mark.parametrize(
    ("binding", "expected"),
    [
        ("next.realtime.SignedCookieBinding", set()),
        ("tests.realtime.test_binding.DeviceBinding", set()),
        ("tests.realtime.test_checks.SessionKeyBinding", {"next.W142"}),
        ("next.realtime.SessionBinding", {"next.W142"}),
    ],
)
def test_check_w142_follows_the_ticket_binding(binding: str, expected: set) -> None:
    config = {"REALTIME": {**_WS["REALTIME"], "TICKET_BINDING": binding}}
    assert _ids(NEXT_FRAMEWORK=config, SESSION_ENGINE=_SIGNED) == expected


class VisitorBinding(SignedCookieBinding):
    pass


@pytest.mark.parametrize(
    ("binding", "engine", "expected"),
    [
        ("next.realtime.SignedCookieBinding", "db", {"next.W147"}),
        ("tests.realtime.test_checks.VisitorBinding", "cache", {"next.W147"}),
        ("next.realtime.SignedCookieBinding", "signed_cookies", set()),
        ("next.realtime.SessionBinding", "db", set()),
    ],
)
def test_check_w147_flags_a_signed_cookie_binding_on_server_sessions(
    binding: str, engine: str, expected: set[str]
) -> None:
    config = {"REALTIME": {**_WS["REALTIME"], "TICKET_BINDING": binding}}
    session_engine = f"django.contrib.sessions.backends.{engine}"
    assert _ids(NEXT_FRAMEWORK=config, SESSION_ENGINE=session_engine) == expected


def test_check_w147_names_logout_and_the_session_binding() -> None:
    config = {"REALTIME": {**_WS["REALTIME"], "TICKET_BINDING": _SIGNED_BINDING}}
    with override_settings(NEXT_FRAMEWORK=config):
        (warning,) = check_realtime_settings()
    assert warning.id == "next.W147"
    assert "outlives logout" in warning.msg
    assert "next.realtime.SessionBinding" in warning.hint


def test_check_w147_ignores_an_unimportable_session_engine() -> None:
    config = {"REALTIME": {**_WS["REALTIME"], "TICKET_BINDING": _SIGNED_BINDING}}
    assert _ids(NEXT_FRAMEWORK=config, SESSION_ENGINE="missing") == set()


@pytest.mark.parametrize(
    "binding",
    [
        None,
        42,
        "tests.realtime.missing.Binding",
        "tests.realtime.test_checks.CustomBroker",
        "tests.realtime.test_checks.AbstractBinding",
    ],
)
def test_check_rejects_an_unusable_ticket_binding(binding: object) -> None:
    config = {"REALTIME": {**_WS["REALTIME"], "TICKET_BINDING": binding}}
    assert _ids(NEXT_FRAMEWORK=config, SESSION_ENGINE=_SIGNED) == {"next.E211"}


def test_check_accepts_a_custom_ticket_binding() -> None:
    config = {
        "REALTIME": {"TICKET_BINDING": "tests.realtime.test_checks.SessionKeyBinding"}
    }
    assert _ids(NEXT_FRAMEWORK=config) == set()


_PLAIN = "tests.support.realtime.PlainBroker"


@pytest.mark.parametrize("replay", [{}, {"REPLAY": {"SIZE": 5}}])
def test_check_warns_a_replay_log_on_a_broker_without_replay(replay: dict) -> None:
    config = {"REALTIME": {"BROKER": {"BACKEND": _PLAIN}, **replay}}
    with override_settings(NEXT_FRAMEWORK=config):
        (warning,) = check_realtime_settings()
    assert warning.id == "next.W143"
    assert "PlainBroker keeps no replay log" in warning.msg
    assert "replay() and areplay()" in warning.hint


@pytest.mark.parametrize(
    ("backend", "replay"),
    [
        (_PLAIN, {"SIZE": 0}),
        (_PLAIN, {"SIZE": -1}),
        ("next.realtime.MemoryBroker", {"SIZE": 5}),
        ("next.realtime.RedisBroker", {"SIZE": 5}),
        ("tests.realtime.test_checks.CustomBroker", None),
    ],
)
def test_check_replay_warning_stays_quiet_when_replay_is_served_or_off(
    backend: str, replay: object
) -> None:
    config = {"REALTIME": {"BROKER": {"BACKEND": backend}, "REPLAY": replay}}
    assert "next.W143" not in _ids(NEXT_FRAMEWORK=config)


@pytest.mark.parametrize("engine", ["django.contrib.sessions.backends.db", "missing"])
def test_check_ignores_server_side_or_unimportable_session_engines(engine: str) -> None:
    assert _ids(NEXT_FRAMEWORK=_WS, SESSION_ENGINE=engine) == set()


@pytest.mark.parametrize("config", [{}, {"REALTIME": None}, None, {"REALTIME": {}}])
def test_check_automatic_transports_leave_warnings_to_the_mounted_app(
    config: object,
) -> None:
    ids = _ids(NEXT_FRAMEWORK=config, MIDDLEWARE=[], SESSION_ENGINE=_SIGNED)
    assert ids == set()


def test_check_skips_ws_warnings_when_ws_is_disabled() -> None:
    config = {"REALTIME": {"TRANSPORTS": ["sse"]}}
    assert _ids(NEXT_FRAMEWORK=config, MIDDLEWARE=[], SESSION_ENGINE=_SIGNED) == set()


@pytest.mark.parametrize("interval", [-1, math.inf, True, "10"])
def test_check_rejects_an_invalid_redis_health_check_interval(interval: object) -> None:
    assert _ids(NEXT_FRAMEWORK=_redis(HEALTH_CHECK_INTERVAL=interval)) == {"next.E186"}


@pytest.mark.parametrize("interval", [0, 2.5])
def test_check_accepts_a_redis_health_check_interval(interval: object) -> None:
    assert _ids(NEXT_FRAMEWORK=_redis(HEALTH_CHECK_INTERVAL=interval)) == set()


@pytest.mark.parametrize("prefix", ["", ":::", "{tenant}", "app{x", "a}b", 7])
def test_check_rejects_a_redis_prefix_that_breaks_the_hash_tag(prefix: object) -> None:
    assert _ids(NEXT_FRAMEWORK=_redis(PREFIX=prefix)) == {"next.E192"}


@pytest.mark.parametrize("prefix", ["next", "app:realtime:"])
def test_check_accepts_a_redis_prefix(prefix: object) -> None:
    assert _ids(NEXT_FRAMEWORK=_redis(PREFIX=prefix)) == set()


@pytest.mark.parametrize("url", ["", None, 6379])
def test_check_rejects_a_redis_url_the_broker_refuses(url: object) -> None:
    assert _ids(NEXT_FRAMEWORK=_redis(URL=url)) == {"next.E213"}


def test_check_accepts_a_redis_url() -> None:
    assert _ids(NEXT_FRAMEWORK=_redis(URL="redis://cache:6379/1")) == set()


@pytest.mark.parametrize("transports", [("sse",), ("ws", "sse")])
def test_check_accepts_a_transport_tuple_as_the_runtime_does(
    transports: tuple[str, ...],
) -> None:
    config = {"REALTIME": {"TRANSPORTS": transports}}
    assert "next.E181" not in _ids(NEXT_FRAMEWORK=config)
    with override_settings(NEXT_FRAMEWORK=config):
        assert active_transports()[0] == transports[0]


@pytest.mark.parametrize(
    "client",
    [
        "max_connections=8",
        {1: "x"},
        {"decode_responses": False},
        {"decode_responses": 1},
    ],
)
def test_check_rejects_an_unusable_redis_client_option(client: object) -> None:
    assert _ids(NEXT_FRAMEWORK=_redis(CLIENT=client)) == {"next.E187"}


@pytest.mark.parametrize(
    "client", [{}, {"max_connections": 8}, {"decode_responses": True}]
)
def test_check_accepts_redis_client_keywords(client: object) -> None:
    assert _ids(NEXT_FRAMEWORK=_redis(CLIENT=client)) == set()


def test_check_defaults_stay_quiet() -> None:
    assert _ids(NEXT_FRAMEWORK={}) == set()


def test_check_reports_a_null_broker_the_manager_refuses() -> None:
    assert _ids(NEXT_FRAMEWORK={"REALTIME": {"BROKER": None}}) == {"next.E177"}
    with (
        override_settings(NEXT_FRAMEWORK={"REALTIME": {"BROKER": None}}),
        pytest.raises(ImproperlyConfigured, match="must be a mapping"),
    ):
        BrokerManager().get()


def test_check_skips_options_of_an_unresolvable_backend() -> None:
    config = {"REALTIME": {"BROKER": {"BACKEND": "x.Y", "OPTIONS": {"ANY": 1}}}}
    assert _ids(NEXT_FRAMEWORK=config) == {"next.E184"}


def _shared_broker_ids(backend: object) -> set[str]:
    config = {"REALTIME": {"BROKER": {"BACKEND": backend}}}
    with override_settings(NEXT_FRAMEWORK=config):
        return {message.id for message in check_shared_broker()}


def test_check_w149_is_a_deployment_check() -> None:
    assert check_shared_broker in check_registry.get_checks(
        include_deployment_checks=True
    )
    assert check_shared_broker not in check_registry.get_checks()


def test_check_w149_warns_the_default_memory_broker() -> None:
    with override_settings(NEXT_FRAMEWORK={}):
        [warning] = check_shared_broker()
    assert warning.id == "next.W149"
    assert "MemoryBroker keeps topics inside one process" in warning.msg
    assert "next.realtime.RedisBroker" in str(warning.hint)
    assert "SILENCED_SYSTEM_CHECKS" in str(warning.hint)


def test_check_w149_warns_a_memory_broker_subclass() -> None:
    backend = "tests.realtime.test_checks.CustomBroker"
    with override_settings(
        NEXT_FRAMEWORK={"REALTIME": {"BROKER": {"BACKEND": backend}}}
    ):
        [warning] = check_shared_broker()
    assert warning.msg.startswith("CustomBroker keeps topics")


@pytest.mark.parametrize(
    "backend", ["next.realtime.RedisBroker", "next.realtime.BrokerBackend", "x.Y"]
)
def test_check_w149_stays_quiet_without_a_memory_broker(backend: str) -> None:
    assert _shared_broker_ids(backend) == set()


def test_check_w149_leaves_a_non_mapping_broker_to_e177() -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"BROKER": None}}):
        assert check_shared_broker() == []


class HeaderTokenView(TicketIntrospectionView):
    def authorize(self, request: HttpRequest) -> bool:
        return request.headers.get("X-Gateway") == "trusted"


def _allow_all(request: HttpRequest) -> bool:
    return True


def _urlconf(*patterns: URLPattern) -> ModuleType:
    module = ModuleType("gateway_urls")
    module.urlpatterns = list(patterns)
    return module


_INCLUDED = _urlconf(path("gateway/", include("next.realtime.urls")))


def _introspection_ids(urlconf: object, token: object = None) -> set[str]:
    config = {"REALTIME": {"GATEWAY_TOKEN": token}}
    with override_settings(ROOT_URLCONF=urlconf, NEXT_FRAMEWORK=config):
        return {message.id for message in check_ticket_introspection()}


@pytest.mark.parametrize("token", [None, "", 42])
def test_check_refuses_a_routed_endpoint_without_a_token(token: object) -> None:
    assert _introspection_ids(_INCLUDED, token) == {"next.E188"}


def test_check_finds_a_directly_routed_endpoint() -> None:
    urlconf = _urlconf(path("ticket/", TicketIntrospectionView.as_view()))
    assert _introspection_ids(urlconf) == {"next.E188"}


def test_check_accepts_a_routed_endpoint_with_a_token() -> None:
    assert _introspection_ids(_INCLUDED, "secret") == set()


@pytest.mark.parametrize(
    "view",
    [HeaderTokenView.as_view(), TicketIntrospectionView.as_view(authorize=_allow_all)],
)
def test_check_trusts_a_swapped_caller_check(view: object) -> None:
    assert _introspection_ids(_urlconf(path("ticket/", view))) == set()


def test_check_ignores_an_unrouted_endpoint() -> None:
    assert _introspection_ids("next.urls") == set()


@pytest.mark.parametrize(
    "urlconf", ["", "tests.realtime.absent_urls", "tests.realtime"]
)
def test_check_leaves_a_broken_urlconf_to_django(urlconf: str) -> None:
    assert _introspection_ids(urlconf) == set()


def test_check_leaves_a_broken_nested_include_to_django() -> None:
    nested = _urlconf(path("inner/", include(ModuleType("hollow_urls"))))
    assert _introspection_ids(_urlconf(path("outer/", include(nested)))) == set()


def test_check_accepts_the_gateway_token_key() -> None:
    config = {"REALTIME": {"GATEWAY_TOKEN": "secret"}}
    with override_settings(NEXT_FRAMEWORK=config):
        assert check_realtime_settings() == []


@pytest.mark.parametrize(
    ("realtime", "ids"),
    [
        (
            {"UPSTREAM": {"RATE": 5, "BURST": 10, "MAX_BYTES": 100, "CONCURRENCY": 64}},
            [],
        ),
        ({"TUNNEL": False}, []),
        ({"TUNNEL": {"CONCURRENCY": 8, "TIMEOUT": 1, "MAX_REPLY_BYTES": 10}}, []),
        ({"UPSTREAM": None}, ["next.E201"]),
        ({"UPSTREAM": {"RATE": True}}, ["next.E202"]),
        ({"UPSTREAM": {"RATE": 1001}}, ["next.E202"]),
        ({"UPSTREAM": {"BURST": 0}}, ["next.E203"]),
        ({"UPSTREAM": {"MAX_BYTES": 65537}}, ["next.E204"]),
        ({"UPSTREAM": {"CONCURRENCY": 0}}, ["next.E209"]),
        ({"UPSTREAM": {"CONCURRENCY": True}}, ["next.E209"]),
        ({"UPSTREAM": {"SIZE": 1}}, ["next.E035"]),
        ({"UPSTREAM": {"TIMEOUT": 0}}, ["next.E210"]),
        ({"UPSTREAM": {"TIMEOUT": 601}}, ["next.E210"]),
        ({"TUNNEL": True}, ["next.E205"]),
        ({"TUNNEL": {"CONCURRENCY": 65}}, ["next.E206"]),
        ({"TUNNEL": {"TIMEOUT": math.nan}}, ["next.E207"]),
        ({"TUNNEL": {"MAX_REPLY_BYTES": "1"}}, ["next.E208"]),
        ({"TUNNEL": {"RATE": 1}}, ["next.E035"]),
    ],
)
def test_check_reports_upstream_and_tunnel_limits(
    realtime: dict[str, object], ids: list[str]
) -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": realtime}):
        messages = check_realtime_settings()
    assert [message.id for message in messages] == ids
