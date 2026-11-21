import base64

from django.http import HttpRequest
from django.test import RequestFactory

from next.conf.merge import FROZEN_DEFAULTS
from next.suspense.tokens import (
    BoundaryClaims,
    BoundaryTarget,
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
    "signed": "next.suspense.SignedSeal",
    "encrypted": "next.suspense.EncryptedSeal",
    "none": "next.suspense.NoSeal",
}
STORAGE_PATHS = {
    "inline": "next.suspense.InlineStorage",
    "cache": "next.suspense.CacheStorage",
    "spill": "next.suspense.SpillStorage",
}
CODEC_PATHS = {
    "json": "next.suspense.JsonCodec",
    "msgpack": "next.suspense.MsgpackCodec",
}


def stacked(**options: object) -> StackedTokenBackend:
    """Return the shipped stack over the test key ring and `options`."""
    return StackedTokenBackend({"OPTIONS": {"KEYS": [KEY], **options}})


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


def split(token: str) -> tuple[str, str, str]:
    """Split a token into its version, epoch and sealed text."""
    version, epoch, sealed = token.split(".", 2)
    return version, epoch, sealed


def flip(text: str, index: int) -> str:
    """Return `text` with one base64 character at `index` swapped for another."""
    swapped = "B" if text[index] == "A" else "A"
    return text[:index] + swapped + text[index + 1 :]


def flip_bit(sealed: str, index: int) -> str:
    """Return base64 `sealed` with one bit of its byte at `index` flipped."""
    raw = bytearray(b64decode(sealed))
    raw[index] ^= 1
    return b64encode(bytes(raw))
