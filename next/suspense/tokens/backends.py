"""The boundary token backend contract and the default stack of replaceable seams."""

import functools
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, NoReturn, cast, override

from django.http import HttpRequest

from next.backends import BackendRoot, resolve_backend_class
from next.conf.defer import SCOPE
from next.conf.merge import FROZEN_DEFAULTS
from next.diagnostics import FailureLog
from next.errors import SettingImportError
from next.identity import SubjectBinding
from next.suspense.errors import BoundarySizeError, BoundaryTokenError
from next.suspense.markers import TokenRejection
from next.suspense.signals import boundary_token_rejected

from .claims import VERSION, VERSIONS, BoundaryClaims, Pairs
from .clock import token_clock
from .codec import TokenCodec
from .context import TokenContext, binding_digest
from .keyring import KeyRing
from .options import TokenSettings
from .props import DEFAULT_LIMIT
from .seal import TokenSeal
from .storage import InlineStorage, TokenStorage
from .transport import HeaderTransport, TokenTransport


logger = logging.getLogger("next.suspense")
_failures = FailureLog(logger)

_DIGESTS: Final = "_next_token_digests"
_TOKEN: Final = re.compile(
    r"(?P<version>[1-9][0-9]{0,3})\.(?P<epoch>0|[1-9][0-9]{0,17})\."
    r"(?P<sealed>[A-Za-z0-9_.-]+)",
    re.ASCII,
)


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
    """Mint the token of a client-trip boundary and redeem it on a later request."""

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Keep the `OPTIONS` of the settings entry."""
        options = (config or {}).get("OPTIONS")
        self.options: Mapping[str, object] = (
            options if isinstance(options, Mapping) else {}
        )

    @property
    def transport(self) -> TokenTransport:
        """Return where the client puts a token, the `X-Next-Boundary` header here."""
        return HeaderTransport()

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
    def redeem(
        self, request: HttpRequest, token: str, target: BoundaryTarget
    ) -> BoundaryClaims:
        """Return the verified claims or raise `BoundaryTokenError` with a reason."""

    def validate(self, tokens: Pairs[object]) -> None:
        """Refuse a component's overrides the backend cannot run under, none here."""
        del tokens

    def prop_limit(self, tokens: Pairs[object] = ()) -> int:
        """Return the longest text one prop value carries under `tokens`."""
        del tokens
        return DEFAULT_LIMIT

    def open(
        self, request: HttpRequest, token: str, *, tokens: Pairs[object] = ()
    ) -> BoundaryClaims:
        """Return the claims a token proves under `tokens`, none of them compared yet.

        A reload knows no component before it reads the claims, which `settle` checks.
        """
        msg = f"{type(self).__name__} serves no boundary reload, it has no open"
        raise NotImplementedError(msg)

    def settle(
        self, request: HttpRequest, claims: BoundaryClaims, target: BoundaryTarget
    ) -> BoundaryClaims:
        """Return opened claims once they name `target` and are still alive."""
        msg = f"{type(self).__name__} serves no boundary reload, it has no settle"
        raise NotImplementedError(msg)

    def announce(
        self, request: HttpRequest, error: BoundaryTokenError, component: str = ""
    ) -> None:
        """Announce a refusal `open` raised, `component` empty while none is known."""
        del request, error, component


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


def _refuse(reason: TokenRejection) -> NoReturn:
    raise BoundaryTokenError(reason)


def _digest(binding: SubjectBinding, request: HttpRequest) -> str:
    """Return the digest of the subject `request` proves, refusing a request of none."""
    subject = binding.bound(request)
    if subject is None:
        _refuse(TokenRejection.BINDING)
    return binding_digest(subject)


@dataclass(frozen=True, slots=True)
class _Issued:
    """The digest one binding issued, with the user and the session key it bound."""

    user: object
    session: str | None
    digest: str


def _issued(binding: SubjectBinding, request: HttpRequest) -> str:
    """Return the digest of the subject `binding` issues for `request`, once a subject.

    A login, a logout or a cycled session in the request replaces the subject it binds.
    """
    digests: dict[SubjectBinding, _Issued] | None = getattr(request, _DIGESTS, None)
    if digests is None:
        digests = {}
        setattr(request, _DIGESTS, digests)
    user = getattr(request, "user", None)
    store = getattr(request, "session", None)
    session = None if store is None else store.session_key
    held = digests.get(binding)
    if held is None or held.user is not user or held.session != session:
        digest = binding_digest(binding.issue(request))
        held = digests[binding] = _Issued(user, session, digest)
    return held.digest


class StackedTokenBackend(BoundaryTokenBackend):
    """Compose a codec, a storage, a seal, a binding and a transport from settings.

    A redeem opens the seal before it reads any claim, so a forgery never expires.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Build every seam once, so a misconfigured one fails at the first use."""
        super().__init__(config)
        options = {**_SHIPPED, **self.options}
        self.codec = _seam(options, "CODEC", TokenCodec)
        self.storage = _seam(options, "STORAGE", TokenStorage)
        self.seal = _seam(options, "SEAL", TokenSeal)
        self._transport = _seam(options, "TRANSPORT", TokenTransport)
        self.keyring = KeyRing(options.get("KEYS"))
        self.settings = TokenSettings.read(options)
        self._overridden: dict[Pairs[object], TokenSettings] = {}
        self._rooms: dict[tuple[int, int], int] = {}

    @property
    @override
    def transport(self) -> TokenTransport:
        return self._transport

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
        if isinstance(self.storage, InlineStorage):
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

        `SpillStorage` keeps the claims inline while the whole token fits `MAX_BYTES`.
        """
        settings = self.settings_for(tokens)
        digest = _issued(settings.binding, request)
        now = token_clock().now()
        stamped = settings.stamped(request, claims, now)
        header = f"{VERSION}.{settings.epoch}."
        context = TokenContext(
            version=VERSION,
            epoch=settings.epoch,
            ring=self.keyring.current(),
            digest=digest,
            lifetime=stamped.expires - now,
            max_stored_bytes=settings.max_stored_bytes,
            cache_alias=settings.cache_alias,
        )
        context.room = functools.partial(
            self._room, settings.max_bytes, len(header), context
        )
        reference = self.storage.put(self.codec.dumps(stamped), context)
        token = header + self.seal.seal(reference, context)
        if len(token) > settings.max_bytes:
            msg = (
                f"the boundary token takes {len(token)} bytes, over MAX_BYTES of "
                f"{settings.max_bytes}. SpillStorage or CacheStorage carries more."
            )
            raise BoundarySizeError(msg)
        return token

    @override
    def redeem(
        self, request: HttpRequest, token: str, target: BoundaryTarget
    ) -> BoundaryClaims:
        """Open the token under the target's overrides and settle it, as a reload does.

        Every refusal is announced and logged once, by whichever phase raised it.
        """
        try:
            claims = self.open(request, token, tokens=target.tokens)
        except BoundaryTokenError as exc:
            self._rejected(request, exc, target)
            raise
        return self.settle(request, claims, target)

    @override
    def open(
        self, request: HttpRequest, token: str, *, tokens: Pairs[object] = ()
    ) -> BoundaryClaims:
        """Check size, shape, seal, binding, epoch, version and storage, in that order.

        No ring or user is read before the shape passes, and no refusal is announced.
        """
        settings = self.settings_for(tokens)
        if len(token) > settings.max_bytes:
            _refuse(TokenRejection.TOO_LARGE)
        match = _TOKEN.fullmatch(token)
        if match is None:
            _refuse(TokenRejection.MALFORMED)
        version, epoch = int(match["version"]), int(match["epoch"])
        context = TokenContext(
            version=version,
            epoch=epoch,
            ring=self.keyring.current,
            digest=functools.partial(_digest, settings.binding, request),
            max_stored_bytes=settings.max_stored_bytes,
            cache_alias=settings.cache_alias,
        )
        reference = self.seal.open(match["sealed"], context)
        if epoch != settings.epoch:
            _refuse(TokenRejection.EXPIRED)
        if version not in VERSIONS:
            _refuse(TokenRejection.VERSION)
        return self.codec.loads(self.storage.get(reference, context), version)

    @override
    def settle(
        self, request: HttpRequest, claims: BoundaryClaims, target: BoundaryTarget
    ) -> BoundaryClaims:
        """Compare the claims to the target, then the expiry, announcing a refusal."""
        settings = self.settings_for(target.tokens)
        try:
            if not target.matches(claims):
                _refuse(TokenRejection.URL_MISMATCH)
            if not settings.alive(request, claims, token_clock().now()):
                _refuse(TokenRejection.EXPIRED)
        except BoundaryTokenError as exc:
            self._rejected(request, exc, target)
            raise
        return claims

    @override
    def announce(
        self, request: HttpRequest, error: BoundaryTokenError, component: str = ""
    ) -> None:
        self._rejected(request, error, BoundaryTarget(component, ""))

    def _room(self, max_bytes: int, header: int, context: TokenContext) -> int:
        """Return the most reference bytes whose sealed token fits `max_bytes`.

        The answer is searched once per cap and header width, the seal counting sizes.
        """
        held = self._rooms.get((max_bytes, header))
        if held is None:
            low, high = -1, max_bytes
            while low < high:
                middle = (low + high + 1) // 2
                if header + self.seal.length(middle, context) <= max_bytes:
                    low = middle
                else:
                    high = middle - 1
            held = low
            self._rooms[max_bytes, header] = held
        return held

    def _rejected(
        self, request: HttpRequest, error: BoundaryTokenError, target: BoundaryTarget
    ) -> None:
        """Announce a refusal and log it at a bounded rate, garbage floods neither.

        A receiver runs for every refused request, so it must stay cheap.
        """
        boundary_token_rejected.send(
            sender=type(self),
            reason=error.reason,
            request=request,
            component=target.component,
        )
        _failures.emit(
            logging.INFO if error.expired else logging.WARNING,
            ("boundary-token", error.reason, target.component),
            "A boundary token for %s was refused as %s.",
            (target.component, str(error.reason)),
        )


__all__ = ["BoundaryTarget", "BoundaryTokenBackend", "StackedTokenBackend"]
