import base64

from django.http import HttpRequest
from django.test import RequestFactory

from next.conf.merge import FROZEN_DEFAULTS
from next.defer import BoundaryTokenError, TokenRejection
from next.defer.tokens import (
    BoundaryClaims,
    BoundaryTarget,
    BoundaryTokenBackend,
    StackedTokenBackend,
    encode_props,
    url_pairs,
)


KEY = "k" * 40
OLD_KEY = "o" * 40
NOW = 1_000_000
COMPONENT = "shop/card"
SCOPE = "pages/shop"
URL_PATH = "/shop/"
URL_KWARGS = {"id": 5}
STORED_BYTES = FROZEN_DEFAULTS["DEFER"]["TOKENS"]["OPTIONS"]["MAX_STORED_BYTES"]

SEAL_PATHS = {
    "signed": "next.defer.SignedSeal",
    "encrypted": "next.defer.EncryptedSeal",
    "none": "next.testing.NoSeal",
}
STORAGE_PATHS = {
    "inline": "next.defer.InlineStorage",
    "cache": "next.defer.CacheStorage",
    "spill": "next.defer.SpillStorage",
}


def stacked(**options: object) -> StackedTokenBackend:
    """Return the shipped stack over `options`, signing with the test `SECRET_KEY`."""
    return StackedTokenBackend({"OPTIONS": options})


def request_for(path: str = URL_PATH) -> HttpRequest:
    return RequestFactory().get(path)


def claims(**props: object) -> BoundaryClaims:
    """Return the claims of the test card, each prop as long as a stored token holds."""
    types = {name: type(value) for name, value in props.items()}
    return BoundaryClaims.of(
        COMPONENT,
        SCOPE,
        props=encode_props(props, types, limit=STORED_BYTES),
        url_path=URL_PATH,
        url_kwargs=URL_KWARGS,
        perms=["shop.view"],
    )


def target(**changes: object) -> BoundaryTarget:
    """Return what the server resolves a redeem of the test card to."""
    fields: dict[str, object] = {
        "component": COMPONENT,
        "scope": SCOPE,
        "url_path": URL_PATH,
        "url_kwargs": url_pairs(URL_KWARGS),
        **changes,
    }
    return BoundaryTarget(**fields)


def b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def split(token: str) -> tuple[str, str, str, str]:
    """Split a token into its version, epoch, profile and sealed text."""
    version, epoch, profile, sealed = token.split(".", 3)
    return version, epoch, profile, sealed


def flip(text: str, index: int) -> str:
    """Return `text` with one base64 character at `index` swapped for another."""
    swapped = "B" if text[index] == "A" else "A"
    return text[:index] + swapped + text[index + 1 :]


def flip_bit(sealed: str, index: int) -> str:
    """Return base64 `sealed` with one bit of its byte at `index` flipped."""
    raw = bytearray(b64decode(sealed))
    raw[index] ^= 1
    return b64encode(bytes(raw))


class MinimalTokenBackend(BoundaryTokenBackend):
    """A backend that issues one fixed token and refuses every token it opens."""

    def issue(self, request, claims, *, tokens=()) -> str:
        return "token"

    def open(self, request, token, *, tokens=()) -> BoundaryClaims:
        raise BoundaryTokenError(TokenRejection.MALFORMED)

    def settle(self, request, claims, target) -> BoundaryClaims:
        return claims


def fail_cache(*args: object, **kwargs: object) -> object:
    """Stand in for a cache call whose server is down."""
    msg = "the cache server is down"
    raise ConnectionError(msg)
