import pytest
from django.test import override_settings

from next.realtime.deployment import (
    tunnel_origin_messages,
    websocket_deployment_messages,
)
from next.testing import override_next_settings


_SIGNED = "django.contrib.sessions.backends.signed_cookies"
_DB = "django.contrib.sessions.backends.db"
_SIGNED_BINDING = {"TICKET_BINDING": "next.realtime.SignedCookieBinding"}


def test_websocket_deployment_messages_report_both_session_problems() -> None:
    with override_settings(MIDDLEWARE=[], SESSION_ENGINE=_SIGNED):
        messages = websocket_deployment_messages()
    assert [message.id for message in messages] == ["next.W141", "next.W142"]


def test_websocket_deployment_messages_stay_quiet_for_a_sound_deployment() -> None:
    assert websocket_deployment_messages() == []


@pytest.mark.parametrize(
    ("engine", "expected"), [(_DB, ["next.W147"]), (_SIGNED, []), ("missing", [])]
)
def test_signed_cookie_binding_warns_only_over_server_sessions(
    engine: str, expected: list[str]
) -> None:
    with (
        override_settings(SESSION_ENGINE=engine),
        override_next_settings(REALTIME=_SIGNED_BINDING),
    ):
        messages = websocket_deployment_messages()
    assert [message.id for message in messages] == expected


def test_tunnel_origin_messages_name_every_untrusted_origin() -> None:
    [warning] = tunnel_origin_messages(["https://a.test", "https://b.test"])
    assert warning.id == "next.W146"
    assert "'https://a.test', 'https://b.test'" in warning.msg


def test_tunnel_origin_messages_stay_quiet_without_origins() -> None:
    assert tunnel_origin_messages([]) == []
