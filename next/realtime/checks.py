"""System checks for realtime broker settings and optional dependencies."""

from collections.abc import Callable, Iterator, Mapping, Sequence

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.core.exceptions import ImproperlyConfigured
from django.urls import URLPattern, URLResolver, get_resolver

from next.checks import NEXT
from next.checks.common import errors_for_unknown_keys
from next.conf.defaults import USER_SETTING
from next.conf.scopes import scope_value

from .backends import BrokerBackend, MemoryBroker
from .binding import binding_class
from .deployment import websocket_deployment_messages
from .frames import MAX_INBOUND_BYTES
from .handlers import (
    MAX_NAME_LENGTH,
    MAX_PATTERN_LENGTH,
    Route,
    handler_registry,
    valid_modules,
    valid_name,
)
from .limits import (
    MAX_BURST,
    MAX_CONCURRENCY,
    MAX_RATE,
    MAX_REPLY_BYTES,
    MAX_TIMEOUT,
    TUNNEL_KEYS,
    UPSTREAM_KEYS,
    valid_burst,
    valid_concurrency,
    valid_heartbeat,
    valid_max_bytes,
    valid_rate,
    valid_reply_bytes,
    valid_timeout,
)
from .manager import broker_class
from .replay import (
    MAX_SIZE,
    MAX_TTL,
    REPLAY_KEYS,
    ReplayingBroker,
    replay_window,
    valid_size,
    valid_ttl,
)
from .roster import (
    MAX_MEMBER_BYTES,
    MAX_MEMBERS,
    MAX_WINDOW,
    PRESENCE_KEYS,
    PresenceBroker,
    valid_member_bytes,
    valid_snapshot_size,
    valid_window,
)
from .tickets import MAX_TICKET_AGE, valid_ticket_age, valid_ws_path
from .views import TicketIntrospectionView


_PREFIX = "NEXT_FRAMEWORK['REALTIME']"
_BROKER_KEYS = frozenset({"BACKEND", "OPTIONS"})
_REALTIME_KEYS = frozenset(
    {
        "BROKER",
        "TRANSPORTS",
        "TICKET_MAX_AGE",
        "TICKET_BINDING",
        "WS_PATH",
        "HEARTBEAT",
        "GATEWAY_TOKEN",
        "REPLAY",
        "PRESENCE",
        "HANDLERS",
        "UPSTREAM",
        "TUNNEL",
    }
)


@register(NEXT)
def check_realtime_settings(*args, **kwargs) -> list[CheckMessage]:
    """Report invalid realtime settings, missing dependencies and risky deployments.

    A missing `REALTIME` key still runs the checks against the defaults.
    """
    raw = getattr(settings, USER_SETTING, None)
    realtime = raw.get("REALTIME", {}) if isinstance(raw, dict) else {}
    if realtime is None:
        realtime = {}
    if not isinstance(realtime, Mapping):
        return []
    errors = errors_for_unknown_keys(
        dict(realtime), allowed=_REALTIME_KEYS, prefix=_PREFIX
    )
    broker = realtime.get("BROKER", {})
    if not isinstance(broker, Mapping):
        return [
            *errors,
            Error(
                f"{_PREFIX}['BROKER'] must be a mapping.", obj=settings, id="next.E177"
            ),
        ]
    errors.extend(_transport_errors(realtime))
    if "TICKET_MAX_AGE" in realtime and not valid_ticket_age(
        realtime["TICKET_MAX_AGE"]
    ):
        errors.append(
            Error(
                f"{_PREFIX}['TICKET_MAX_AGE'] must be an integer from 1 to "
                f"{MAX_TICKET_AGE}.",
                obj=settings,
                id="next.E182",
            )
        )
    if "WS_PATH" in realtime and not valid_ws_path(realtime["WS_PATH"]):
        errors.append(
            Error(
                f"{_PREFIX}['WS_PATH'] must be absolute and have no query or fragment.",
                obj=settings,
                id="next.E183",
            )
        )
    errors.extend(_binding_errors(realtime))
    errors.extend(_heartbeat_errors(realtime))
    errors.extend(_replay_errors(realtime))
    errors.extend(_presence_errors(realtime))
    errors.extend(_handler_module_errors(realtime))
    errors.extend(_upstream_errors(realtime))
    errors.extend(_tunnel_errors(realtime))
    errors.extend(_broker_errors(broker))
    errors.extend(_presence_warnings(realtime, broker))
    errors.extend(_deployment_warnings(realtime))
    return errors


def _binding_errors(realtime: Mapping[str, object]) -> list[CheckMessage]:
    """Require the ticket binding to name an importable concrete TicketBinding."""
    if "TICKET_BINDING" not in realtime:
        return []
    if binding_class(realtime["TICKET_BINDING"]) is not None:
        return []
    return [
        Error(
            f"{_PREFIX}['TICKET_BINDING'] must be the dotted path of an importable "
            "concrete TicketBinding subclass.",
            hint="Use 'next.realtime.SessionBinding' or "
            "'next.realtime.SignedCookieBinding'.",
            obj=settings,
            id="next.E211",
        )
    ]


def _heartbeat_errors(realtime: Mapping[str, object]) -> list[CheckMessage]:
    """Require a positive finite WebSocket ping interval in seconds."""
    if "HEARTBEAT" not in realtime or valid_heartbeat(realtime["HEARTBEAT"]):
        return []
    return [
        Error(
            f"{_PREFIX}['HEARTBEAT'] must be a positive finite number of seconds.",
            hint=(
                "It times the WebSocket ping, an SSE stream takes its heartbeat from "
                "the HEARTBEAT_SECONDS option of the partial backend."
            ),
            obj=settings,
            id="next.E185",
        )
    ]


def _replay_errors(realtime: Mapping[str, object]) -> list[CheckMessage]:
    """Require a replay mapping with a SIZE up to MAX_SIZE and a TTL up to a year.

    Only a missing key falls back to the default, an explicit None is not a mapping.
    """
    if "REPLAY" not in realtime:
        return []
    replay = realtime["REPLAY"]
    if not isinstance(replay, Mapping):
        return [
            Error(
                f"{_PREFIX}['REPLAY'] must be a mapping.", obj=settings, id="next.E191"
            )
        ]
    errors = errors_for_unknown_keys(
        dict(replay), allowed=REPLAY_KEYS, prefix=f"{_PREFIX}['REPLAY']"
    )
    if "SIZE" in replay and not valid_size(replay["SIZE"]):
        errors.append(
            Error(
                f"{_PREFIX}['REPLAY']['SIZE'] must be an integer from 0 to {MAX_SIZE}.",
                hint="Use 0 to turn the replay log off.",
                obj=settings,
                id="next.E189",
            )
        )
    if "TTL" in replay and not valid_ttl(replay["TTL"]):
        errors.append(
            Error(
                f"{_PREFIX}['REPLAY']['TTL'] must be a positive number of seconds "
                f"up to {MAX_TTL}, one year.",
                obj=settings,
                id="next.E190",
            )
        )
    return errors


def _presence_errors(realtime: Mapping[str, object]) -> list[CheckMessage]:
    """Require a presence mapping with a bounded window, member count and member size.

    Only a missing key falls back to the default, an explicit None is not a mapping.
    """
    if "PRESENCE" not in realtime:
        return []
    presence = realtime["PRESENCE"]
    prefix = f"{_PREFIX}['PRESENCE']"
    if not isinstance(presence, Mapping):
        return [Error(f"{prefix} must be a mapping.", obj=settings, id="next.E193")]
    errors = errors_for_unknown_keys(
        dict(presence), allowed=PRESENCE_KEYS, prefix=prefix
    )
    if "WINDOW" in presence and not valid_window(presence["WINDOW"]):
        errors.append(
            Error(
                f"{prefix}['WINDOW'] must be a positive number of seconds up to "
                f"{MAX_WINDOW}.",
                obj=settings,
                id="next.E194",
            )
        )
    if "SIZE" in presence and not valid_snapshot_size(presence["SIZE"]):
        errors.append(
            Error(
                f"{prefix}['SIZE'] must be an integer from 0 to {MAX_MEMBERS}.",
                hint="Use 0 to send the count without members.",
                obj=settings,
                id="next.E195",
            )
        )
    if "MEMBER_BYTES" in presence and not valid_member_bytes(presence["MEMBER_BYTES"]):
        errors.append(
            Error(
                f"{prefix}['MEMBER_BYTES'] must be an integer from 1 to "
                f"{MAX_MEMBER_BYTES}.",
                obj=settings,
                id="next.E196",
            )
        )
    return errors


def _handler_module_errors(realtime: Mapping[str, object]) -> list[CheckMessage]:
    """Require `HANDLERS` to be None or a list of dotted module names."""
    modules = realtime.get("HANDLERS")
    if modules is None or valid_modules(modules):
        return []
    return [
        Error(
            f"{_PREFIX}['HANDLERS'] must be None or a list of dotted module names.",
            hint=(
                "Leave it None to import the realtime module of every installed "
                "app, or use [] to import none."
            ),
            obj=settings,
            id="next.E197",
        )
    ]


def _limit_errors(
    section: str,
    values: Mapping[str, object],
    rules: Sequence[tuple[str, Callable[[object], bool], str, str]],
) -> list[CheckMessage]:
    """Report every present key of a limits mapping that its rule refuses."""
    return [
        Error(f"{_PREFIX}['{section}']['{key}'] must be {text}.", obj=settings, id=code)
        for key, valid, text, code in rules
        if key in values and not valid(values[key])
    ]


def _upstream_errors(realtime: Mapping[str, object]) -> list[CheckMessage]:
    """Require an upstream mapping whose five limits all stay in range."""
    if "UPSTREAM" not in realtime:
        return []
    upstream = realtime["UPSTREAM"]
    prefix = f"{_PREFIX}['UPSTREAM']"
    if not isinstance(upstream, Mapping):
        return [Error(f"{prefix} must be a mapping.", obj=settings, id="next.E201")]
    errors = errors_for_unknown_keys(
        dict(upstream), allowed=UPSTREAM_KEYS, prefix=prefix
    )
    errors.extend(
        _limit_errors(
            "UPSTREAM",
            upstream,
            (
                (
                    "RATE",
                    valid_rate,
                    f"a positive number of frames a second up to {MAX_RATE}",
                    "next.E202",
                ),
                (
                    "BURST",
                    valid_burst,
                    f"an integer from 1 to {MAX_BURST}",
                    "next.E203",
                ),
                (
                    "MAX_BYTES",
                    valid_max_bytes,
                    f"an integer from 1 to {MAX_INBOUND_BYTES}",
                    "next.E204",
                ),
                (
                    "CONCURRENCY",
                    valid_concurrency,
                    f"an integer from 1 to {MAX_CONCURRENCY}",
                    "next.E209",
                ),
                (
                    "TIMEOUT",
                    valid_timeout,
                    f"a positive number of seconds up to {MAX_TIMEOUT}",
                    "next.E210",
                ),
            ),
        )
    )
    return errors


def _tunnel_errors(realtime: Mapping[str, object]) -> list[CheckMessage]:
    """Require the tunnel to be False or a mapping of bounded limits."""
    tunnel = realtime.get("TUNNEL")
    if "TUNNEL" not in realtime or tunnel is False:
        return []
    prefix = f"{_PREFIX}['TUNNEL']"
    if not isinstance(tunnel, Mapping):
        return [
            Error(f"{prefix} must be False or a mapping.", obj=settings, id="next.E205")
        ]
    errors = errors_for_unknown_keys(dict(tunnel), allowed=TUNNEL_KEYS, prefix=prefix)
    errors.extend(
        _limit_errors(
            "TUNNEL",
            tunnel,
            (
                (
                    "CONCURRENCY",
                    valid_concurrency,
                    f"an integer from 1 to {MAX_CONCURRENCY}",
                    "next.E206",
                ),
                (
                    "TIMEOUT",
                    valid_timeout,
                    f"a positive number of seconds up to {MAX_TIMEOUT}",
                    "next.E207",
                ),
                (
                    "MAX_REPLY_BYTES",
                    valid_reply_bytes,
                    f"an integer from 1 to {MAX_REPLY_BYTES}",
                    "next.E208",
                ),
            ),
        )
    )
    return errors


def _presence_warnings(
    realtime: Mapping[str, object], broker: Mapping[str, object]
) -> list[CheckMessage]:
    """Flag presence settings the configured broker cannot serve.

    Presence is opt-in per stream, so only an explicit `PRESENCE` key signals intent.
    """
    if "PRESENCE" not in realtime:
        return []
    backend_class = _broker_class(broker)
    if backend_class is None or issubclass(backend_class, PresenceBroker):
        return []
    return [
        DjangoWarning(
            f"{_PREFIX}['PRESENCE'] is set, but {backend_class.__qualname__} keeps "
            "no presence, so every stream that asks for it fails to start.",
            hint=(
                "Implement next.realtime.PresenceBroker on the broker, or drop "
                "PRESENCE and presence= from the streams."
            ),
            obj=settings,
            id="next.W144",
        )
    ]


def _transport_errors(realtime: Mapping[str, object]) -> list[CheckMessage]:
    """Validate the explicit transport list or tuple the runtime reads."""
    transports = realtime.get("TRANSPORTS")
    if transports is None:
        return []
    if (
        not isinstance(transports, list | tuple)
        or not transports
        or any(
            not isinstance(item, str) or item not in {"sse", "ws"}
            for item in transports
        )
        or len(set(transports)) != len(transports)
    ):
        return [
            Error(
                f"{_PREFIX}['TRANSPORTS'] must be a unique non-empty list or tuple of "
                "only 'sse' and 'ws'.",
                obj=settings,
                id="next.E181",
            )
        ]
    return []


def _deployment_warnings(realtime: Mapping[str, object]) -> list[CheckMessage]:
    """Flag an explicit WebSocket transport on a deployment without server sessions."""
    transports = realtime.get("TRANSPORTS")
    if not isinstance(transports, list | tuple) or "ws" not in transports:
        return []
    return websocket_deployment_messages()


def _broker_errors(broker: Mapping[str, object]) -> list[CheckMessage]:
    """Validate the selected backend and its options."""
    errors: list[CheckMessage] = []
    errors.extend(
        errors_for_unknown_keys(
            dict(broker), allowed=_BROKER_KEYS, prefix=f"{_PREFIX}['BROKER']"
        )
    )
    backend_class = _broker_class(broker)
    options = broker.get("OPTIONS")
    if options is not None and not isinstance(options, Mapping):
        errors.append(
            Error(
                f"{_PREFIX}['BROKER']['OPTIONS'] must be a mapping.",
                obj=settings,
                id="next.E180",
            )
        )
    if backend_class is not None:
        checked = options if isinstance(options, Mapping) else {}
        errors.extend(backend_class.check_options(checked))
    errors.extend(_replay_warnings(backend_class))
    if backend_class is None:
        errors.append(
            Error(
                f"{_PREFIX}['BROKER']['BACKEND'] must name an importable "
                "concrete BrokerBackend subclass.",
                obj=settings,
                id="next.E184",
            )
        )
    return errors


def _replay_warnings(backend_class: type[BrokerBackend] | None) -> list[CheckMessage]:
    """Flag a replay log the configured broker cannot serve, so resumes resync."""
    if backend_class is None or issubclass(backend_class, ReplayingBroker):
        return []
    try:
        size, _ = replay_window()
    except ImproperlyConfigured:
        return []
    if size == 0:
        return []
    return [
        DjangoWarning(
            f"{_PREFIX}['REPLAY']['SIZE'] is {size}, but "
            f"{backend_class.__qualname__} keeps no replay log, so every reconnect "
            "resyncs from hello.",
            hint=(
                "Implement replay() and areplay() from "
                "next.realtime.replay.ReplayingBroker on the broker, or set SIZE "
                "to 0."
            ),
            obj=settings,
            id="next.W143",
        )
    ]


def _broker_class(broker: Mapping[str, object]) -> type[BrokerBackend] | None:
    """Resolve the class the broker manager would build, None when it cannot."""
    try:
        return broker_class(broker)
    except ImproperlyConfigured:
        return None


@register(NEXT, deploy=True)
def check_shared_broker(*args, **kwargs) -> list[CheckMessage]:
    """Warn when the broker keeps topics inside one process (`next.W149`)."""
    broker = scope_value("REALTIME", "BROKER")
    if not isinstance(broker, Mapping):
        return []
    backend_class = _broker_class(broker)
    if backend_class is None or not issubclass(backend_class, MemoryBroker):
        return []
    return [
        DjangoWarning(
            f"{backend_class.__qualname__} keeps topics inside one process, so with "
            "several workers or hosts a publish reaches only the subscribers of the "
            "publishing process.",
            hint=(
                "Select next.realtime.RedisBroker or another shared BrokerBackend, "
                "or list next.W149 in SILENCED_SYSTEM_CHECKS for a single-process "
                "deployment."
            ),
            obj=settings,
            id="next.W149",
        )
    ]


@register(NEXT)
def check_upstream_handlers(*args, **kwargs) -> list[CheckMessage]:
    """Report handlers that can never match and pairs two handlers claim."""
    messages: list[CheckMessage] = [
        Error(_invalid_route(entry), obj=_handler_label(entry), id="next.E198")
        for entry in handler_registry
        if not entry.valid
    ]
    messages.extend(
        Error(
            f"The upstream event {other.name!r} on {other.pattern!r} is registered "
            f"by {_handler_label(held)} and {_handler_label(other)}, and only the "
            "first one runs.",
            hint="Rename the event or narrow the topic pattern of one of them.",
            obj=_handler_label(other),
            id="next.E199",
        )
        for held, other in handler_registry.duplicates()
    )
    return messages


def _invalid_route(entry: Route) -> str:
    """Describe why a route can never match."""
    if not valid_name(entry.name):
        return (
            f"The upstream event name {entry.name!r} of {_handler_label(entry)} is "
            f"not 1 to {MAX_NAME_LENGTH} ASCII letters, digits, underscores, dots, "
            "colons or hyphens."
        )
    return (
        f"The topic pattern {entry.pattern!r} of {_handler_label(entry)} is not 1 to "
        f"{MAX_PATTERN_LENGTH} topic characters with distinct {{name}} placeholders "
        "outside the next: prefix, with a colon or a dot between two placeholders."
    )


def _handler_label(entry: Route) -> str:
    """Return the dotted name of a route's handler."""
    handler = entry.handler
    module = getattr(handler, "__module__", None) or "?"
    name = getattr(handler, "__qualname__", None) or repr(handler)
    return f"{module}.{name}"


@register(Tags.urls, NEXT)
def check_ticket_introspection(*args, **kwargs) -> list[CheckMessage]:
    """Refuse a routed ticket introspection endpoint that no bearer token guards."""
    token = scope_value("REALTIME", "GATEWAY_TOKEN")
    if isinstance(token, str) and token:
        return []
    if not getattr(settings, "ROOT_URLCONF", None):
        return []
    try:
        views = list(_routed_views(get_resolver().url_patterns))
    except (ImportError, ImproperlyConfigured):
        return []
    if not any(_default_access(view) for view in views):
        return []
    return [
        Error(
            "The ticket introspection endpoint is routed, but "
            f"{_PREFIX}['GATEWAY_TOKEN'] is not a non-empty string, so it refuses "
            "every gateway.",
            hint=(
                "Set GATEWAY_TOKEN to a long random secret, or override "
                "TicketIntrospectionView.authorize in a subclass."
            ),
            obj=settings,
            id="next.E188",
        )
    ]


def _routed_views(
    patterns: Sequence[URLResolver | URLPattern],
) -> Iterator[Callable[..., object]]:
    """Yield the view of every pattern the URLconf routes, through each include."""
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _routed_views(pattern.url_patterns)
        else:
            yield pattern.callback


def _default_access(view: Callable[..., object]) -> bool:
    """Whether a view is the introspection endpoint behind the stock bearer check."""
    view_class = getattr(view, "view_class", None)
    return (
        isinstance(view_class, type)
        and issubclass(view_class, TicketIntrospectionView)
        and view_class.authorize is TicketIntrospectionView.authorize
        and "authorize" not in getattr(view, "view_initkwargs", {})
    )


__all__ = [
    "check_realtime_settings",
    "check_shared_broker",
    "check_ticket_introspection",
    "check_upstream_handlers",
]
