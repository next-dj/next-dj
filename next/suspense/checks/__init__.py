"""System checks for the `DEFER` backends, token seams, pinning sources and streams."""

import ast
import inspect
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

import django
from django.apps import apps
from django.conf import settings
from django.core.cache import DEFAULT_CACHE_ALIAS
from django.core.cache.backends.locmem import LocMemCache
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.core.exceptions import ImproperlyConfigured

from next.checks import NEXT
from next.checks.common import import_backend_class, raw_scope
from next.compression.manager import GZIP_MIDDLEWARE
from next.conf import next_framework_settings
from next.conf.checks import error_for_unusable_path
from next.conf.compression import compression_settings
from next.conf.defer import SCOPE, defer_option
from next.conf.merge import FROZEN_DEFAULTS
from next.defer import MIN_KEY_LENGTH
from next.identity import SubjectBinding
from next.pages.checks.composed import iter_composed_pages
from next.pages.checks.contexts import load_routed_pages
from next.pages.checks.responses import shared_page_paths
from next.pages.loaders import (
    load_page_module,
    load_python_module_memo,
    page_boundaries,
    serves,
)
from next.pages.manager import page
from next.pages.responses import cache_control
from next.partial.checks.nodes import zone_nodes
from next.partial.more import MoreNode
from next.partial.zone import LOADING
from next.static.nonce import CSP_MIDDLEWARE
from next.static.runtime import ScriptInjectionPolicy
from next.suspense.admission import Admission
from next.suspense.checks.calls import (
    check_client_calls,
    check_declared_props,
    check_prop_types,
    deferred_components,
    mints_tokens,
)
from next.suspense.connections import (
    check_connection_sources,
    check_pool_budget,
    check_pool_timeout,
    check_thread_sources,
)
from next.suspense.connections.config import backend_paths
from next.suspense.executors import BoundaryExecutor
from next.suspense.policy import StreamPolicy
from next.suspense.tokens import (
    BoundaryTokenBackend,
    CacheStorage,
    EncryptedSeal,
    NoSeal,
    TokenCodec,
    TokenSeal,
    TokenStorage,
    TokenTransport,
)
from next.suspense.tokens.binding import BINDINGS, NoBinding, binding_problem
from next.suspense.tokens.seal import crypto_library
from next.suspense.transport import StreamTransport
from next.utils import middleware_listed


@dataclass(frozen=True, slots=True)
class _Family:
    """One `DEFER` key that names a backend class and the root the class extends."""

    path: tuple[str, ...]
    root: type


_FAMILIES: Final = (
    _Family(("EXECUTOR", "BACKEND"), BoundaryExecutor),
    _Family(("ADMISSION",), Admission),
    _Family(("POLICY", "BACKEND"), StreamPolicy),
    _Family(("TRANSPORT", "BACKEND"), StreamTransport),
    _Family(("TOKENS", "BACKEND"), BoundaryTokenBackend),
)

_SEAMS: Final = (
    _Family(("TOKENS", "OPTIONS", "CODEC"), TokenCodec),
    _Family(("TOKENS", "OPTIONS", "STORAGE"), TokenStorage),
    _Family(("TOKENS", "OPTIONS", "SEAL"), TokenSeal),
    _Family(("TOKENS", "OPTIONS", "TRANSPORT"), TokenTransport),
)
"""The token seams of the shipped backend, read while no other backend owns them."""

_CALLABLES: Final = (("TOKENS", "OPTIONS", "KEYS"), ("TOKENS", "OPTIONS", "MAX_AGE"))
_BINDING: Final = ("TOKENS", "OPTIONS", "BINDING")
_CALLABLE_HINT: Final = "Name an importable callable."
_BINDING_HINT: Final = (
    'Name "none", "session", "user", a SubjectBinding subclass that takes no '
    "arguments, or a request callable that returns the subject as text or None."
)
_ALIAS: Final = ("TOKENS", "OPTIONS", "CACHE_ALIAS")
_KEYS: Final = ("TOKENS", "OPTIONS", "KEYS")
_SESSIONS_APP: Final = "django.contrib.sessions"
_AUTH_APP: Final = "django.contrib.auth"
_UNMINTED: Final = "no boundary token can be minted"
_FLUSHING_GZIP: Final = (6, 1)
_DJANGO_CSP: Final = CSP_MIDDLEWARE[0]
_NONCE_SOURCE: Final = "<CSP_NONCE_SENTINEL>"
"""The value of `django.utils.csp.CSP.NONCE`, the placeholder the middleware fills."""

_UNSAFE_INLINE: Final = "'unsafe-inline'"
_OVERRIDING: Final = ("'nonce-", "'sha256-", "'sha384-", "'sha512-", "'strict-dynamic'")
_SCRIPT_DIRECTIVES: Final = ("script-src-elem", "script-src", "default-src")
_DURABLE: Final = "durable"
_CACHE_SESSIONS: Final = frozenset(
    {
        "django.contrib.sessions.backends.cache",
        "django.contrib.sessions.backends.cached_db",
    }
)


def _at(scope: Mapping[str, object], path: tuple[str, ...]) -> object:
    """Return the value under `path`, `None` where a level is no mapping."""
    value: object = scope
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _usable(dotted: str, root: type) -> str | None:
    """Return why `dotted` names no concrete subclass of `root`, `None` when it does."""
    try:
        klass = import_backend_class(dotted)
    except ImportError as exc:
        return f"does not import ({exc})"
    if not (isinstance(klass, type) and issubclass(klass, root)):
        return f"is no subclass of {root.__name__}"
    if inspect.isabstract(klass):
        return "is an abstract class"
    return None


def _callable(dotted: str) -> str | None:
    """Return why `dotted` names no callable, `None` when it does."""
    try:
        target: object = import_backend_class(dotted)
    except ImportError as exc:
        return f"does not import ({exc})"
    return None if callable(target) else "names no callable"


def _binding(dotted: str) -> str | None:
    """Return why `dotted` names neither a binding nor a callable, `None` for one."""
    if dotted in BINDINGS:
        return None
    try:
        target: object = import_backend_class(dotted)
    except ImportError as exc:
        return f"does not import ({exc})"
    return binding_problem(target)


def _where(path: tuple[str, ...]) -> str:
    return "NEXT_FRAMEWORK" + "".join(f"[{key!r}]" for key in (SCOPE, *path))


def _family_error(
    scope: Mapping[str, object],
    family: _Family,
    consequence: str = "no deferred job can run",
) -> CheckMessage | None:
    """Build the `next.E219` error for one family, `None` for a usable path."""
    dotted = _at(scope, family.path)
    if not isinstance(dotted, str) or not dotted:
        return None
    problem = _usable(dotted, family.root)
    if problem is None:
        return None
    shipped = _at(FROZEN_DEFAULTS[SCOPE], family.path)
    return error_for_unusable_path(
        _where(family.path),
        dotted,
        problem,
        consequence,
        f"Name a concrete {family.root.__name__} subclass, such as {shipped!r}.",
    )


def _shipped_tokens(scope: Mapping[str, object]) -> bool:
    """Whether the shipped token backend owns the `TOKENS` options."""
    tokens = scope.get("TOKENS")
    backend = tokens.get("BACKEND") if isinstance(tokens, Mapping) else None
    return backend in {None, _at(FROZEN_DEFAULTS[SCOPE], ("TOKENS", "BACKEND"))}


def _option_errors(scope: Mapping[str, object]) -> list[CheckMessage]:
    """Build the `next.E219` errors for the token options that name a path."""
    errors = [_family_error(scope, family, _UNMINTED) for family in _SEAMS]
    for path, probe, hint in (
        *((path, _callable, _CALLABLE_HINT) for path in _CALLABLES),
        (_BINDING, _binding, _BINDING_HINT),
    ):
        dotted = _at(scope, path)
        problem = probe(dotted) if isinstance(dotted, str) and dotted else None
        if problem is not None:
            errors.append(
                error_for_unusable_path(
                    _where(path), str(dotted), problem, _UNMINTED, hint
                )
            )
    return [error for error in errors if error is not None]


@register(NEXT)
def check_defer_backends(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a `DEFER` backend path that names no usable backend (`next.E219`)."""
    scope = raw_scope(SCOPE)
    if scope is None:
        return []
    found = [_family_error(scope, family) for family in _FAMILIES]
    errors = [error for error in found if error is not None]
    if _shipped_tokens(scope):
        errors.extend(_option_errors(scope))
    return errors


def _shipped_backend() -> bool:
    """Whether the merged settings name the shipped token backend."""
    shipped = _at(FROZEN_DEFAULTS[SCOPE], ("TOKENS", "BACKEND"))
    return defer_option("TOKENS", "BACKEND") == shipped


def _token_option(name: str) -> object:
    """Return a merged token option of the shipped backend, `None` for another one."""
    if not _shipped_backend():
        return None
    return defer_option("TOKENS", "OPTIONS", name)


def _token_class(name: str) -> type | None:
    """Return the class a token option names, `None` when it names none."""
    dotted = _token_option(name)
    if not isinstance(dotted, str):
        return None
    if name == "BINDING" and dotted in BINDINGS:
        return BINDINGS[dotted]
    try:
        klass = import_backend_class(dotted)
    except ImportError:
        return None
    return klass if isinstance(klass, type) else None


def _names(name: str, root: type) -> bool:
    klass = _token_class(name)
    return klass is not None and issubclass(klass, root)


def _cache_storage() -> str | None:
    """Return the cache alias the token storage writes to, `None` for none."""
    alias = _token_option("CACHE_ALIAS")
    if not _names("STORAGE", CacheStorage) or not isinstance(alias, str):
        return None
    return alias


def _seal_errors() -> list[CheckMessage]:
    """Report a seal that seals nothing in production or misses its library."""
    errors: list[CheckMessage] = []
    if _names("SEAL", NoSeal) and not settings.DEBUG:
        errors.append(
            Error(
                f"{_where(('TOKENS', 'OPTIONS', 'SEAL'))} names NoSeal while DEBUG is "
                "off, so anyone can forge a boundary token with any props.",
                hint="Use next.suspense.SignedSeal or next.suspense.EncryptedSeal.",
                obj=settings,
                id="next.E221",
            )
        )
    if _names("SEAL", EncryptedSeal) and crypto_library() is None:
        errors.append(
            Error(
                f"{_where(('TOKENS', 'OPTIONS', 'SEAL'))} names EncryptedSeal, but the "
                "cryptography package does not import, so no boundary token can be "
                "minted.",
                hint="Install the next-dj[crypto] extra or use SignedSeal.",
                obj=settings,
                id="next.E230",
            )
        )
    return errors


def _binding_errors() -> list[CheckMessage]:
    """Report a binding that reads the session in a project without the sessions app."""
    klass = _token_class("BINDING")
    if (
        klass is None
        or not issubclass(klass, SubjectBinding)
        or not klass.needs_sessions
        or apps.is_installed(_SESSIONS_APP)
    ):
        return []
    return [
        Error(
            f"{_where(_BINDING)} names {klass.__name__}, which reads the session, but "
            f"{_SESSIONS_APP} is not installed, so no bound boundary token can be "
            "minted.",
            hint="Add django.contrib.sessions to INSTALLED_APPS or bind otherwise.",
            obj=settings,
            id="next.E226",
        )
    ]


def _store(alias: str) -> tuple[object, tuple[str, ...]] | None:
    """Return the backend class and the locations a cache alias stores in."""
    config = settings.CACHES.get(alias)
    if not isinstance(config, Mapping):
        return None
    backend = config.get("BACKEND")
    try:
        resolved = import_backend_class(backend) if isinstance(backend, str) else None
    except ImportError:
        resolved = None
    location: object = config.get("LOCATION", "")
    places: list[object]
    if isinstance(location, str):
        places = list(re.split("[;,]", location))
    elif isinstance(location, list | tuple):
        places = list(location)
    else:
        places = [location]
    return resolved or backend, tuple(sorted(str(place).strip() for place in places))


def _session_cache_errors() -> list[CheckMessage]:
    """Report a token cache that stores where the session engine stores sessions."""
    alias = _cache_storage()
    engine = settings.SESSION_ENGINE
    if alias is None or engine not in _CACHE_SESSIONS:
        return []
    sessions = settings.SESSION_CACHE_ALIAS
    store = _store(alias)
    if alias != sessions and (store is None or store != _store(sessions)):
        return []
    return [
        Error(
            f"{_where(_ALIAS)} is {alias!r}, which stores in the cache the session "
            "engine keeps sessions in, so token entries and sessions evict and expose "
            "each other.",
            hint="Give boundary tokens a cache alias of their own.",
            obj=settings,
            id="next.E234",
        )
    ]


def _shared_cache_warnings() -> list[CheckMessage]:
    """Warn about token claims stored in the default cache nobody chose for them."""
    alias = _cache_storage()
    if alias != DEFAULT_CACHE_ALIAS or _at(raw_scope(SCOPE) or {}, _ALIAS) is not None:
        return []
    return [
        DjangoWarning(
            f"{_where(_ALIAS)} is left at {alias!r} while the token storage keeps "
            "claims in a cache. Django unpickles a cache entry before any MAC is "
            "checked, so whoever can write that cache can run code in every process.",
            hint=(
                "Set CACHE_ALIAS to a cache whose writers are trusted exactly like "
                "those of the session cache."
            ),
            obj=settings,
            id="next.W180",
        )
    ]


def _key_errors() -> list[CheckMessage]:
    """Report a project secret too short to seal the tokens a component may mint."""
    if not _shipped_backend() or _token_option("KEYS") is not None:
        return []
    try:
        keys = (settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS)
    except ImproperlyConfigured:
        return []
    if all(isinstance(key, str) and len(key) >= MIN_KEY_LENGTH for key in keys):
        return []
    if not mints_tokens():
        return []
    return [
        Error(
            f"SECRET_KEY or an entry of SECRET_KEY_FALLBACKS is shorter than "
            f"{MIN_KEY_LENGTH} characters, and with {_where(_KEYS)} unset boundary "
            "tokens are signed and verified with it, so anyone could forge one.",
            hint=(
                "Generate each secret with secrets.token_urlsafe(32), or name a ring "
                "of long keys in KEYS."
            ),
            obj=settings,
            id="next.E231",
        )
    ]


def _shell_lifetimes() -> list[tuple[Path, int]]:
    """Return each page a shared cache may store, with its static lifetime."""
    lifetimes: list[tuple[Path, int]] = []
    for path in shared_page_paths():
        control = cache_control(getattr(load_python_module_memo(path), "cache", None))
        seconds = None if control is None else control.seconds
        if seconds is not None:
            lifetimes.append((path, seconds))
    return lifetimes


def _lifetime_warnings() -> list[CheckMessage]:
    """Warn about a token lifetime a cached shell or an encrypting seal defeats."""
    age, held = _token_option("MAX_AGE"), _token_option("BUCKET")
    bucket = held if isinstance(held, int) else 0
    warnings: list[CheckMessage] = []
    if bucket and _encrypts():
        warnings.append(
            DjangoWarning(
                f"{_where(('TOKENS', 'OPTIONS', 'BUCKET'))} is set while the seal "
                "encrypts under a fresh key, so equal claims never give equal tokens.",
                hint="Drop BUCKET, or use SignedSeal for byte-stable shells.",
                obj=settings,
                id="next.W174",
            )
        )
    if not isinstance(age, int):
        return warnings
    short = [
        str(path) for path, seconds in _shell_lifetimes() if seconds + bucket > age
    ]
    if short:
        warnings.append(
            DjangoWarning(
                f"The boundary token MAX_AGE of {age} seconds is below the shared "
                f"cache lifetime plus BUCKET of {', '.join(short)}, so a cached shell "
                "hands out expired tokens.",
                hint="Raise MAX_AGE to the longest shell lifetime plus BUCKET.",
                obj=settings,
                id="next.W167",
            )
        )
    return warnings


def _encrypts() -> bool:
    klass = _token_class("SEAL")
    return klass is not None and issubclass(klass, TokenSeal) and klass.encrypts


@register(NEXT)
def check_defer_tokens(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report token seams that forge, fail or expire (`E221`, `E226`, `E230`, `E234`).

    It also warns about an unchosen token cache, a short lifetime and a vain bucket.
    """
    return [
        *_seal_errors(),
        *_binding_errors(),
        *_session_cache_errors(),
        *_shared_cache_warnings(),
        *_lifetime_warnings(),
    ]


@register(NEXT)
def check_token_keys(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Refuse a project secret too short to seal boundary tokens with (`next.E231`).

    A declaration only a lazy backend loads at render meets the refusal at mint instead.
    """
    return _key_errors()


def _locmem(alias: str) -> bool:
    """Whether the cache under `alias` keeps its entries inside one process."""
    config = settings.CACHES.get(alias)
    backend = config.get("BACKEND") if isinstance(config, Mapping) else None
    if not isinstance(backend, str):
        return False
    try:
        klass = import_backend_class(backend)
    except ImportError:
        return False
    return isinstance(klass, type) and issubclass(klass, LocMemCache)


@register(NEXT, deploy=True)
def check_token_cache(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Refuse a process-local token cache for a deployment (`next.E223`)."""
    alias = _cache_storage()
    if alias is None or not _locmem(alias):
        return []
    return [
        Error(
            f"{_where(('TOKENS', 'OPTIONS', 'CACHE_ALIAS'))} {alias!r} is a locmem "
            "cache, which keeps token entries inside one process, so with several "
            "workers or hosts a redeem misses the entry another process wrote.",
            hint=(
                "Point CACHE_ALIAS at a shared cache such as Redis, or list next.E223 "
                "in SILENCED_SYSTEM_CHECKS for a single-process deployment."
            ),
            obj=settings,
            id="next.E223",
        )
    ]


@register(NEXT, deploy=True)
def check_token_binding(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn when a project with accounts mints tokens bound to nobody (`next.W186`)."""
    klass = _token_class("BINDING")
    if klass is not NoBinding or not apps.is_installed(_AUTH_APP) or not mints_tokens():
        return []
    return [
        DjangoWarning(
            f"{_where(_BINDING)} is NoBinding in a project with "
            f"{_AUTH_APP} installed, so a boundary token minted for one signed-in "
            "user also opens the boundary for any client that obtains it until it "
            "expires.",
            hint=(
                'Set it to "user" or "session" when deferred content belongs to an '
                "account, or list next.W186 in SILENCED_SYSTEM_CHECKS when every "
                "client-trip boundary shows public content."
            ),
            obj=settings,
            id="next.W186",
        )
    ]


def _runtime_disabled() -> bool:
    """Whether `NEXT_JS_OPTIONS['policy']` names `disabled`, an unknown one not."""
    options = next_framework_settings.NEXT_JS_OPTIONS
    if not isinstance(options, dict):
        return False
    try:
        policy = ScriptInjectionPolicy(
            options.get("policy", ScriptInjectionPolicy.AUTO)
        )
    except ValueError:
        return False
    return policy is ScriptInjectionPolicy.DISABLED


def _template_needs() -> Iterator[str]:
    """Yield what a composed page template declares that a bundle serves."""
    for page_path, template in iter_composed_pages():
        for zone in zone_nodes(template):
            if zone.options.reload == LOADING:
                yield f'the zone "{zone.name}" of {page_path} reloads to its loading UI'
        mores = cast("list[MoreNode]", template.nodelist.get_nodes_by_type(MoreNode))
        for node in mores:
            if node.options.windowed:
                yield f"the {{% more %}} of {page_path} sets prune= or url="


def _first_need() -> str | None:
    """Return the first need a bundle serves, the templates read before components."""
    found = next(_template_needs(), None)
    if found is not None or not mints_tokens():
        return found
    return "a component loads on a later request or declares props"


@register(Tags.templates, NEXT)
def check_bundles_have_a_runtime(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn when the `disabled` script policy drops a bundle a page needs (`next.W181`).

    Each bundle registers with the runtime, so without it no bundle tag renders.
    """
    if not _runtime_disabled():
        return []
    need = _first_need()
    if need is None:
        return []
    return [
        DjangoWarning(
            f"NEXT_JS_OPTIONS['policy'] is 'disabled' while {need}. The browser code "
            "of that markup is a bundle that registers with the runtime, so no page "
            "loads it and the markup stays inert.",
            hint=(
                "Set the policy to 'auto', or to 'manual' with a tag of your own that "
                "emits the runtime before {% collect_scripts %}."
            ),
            obj=settings,
            id="next.W181",
        )
    ]


def _streams() -> bool:
    """Whether the delivery policy may stream, as nothing sets `STREAM` to `False`."""
    return defer_option("POLICY", "OPTIONS", "STREAM") is not False


@register(NEXT)
def check_buffering_gzip(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn that Django's `GZipMiddleware` holds every page stream back (`next.W153`).

    Below Django 6.1 it compresses a stream without flushing, so the policy inlines it.
    """
    if (
        django.VERSION[:2] >= _FLUSHING_GZIP
        or not _streams()
        or compression_settings().enabled
        or not middleware_listed(settings.MIDDLEWARE, GZIP_MIDDLEWARE)
    ):
        return []
    return [
        DjangoWarning(
            f"{GZIP_MIDDLEWARE} is listed while next.dj compression is off, and below "
            "Django 6.1 it compresses a stream without flushing, so every page a "
            "gzip client asks for is assembled inline rather than streamed.",
            hint=(
                "Replace it with next.middleware.CompressionMiddleware and turn "
                "NEXT_FRAMEWORK['COMPRESSION']['ENABLED'] on, or upgrade to Django 6.1."
            ),
            obj=settings,
            id="next.W153",
        )
    ]


def _script_sources(policy: object) -> tuple[str, ...] | None:
    """Return the sources a policy lets script elements run from, `None` for any."""
    if not isinstance(policy, Mapping):
        return None
    for directive in _SCRIPT_DIRECTIVES:
        value = policy.get(directive)
        if isinstance(value, str):
            return tuple(value.split())
        if isinstance(value, list | tuple | set | frozenset):
            return tuple(str(source) for source in value)
    return None


def _inline_allowed(sources: tuple[str, ...]) -> bool:
    """Whether `sources` run an inline script with no nonce, as `'unsafe-inline'` does.

    A nonce, a hash or `'strict-dynamic'` beside it makes browsers ignore it.
    """
    return _UNSAFE_INLINE in sources and not any(
        source == _NONCE_SOURCE or source.startswith(_OVERRIDING) for source in sources
    )


def _refused_scripts() -> str | None:
    """Return why the CSP refuses the inline scripts of a page stream, `None` if not.

    Only the policy of Django's own middleware is readable, django-csp's is not read.
    """
    middleware = settings.MIDDLEWARE
    listed = [path for path in CSP_MIDDLEWARE if middleware_listed(middleware, path)]
    if not listed:
        return None
    sources = None
    if _DJANGO_CSP in listed:
        sources = _script_sources(getattr(settings, "SECURE_CSP", None))
        if sources is None or _inline_allowed(sources):
            return None
    if not next_framework_settings.CSP_NONCE:
        return "NEXT_FRAMEWORK['CSP_NONCE'] is off"
    if sources is not None and _NONCE_SOURCE not in sources:
        return "the script sources of SECURE_CSP hold no CSP.NONCE"
    return None


@register(NEXT)
def check_stream_nonce(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn that the CSP refuses the inline scripts a page stream writes (`next.W160`).

    Each chunk is an inline script, so a policy that refuses it keeps the loading UI.
    """
    if not _streams():
        return []
    reason = _refused_scripts()
    if reason is None:
        return []
    return [
        DjangoWarning(
            "A streamed page writes each deferred boundary as an inline script, and "
            f"a CSP middleware is listed while {reason}, so the browser refuses "
            "those scripts and every boundary keeps its loading UI.",
            hint=(
                "Keep NEXT_FRAMEWORK['CSP_NONCE'] on and put the nonce in the "
                "script-src of the policy, or set DEFER['POLICY']['OPTIONS']['STREAM'] "
                "to False."
            ),
            obj=settings,
            id="next.W160",
        )
    ]


def _source_file(func: object) -> Path | None:
    """Return the file a function was defined in, `None` for one without code."""
    code = getattr(func, "__code__", None)
    filename = getattr(code, "co_filename", None)
    return Path(filename) if isinstance(filename, str) else None


def _deferred_page_sources() -> Iterator[Path | None]:
    """Yield each page with a page boundary and the files of its deferred callables."""
    for _url, page_path in load_routed_pages() or []:
        chain = page_boundaries(page_path)
        if not (chain.deferred and serves(page_path, *load_page_module(page_path))):
            continue
        yield page_path
        plan = page.gating_plan(page_path, chain)
        steps = plan.graph.steps
        for _source, gated in plan.split.jobs:
            for index in sorted(gated):
                yield _source_file(steps[index].func)


def _deferred_sources() -> list[Path]:
    """Return the modules deferred jobs run code from, each once, in a stable order."""
    found = [info.module_path for info in deferred_components()]
    found.extend(_deferred_page_sources())
    return list(dict.fromkeys(path.resolve() for path in found if path is not None))


def _durable(path: Path) -> bool:
    """Whether the source of `path` passes `durable=True`, an unreadable one not."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return False
    return any(
        isinstance(node, ast.keyword)
        and node.arg == _DURABLE
        and isinstance(node.value, ast.Constant)
        and node.value.value is True
        for node in ast.walk(tree)
    )


@register(NEXT)
def check_durable_jobs(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about `durable=True` in a deferred job with connection state (`next.W173`).

    The scan reads the literal keyword only, so a value it cannot see is not found.
    """
    if not backend_paths("CONNECTION_STATE"):
        return []
    return [
        DjangoWarning(
            f"{path} passes durable=True while NEXT_FRAMEWORK['DEFER']"
            "['CONNECTION_STATE'] is set. A ConnectionState replays its state with "
            "transaction-local statements, so a deferred job runs inside a "
            "transaction and a durable atomic block there raises.",
            hint=(
                "Drop durable=True from code a deferred boundary runs, or render "
                "that boundary inline."
            ),
            obj=str(path),
            id="next.W173",
        )
        for path in _deferred_sources()
        if _durable(path)
    ]


register(NEXT)(check_connection_sources)
register(NEXT)(check_thread_sources)
register(NEXT)(check_pool_budget)
register(NEXT)(check_pool_timeout)
register(Tags.templates, NEXT)(check_client_calls)
register(Tags.templates, NEXT)(check_declared_props)
register(Tags.templates, NEXT)(check_prop_types)


__all__ = [
    "check_buffering_gzip",
    "check_bundles_have_a_runtime",
    "check_client_calls",
    "check_connection_sources",
    "check_declared_props",
    "check_defer_backends",
    "check_defer_tokens",
    "check_durable_jobs",
    "check_pool_budget",
    "check_pool_timeout",
    "check_prop_types",
    "check_stream_nonce",
    "check_thread_sources",
    "check_token_binding",
    "check_token_cache",
    "check_token_keys",
]
