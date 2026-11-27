"""The boundary token backend contract and the default stack of replaceable seams."""

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, NoReturn, cast, override

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest

from next.backends import BackendRoot, resolve_backend_class
from next.conf.defer import SCOPE
from next.conf.merge import FROZEN_DEFAULTS
from next.defer.errors import BoundarySizeError, BoundaryTokenError
from next.defer.markers import TokenRejection
from next.defer.signals import boundary_token_rejected
from next.defer.state import state_of
from next.diagnostics import FailureLog
from next.errors import SettingImportError
from next.identity import SubjectBinding

from . import codec
from .carrier import CARRIERS, TokenCarrier
from .claims import VERSION, VERSIONS, BoundaryClaims, Pairs
from .clock import token_clock
from .props import DEFAULT_LIMIT
from .seal import TokenSeal, token_salt
from .settings import PROJECT_PROFILE, TokenSettings
from .storage import StorageTerms, TokenStorage


logger = logging.getLogger("next.defer")
_failures = FailureLog(logger)
_suspicious = FailureLog(
    logging.getLogger(f"django.security.{BoundaryTokenError.__name__}")
)

_OPTIONS: Final = "NEXT_FRAMEWORK['DEFER']['TOKENS']['OPTIONS']"
_REFUSED: Final = "A boundary token for %s was refused as %s."
_HEADER: Final = re.compile(
    r"(?P<version>[1-9][0-9]{0,3})\.(?P<epoch>0|[1-9][0-9]{0,17})\."
    r"(?P<profile>0|[0-9a-f]{12})\.",
    re.ASCII,
)
_SEALED: Final = re.compile(r"[A-Za-z0-9_.-]+", re.ASCII)


@dataclass(frozen=True, slots=True)
class BoundaryTarget:
    """What the server resolved a redeem request to, read before it trusts the token.

    `tokens` holds the overrides of the resolved component, never any the token names.
    """

    component: str
    scope: str
    url_path: str = "/"
    url_kwargs: Pairs[str] = ()
    tokens: Pairs[object] = ()

    def matches(self, claims: BoundaryClaims) -> bool:
        """Whether the claims name this component, scope and URL."""
        return (
            claims.component == self.component
            and claims.scope == self.scope
            and claims.url_path == self.url_path
            and claims.url_kwargs == self.url_kwargs
        )


class BoundaryTokenBackend(ABC):
    """Mint the token of a client-trip boundary and open it on a later reload.

    A reload opens a token before it knows the component, then settles the claims.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Keep the `OPTIONS` of the settings entry."""
        options = (config or {}).get("OPTIONS")
        self.options: Mapping[str, object] = (
            options if isinstance(options, Mapping) else {}
        )

    @property
    def carrier(self) -> TokenCarrier:
        """Return where the client puts a token, the `X-Next-Boundary` header here."""
        return CARRIERS["header"]

    @abstractmethod
    def issue(
        self,
        request: HttpRequest,
        claims: BoundaryClaims,
        *,
        tokens: Pairs[object] = (),
    ) -> str:
        """Return the token of a client-trip boundary whose access rule passed."""

    @abstractmethod
    def open(
        self, request: HttpRequest, token: str, *, tokens: Pairs[object] = ()
    ) -> BoundaryClaims:
        """Return the claims a token proves under `tokens`, none compared yet."""

    @abstractmethod
    def settle(
        self, request: HttpRequest, claims: BoundaryClaims, target: BoundaryTarget
    ) -> BoundaryClaims:
        """Return opened claims once they name `target` and are still alive."""

    def validate(self, tokens: Pairs[object]) -> None:
        """Refuse a component's overrides the backend cannot run under, none here."""
        del tokens

    def prop_limit(self, tokens: Pairs[object] = ()) -> int:
        """Return the longest text one prop value carries under `tokens`."""
        del tokens
        return DEFAULT_LIMIT

    def bound(self, request: HttpRequest, tokens: Pairs[object] = ()) -> bool:
        """Whether the token of `request` ties it to its visitor, a private render."""
        del request, tokens
        return True

    def profile(self, token: str) -> str | None:
        """Return the overrides profile `token` names in clear, `None` for no name.

        A reload opens a named token under the overrides of that name alone.
        """
        del token
        return None

    def redeem(
        self, request: HttpRequest, token: str, target: BoundaryTarget
    ) -> BoundaryClaims:
        """Open the token under the target's overrides and settle it, as a reload does.

        A refusal of either phase is announced once before it is raised.
        """
        try:
            claims = self.open(request, token, tokens=target.tokens)
            return self.settle(request, claims, target)
        except BoundaryTokenError as exc:
            self.announce(request, exc, target.component)
            raise

    def announce(
        self, request: HttpRequest, error: BoundaryTokenError, component: str = ""
    ) -> None:
        """Send `boundary_token_rejected` and log the refusal at a bounded rate.

        A receiver that raises is logged by Django and never turns the 403 into a 500.
        """
        boundary_token_rejected.send_robust(
            sender=type(self), reason=error.reason, request=request, component=component
        )
        key = ("boundary_token", error.reason, component)
        args = (component or "an unresolved component", error.detail)
        if error.reason is TokenRejection.STORAGE_DOWN:
            _failures.emit(logging.ERROR, key, _REFUSED, args, exc_info=error)
        elif error.expired:
            _failures.emit(logging.INFO, key, _REFUSED, args)
        else:
            _failures.emit(logging.WARNING, key, _REFUSED, args)
            _suspicious.emit(logging.WARNING, key, _REFUSED, args)


_SHIPPED: Final = cast(
    "Mapping[str, object]", FROZEN_DEFAULTS[SCOPE]["TOKENS"]["OPTIONS"]
)


def _seam[T](options: Mapping[str, object], key: str, root: BackendRoot[T]) -> T:
    """Build the seam a dotted path names, refusing a class outside its root."""
    dotted = options.get(key)
    try:
        klass = resolve_backend_class({"BACKEND": dotted}, base=root)
    except ImportError as exc:
        raise SettingImportError(key, str(dotted), exc, scope=SCOPE) from exc
    return klass()


def _carrier(name: object) -> TokenCarrier:
    """Return the carrier `CARRIER` names, refusing a place the client lacks."""
    if not isinstance(name, str) or name not in CARRIERS:
        places = ", ".join(map(repr, CARRIERS))
        msg = f"{_OPTIONS}['CARRIER'] must be one of {places}, got {name!r}."
        raise ImproperlyConfigured(msg)
    return CARRIERS[name]


def _refuse(reason: TokenRejection) -> NoReturn:
    raise BoundaryTokenError(reason)


def _proven(binding: SubjectBinding, request: HttpRequest) -> str:
    """Return the subject `request` proves, refusing a request that proves none."""
    subject = binding.bound(request)
    if subject is None:
        _refuse(TokenRejection.BINDING)
    return subject


@dataclass(frozen=True, slots=True)
class IssuedSubject:
    """The subject one binding issued, with the user and session key it was read at."""

    user: object
    session: str | None
    subject: str


def _issued(binding: SubjectBinding, request: HttpRequest) -> IssuedSubject:
    """Return the subject `binding` issues for `request`, read once per subject.

    A login, a logout or a cycled session in the request replaces the subject it binds.
    """
    issued = state_of(request).issued
    user = getattr(request, "user", None)
    store = getattr(request, "session", None)
    session = None if store is None else store.session_key
    held = issued.get(binding)
    if held is None or held.user is not user or held.session != session:
        held = IssuedSubject(user, session, binding.issue(request))
        issued[binding] = held
    return held


class StackedTokenBackend(BoundaryTokenBackend):
    """Compose a storage, a seal, a binding and a carrier from settings.

    A token is `version.epoch.profile.sealed`, its seal salted with header and subject.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Build every seam once, so a misconfigured one fails at the first use."""
        super().__init__(config)
        options = {**_SHIPPED, **self.options}
        self.storage = _seam(options, "STORAGE", TokenStorage)
        self.seal = _seam(options, "SEAL", TokenSeal)
        self._carrier = _carrier(options.get("CARRIER"))
        self.settings = TokenSettings.read(options)
        self._overridden: dict[Pairs[object], TokenSettings] = {}

    @property
    @override
    def carrier(self) -> TokenCarrier:
        return self._carrier

    def settings_for(self, tokens: Pairs[object]) -> TokenSettings:
        """Return the settings with one component's overrides, built once per set."""
        held = self._overridden.get(tokens)
        if held is None:
            held = self.settings.overridden(tokens)
            self._overridden[tokens] = held
        return held

    @override
    def validate(self, tokens: Pairs[object]) -> None:
        self.settings_for(tokens)

    @override
    def prop_limit(self, tokens: Pairs[object] = ()) -> int:
        """Return `MAX_BYTES` for claims kept inline, `MAX_STORED_BYTES` once stored."""
        settings = self.settings_for(tokens)
        if self.storage.inline:
            return settings.max_bytes
        return settings.max_stored_bytes

    @override
    def issue(
        self,
        request: HttpRequest,
        claims: BoundaryClaims,
        *,
        tokens: Pairs[object] = (),
    ) -> str:
        """Return the token of the stamped claims, refusing one over `MAX_BYTES`.

        A token over `MAX_BYTES` is sealed again over what the storage overflows to.
        """
        settings = self.settings_for(tokens)
        subject = _issued(settings.binding, request).subject
        now = token_clock().now()
        stamped = settings.stamped(request, claims, now)
        header = f"{VERSION}.{settings.epoch}.{settings.profile}."
        terms = StorageTerms(
            max_stored_bytes=settings.max_stored_bytes,
            cache_alias=settings.cache_alias,
            lifetime=stamped.expires - now,
        )
        data = codec.dumps(stamped)
        salt = token_salt(VERSION, settings.epoch, settings.profile, subject)
        token = header + self.seal.seal(self.storage.put(data, terms), salt)
        if len(token) > settings.max_bytes:
            spilled = self.storage.overflow(data, terms)
            if spilled is not None:
                token = header + self.seal.seal(spilled, salt)
        if len(token) > settings.max_bytes:
            msg = (
                f"the boundary token takes {len(token)} bytes, over MAX_BYTES of "
                f"{settings.max_bytes}. SpillStorage or CacheStorage carries more."
            )
            raise BoundarySizeError(msg)
        return token

    @override
    def bound(self, request: HttpRequest, tokens: Pairs[object] = ()) -> bool:
        """Whether the binding tied the token to a subject no other visitor proves."""
        binding = self.settings_for(tokens).binding
        return binding.binds and _issued(binding, request).subject not in binding.shared

    @override
    def profile(self, token: str) -> str:
        """Return the profile the header names, the project's for a token of no header.

        Only the bounded header is read, and the seal later proves what it names.
        """
        match = _HEADER.match(token)
        return PROJECT_PROFILE if match is None else match["profile"]

    @override
    def open(
        self, request: HttpRequest, token: str, *, tokens: Pairs[object] = ()
    ) -> BoundaryClaims:
        """Check size, shape, binding, seal, profile, epoch, version and storage.

        No key or user is read before the size and the shape pass.
        """
        settings = self.settings_for(tokens)
        if len(token) > settings.max_bytes:
            _refuse(TokenRejection.TOO_LARGE)
        match = _HEADER.match(token)
        sealed = "" if match is None else token[match.end() :]
        if match is None or not _SEALED.fullmatch(sealed):
            _refuse(TokenRejection.MALFORMED)
        version, epoch, profile = (
            int(match["version"]),
            int(match["epoch"]),
            match["profile"],
        )
        subject = _proven(settings.binding, request)
        terms = StorageTerms(
            max_stored_bytes=settings.max_stored_bytes, cache_alias=settings.cache_alias
        )
        reference = self.seal.open(sealed, token_salt(version, epoch, profile, subject))
        if profile != settings.profile:
            _refuse(TokenRejection.URL_MISMATCH)
        if epoch != settings.epoch:
            _refuse(TokenRejection.EXPIRED)
        if version not in VERSIONS:
            _refuse(TokenRejection.VERSION)
        return codec.loads(self.storage.get(reference, terms), version)

    @override
    def settle(
        self, request: HttpRequest, claims: BoundaryClaims, target: BoundaryTarget
    ) -> BoundaryClaims:
        """Compare the claims to the target, then the expiry."""
        if not target.matches(claims):
            _refuse(TokenRejection.URL_MISMATCH)
        if not self.settings_for(target.tokens).alive(
            request, claims, token_clock().now()
        ):
            _refuse(TokenRejection.EXPIRED)
        return claims


__all__ = ["BoundaryTarget", "BoundaryTokenBackend", "StackedTokenBackend"]
