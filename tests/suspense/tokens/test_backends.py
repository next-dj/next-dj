import itertools
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.cache import SessionStore
from django.core import signing
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest

import next.realtime
from next.suspense import (
    BoundaryPropsError,
    BoundarySizeError,
    BoundaryTokenError,
    TokenRejection,
    boundary_token_rejected,
)
from next.suspense.tokens import (
    VERSION,
    VERSIONS,
    BoundaryClaims,
    BoundaryTarget,
    BoundaryTokenBackend,
    FrozenClock,
    HeaderTransport,
    JsonCodec,
    QueryTransport,
    SignedSeal,
    StackedTokenBackend,
)
from next.suspense.tokens.context import PURPOSE, TokenContext, purpose
from next.suspense.tokens.props import DEFAULT_LIMIT
from next.suspense.tokens.seal import ENCRYPTION_INFO, KID_SALT
from next.suspense.tokens.storage import STORE_SALT, cache_key
from next.testing import capture_signals, reset_failure_logs
from tests.support.crypto import install_crypto, install_msgpack
from tests.support.tokens import (
    CODEC_PATHS,
    KEY,
    NOW,
    OLD_KEY,
    SEAL_PATHS,
    STORAGE_PATHS,
    b64decode,
    b64encode,
    claims,
    flip,
    request_for,
    split,
    stacked,
    target,
)


_AGES: list[object] = []
_BUCKET_START = 999_960


def age_of(request, claims) -> object:
    return _AGES[-1]


def tenant(request) -> str | None:
    return request.headers.get("X-Tenant")


_ASKED: list[HttpRequest] = []


def counted(request) -> str:
    _ASKED.append(request)
    return "acme"


def shaped_age(request, claims) -> int:
    return 3600 if request.headers.get("X-Long") else 60


def _reason(backend, token: str, request: HttpRequest | None = None, **changes):
    with pytest.raises(BoundaryTokenError) as caught:
        backend.redeem(request or request_for(), token, target(**changes))
    return caught.value.reason


def _forged(version: int, epoch: int, data: bytes, keys=(KEY,), binding="") -> str:
    context = TokenContext(version=version, epoch=epoch, ring=keys, digest=binding)
    return f"{version}.{epoch}.{SignedSeal().seal(b'i' + data, context)}"


def _payload(**changes: object) -> bytes:
    fields: dict[str, object] = {
        "component": "shop/card",
        "scope": "pages/shop",
        "url_path": "/shop/",
        "url_kwargs": (("id", "5"),),
        "perms": ("shop.view",),
        "issued": NOW,
        "expires": NOW + 600,
        **changes,
    }
    return JsonCodec().dumps(BoundaryClaims(**fields))


@pytest.mark.usefixtures("clock")
class TestRoundTrip:
    """Every combination of the shipped seams mints a token that redeems."""

    @pytest.mark.parametrize(
        ("seal", "storage", "codec"),
        list(itertools.product(SEAL_PATHS, STORAGE_PATHS, CODEC_PATHS)),
    )
    def test_every_seam_combination_round_trips(
        self, monkeypatch: pytest.MonkeyPatch, seal: str, storage: str, codec: str
    ) -> None:
        install_crypto(monkeypatch)
        install_msgpack(monkeypatch)
        backend = stacked(
            SEAL=SEAL_PATHS[seal],
            STORAGE=STORAGE_PATHS[storage],
            CODEC=CODEC_PATHS[codec],
        )
        token = backend.issue(request_for(), claims(sku="A1", count=3))
        redeemed = backend.redeem(request_for(), token, target())
        assert redeemed.props == (("count", 3), ("sku", "A1"))
        assert redeemed.perms == ("shop.view",)
        assert (redeemed.issued, redeemed.expires) == (NOW, NOW + 600)

    def test_the_token_reads_version_epoch_and_a_url_safe_seal(self) -> None:
        token = stacked(EPOCH=7).issue(request_for(), claims())
        assert re.fullmatch(r"2\.7\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}", token)

    def test_the_signed_body_is_the_tagged_claims(self) -> None:
        version, epoch, sealed = split(stacked().issue(request_for(), claims()))
        body = b64decode(sealed.split(".")[0])
        assert body.startswith(b"i{")
        assert (version, epoch) == (str(VERSION), "1")

    def test_the_minted_expiry_is_sealed_in_the_claims(self) -> None:
        token = stacked(MAX_AGE=90).issue(request_for(), claims())
        assert b'"e":1000090' in b64decode(split(token)[2].split(".")[0])

    def test_the_cache_storage_token_hides_the_props(self) -> None:
        token = stacked(STORAGE=STORAGE_PATHS["cache"]).issue(
            request_for(), claims(sku="SECRET")
        )
        assert "SECRET" not in token
        assert b"SECRET" not in b64decode(split(token)[2].split(".")[0])


@pytest.mark.usefixtures("clock")
class TestTampering:
    """Any change to a token, its salt or its keys is refused before claims are read."""

    def test_every_character_of_body_and_mac_is_covered(self) -> None:
        backend = stacked()
        token = backend.issue(request_for(), claims(sku="A1"))
        last = token.rindex(".") - 1
        for index in range(len("2.1."), len(token)):
            if token[index] == ".":
                continue
            refused = {TokenRejection.SIGNATURE}
            if index == last:
                refused.add(TokenRejection.MALFORMED)
            assert _reason(backend, flip(token, index)) in refused, index

    @pytest.mark.parametrize(
        "mangle",
        [
            lambda token: token[:-1],
            lambda token: token[:20],
            lambda token: token + token,
            lambda token: token + "." + token,
            lambda token: "x" + token,
            lambda token: token.replace(".", "-", 1),
            lambda token: "01" + token[1:],
            lambda token: "0" + token[1:],
            lambda token: token[:2] + "0" + token[2:],
            lambda token: token[:2] + "+" + token[2:],
            lambda token: token[:2] + "-1" + token[3:],
            lambda token: token + "\n",
            lambda _token: "",
            lambda _token: "1.1.",
        ],
    )
    def test_truncation_and_concatenation_are_refused(self, mangle) -> None:
        backend = stacked()
        token = backend.issue(request_for(), claims())
        assert _reason(backend, mangle(token)) in {
            TokenRejection.MALFORMED,
            TokenRejection.SIGNATURE,
        }

    def test_a_token_signed_under_a_realtime_salt_is_refused(self) -> None:
        backend = stacked()
        signer = signing.Signer(
            key=KEY, sep=".", salt="next.realtime.subject.v1", algorithm="sha256"
        )
        token = "1.1." + signer.sign(b64encode(b"i" + _payload()))
        assert _reason(backend, token) is TokenRejection.SIGNATURE

    def test_a_rewritten_version_or_epoch_header_is_refused(self) -> None:
        backend = stacked()
        token = backend.issue(request_for(), claims())
        assert _reason(backend, "3" + token[1:]) is TokenRejection.SIGNATURE
        assert _reason(backend, "2.2" + token[3:]) is TokenRejection.SIGNATURE

    def test_an_epoch_with_a_leading_zero_is_malformed(self, monkeypatch) -> None:
        backend = stacked(EPOCH=1)
        token = backend.issue(request_for(), claims())
        monkeypatch.setattr(backend, "keyring", SimpleNamespace(current=None))
        assert _reason(backend, "2.01" + token[3:]) is TokenRejection.MALFORMED
        assert _reason(backend, "2.00" + token[3:]) is TokenRejection.MALFORMED

    def test_a_twin_of_the_last_body_character_is_malformed(self) -> None:
        backend = stacked()
        token = backend.issue(request_for(), claims(sku="A1"))
        body = split(token)[2].split(".")[0]
        end = len("2.1.") + len(body)
        twins = [
            token[: end - 1] + char + token[end:]
            for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            if char != body[-1] and b64decode(body[:-1] + char) == b64decode(body)
        ]
        assert twins
        for twin in twins:
            assert _reason(backend, twin) is TokenRejection.MALFORMED

    def test_a_token_of_a_past_epoch_answers_with_a_reload(self) -> None:
        token = stacked(EPOCH=1).issue(request_for(), claims())
        assert _reason(stacked(EPOCH=2), token) is TokenRejection.EXPIRED

    def test_a_forged_token_claiming_the_current_epoch_is_refused(self) -> None:
        token = stacked(EPOCH=1).issue(request_for(), claims())
        forged = "1.2." + split(token)[2]
        assert _reason(stacked(EPOCH=2), forged) is TokenRejection.SIGNATURE

    def test_a_verified_token_of_an_unknown_version_answers_with_a_reload(self) -> None:
        token = _forged(max(VERSIONS) + 1, 1, _payload(version=max(VERSIONS) + 1))
        assert _reason(stacked(), token) is TokenRejection.VERSION

    def test_a_payload_whose_version_disagrees_with_its_header_is_malformed(
        self,
    ) -> None:
        forged = _forged(VERSION, 1, _payload(version=VERSION + 1))
        assert _reason(stacked(), forged) is TokenRejection.MALFORMED

    def test_a_verified_token_of_the_version_before_answers_with_a_reload(self) -> None:
        forged = _forged(VERSION - 1, 1, _payload(version=VERSION - 1))
        assert _reason(stacked(), forged) is TokenRejection.VERSION

    def test_an_old_key_in_the_ring_is_accepted(self) -> None:
        token = stacked(KEYS=[OLD_KEY]).issue(request_for(), claims())
        redeemed = stacked(KEYS=[KEY, OLD_KEY]).redeem(request_for(), token, target())
        assert redeemed.component == "shop/card"

    def test_a_removed_key_is_refused(self) -> None:
        token = stacked(KEYS=[OLD_KEY]).issue(request_for(), claims())
        assert _reason(stacked(KEYS=[KEY]), token) is TokenRejection.SIGNATURE

    def test_an_encrypted_token_with_a_flipped_kid_nonce_or_tag_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_crypto(monkeypatch)
        backend = stacked(SEAL=SEAL_PATHS["encrypted"])
        token = backend.issue(request_for(), claims())
        version, epoch, sealed = split(token)
        raw = b64decode(sealed)
        for index in (0, 3, 4, 19, 20, len(raw) - 16, len(raw) - 1):
            flipped = bytearray(raw)
            flipped[index] ^= 0x80
            forged = f"{version}.{epoch}.{b64encode(bytes(flipped))}"
            assert _reason(backend, forged) is TokenRejection.SIGNATURE, index


@pytest.mark.usefixtures("clock")
class TestSizeBeforeCrypto:
    """Length and shape are checked before any signature or decryption work."""

    @pytest.fixture()
    def no_crypto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(*args, **kwargs):
            msg = "no crypto may run for this token"
            raise AssertionError(msg)

        monkeypatch.setattr(signing.Signer, "unsign", refuse)
        monkeypatch.setattr(signing.Signer, "signature", refuse)

    def test_an_oversized_token_never_reaches_the_signer(self, no_crypto) -> None:
        assert _reason(stacked(), "1.1." + "A" * 3000) is TokenRejection.TOO_LARGE

    def test_a_malformed_token_never_reaches_the_signer(self, no_crypto) -> None:
        assert _reason(stacked(), "garbage") is TokenRejection.MALFORMED

    @pytest.mark.parametrize(
        "token",
        ["garbage", "2.1.AB." + "A" * 43, "2.1.A." + "A" * 43, "2.1." + "A" * 3000],
    )
    def test_a_token_refused_for_its_shape_reads_no_ring_and_no_user(
        self, monkeypatch: pytest.MonkeyPatch, token: str
    ) -> None:
        calls: list[str] = []

        class Watched(HttpRequest):
            @property
            def user(self):
                calls.append("user")
                return AnonymousUser()

        def ring() -> tuple[str, ...]:
            calls.append("ring")
            return (KEY,)

        backend = stacked(BINDING="user")
        monkeypatch.setattr(backend, "keyring", SimpleNamespace(current=ring))
        _reason(backend, token, Watched())
        assert calls == []

    def test_a_well_shaped_forgery_loads_the_user_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        class Watched(HttpRequest):
            @property
            def user(self):
                calls.append("user")
                return AnonymousUser()

        backend = stacked(BINDING="user")
        forged = "2.1.AAAA." + "A" * 43
        assert _reason(backend, forged, Watched()) is TokenRejection.SIGNATURE
        assert calls == ["user"]

    def test_an_oversized_token_never_reaches_the_cipher(
        self, monkeypatch: pytest.MonkeyPatch, no_crypto
    ) -> None:
        install_crypto(monkeypatch)
        backend = stacked(SEAL=SEAL_PATHS["encrypted"])
        monkeypatch.setattr(backend.seal, "open", None)
        assert _reason(backend, "1.1." + "A" * 3000) is TokenRejection.TOO_LARGE

    def test_a_token_over_max_bytes_is_refused_at_mint(self) -> None:
        with pytest.raises(BoundarySizeError, match="MAX_BYTES"):
            stacked(MAX_BYTES=50).issue(request_for(), claims())

    def test_spill_storage_mints_what_inline_storage_cannot(self) -> None:
        big = claims(blob="x" * 400)
        with pytest.raises(BoundaryPropsError, match="MAX_BYTES"):
            stacked(MAX_BYTES=256).issue(request_for(), big)
        backend = stacked(MAX_BYTES=256, STORAGE=STORAGE_PATHS["spill"])
        token = backend.issue(request_for(), big)
        assert len(token) <= 256
        assert backend.redeem(request_for(), token, target()).props[0][1] == "x" * 400

    @pytest.mark.parametrize("seal", ["signed", "encrypted", "none"])
    @pytest.mark.parametrize("size", [1200, 1500, 1900, 5000])
    def test_spill_storage_never_refuses_what_the_cache_carries(
        self, monkeypatch: pytest.MonkeyPatch, seal: str, size: int
    ) -> None:
        install_crypto(monkeypatch)
        backend = stacked(STORAGE=STORAGE_PATHS["spill"], SEAL=SEAL_PATHS[seal])
        token = backend.issue(request_for(), claims(q="x" * size))
        assert len(token) <= 2048
        assert backend.redeem(request_for(), token, target()).props[0][1] == "x" * size

    @pytest.mark.parametrize("seal", ["signed", "encrypted", "none"])
    def test_spill_storage_keeps_inline_every_token_that_fits(
        self, monkeypatch: pytest.MonkeyPatch, seal: str
    ) -> None:
        install_crypto(monkeypatch)
        spill = stacked(STORAGE=STORAGE_PATHS["spill"], SEAL=SEAL_PATHS[seal])
        inline = stacked(SEAL=SEAL_PATHS[seal])
        longest = 0
        for size in range(1350, 1600):
            try:
                fitting = inline.issue(request_for(), claims(q="x" * size))
            except BoundaryPropsError:
                fitting = None
            token = spill.issue(request_for(), claims(q="x" * size))
            if fitting is None:
                assert len(token) < 200, size
            else:
                longest = max(longest, len(fitting))
                assert len(token) == len(fitting), size
        assert 2048 - 4 <= longest <= 2048

    def test_a_cap_no_reference_fits_is_refused_at_mint(self) -> None:
        backend = stacked(MAX_BYTES=40, STORAGE=STORAGE_PATHS["spill"])
        with pytest.raises(BoundaryPropsError, match="MAX_BYTES"):
            backend.issue(request_for(), claims())

    def test_the_room_is_searched_once_per_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = stacked(STORAGE=STORAGE_PATHS["spill"])
        counted: list[int] = []
        length = backend.seal.length

        def measured(size, context):
            counted.append(size)
            return length(size, context)

        monkeypatch.setattr(backend.seal, "length", measured)
        backend.issue(request_for(), claims(q="x" * 3000))
        searched = len(counted)
        backend.issue(request_for(), claims(q="y" * 3000))
        assert searched > 0
        assert len(counted) == searched


class TestExpiry:
    """A token lives from its floored mint second for its lifetime, never longer."""

    def test_the_last_second_is_accepted_and_the_next_is_expired(
        self, clock: FrozenClock
    ) -> None:
        backend = stacked(MAX_AGE=60)
        token = backend.issue(request_for(), claims())
        clock.advance(59)
        assert backend.redeem(request_for(), token, target())
        clock.advance(1)
        assert _reason(backend, token) is TokenRejection.EXPIRED

    def test_a_bucket_makes_equal_claims_give_equal_tokens(
        self, clock: FrozenClock
    ) -> None:
        backend = stacked(BUCKET=60)
        clock.at = _BUCKET_START + 20
        first = backend.issue(request_for(), claims())
        clock.advance(39)
        assert backend.issue(request_for(), claims()) == first
        assert backend.open(request_for(), first).expires == _BUCKET_START + 600
        clock.advance(1)
        assert backend.issue(request_for(), claims()) != first

    def test_the_bucket_shortens_the_lifetime_from_the_floor(
        self, clock: FrozenClock
    ) -> None:
        backend = stacked(BUCKET=60, MAX_AGE=120)
        clock.at = _BUCKET_START + 59
        token = backend.issue(request_for(), claims())
        clock.at = _BUCKET_START + 119
        assert backend.redeem(request_for(), token, target())
        clock.advance(1)
        assert _reason(backend, token) is TokenRejection.EXPIRED

    @pytest.mark.parametrize(
        ("answer", "lifetime"), [(0, 1), (10**9, 86_400), (90, 90)]
    )
    def test_a_callable_max_age_is_clamped_at_mint(
        self, clock, answer, lifetime
    ) -> None:
        _AGES.append(answer)
        backend = stacked(MAX_AGE=f"{__name__}.age_of")
        token = backend.issue(request_for(), claims())
        assert backend.open(request_for(), token).expires == NOW + lifetime

    def test_a_request_shaped_for_a_longer_life_extends_nothing(
        self, clock: FrozenClock
    ) -> None:
        backend = stacked(MAX_AGE=f"{__name__}.shaped_age")
        token = backend.issue(request_for(), claims())
        clock.advance(60)
        longer = request_for()
        longer.META["HTTP_X_LONG"] = "1"
        assert _reason(backend, token, longer) is TokenRejection.EXPIRED
        assert _reason(backend, token) is TokenRejection.EXPIRED

    def test_a_static_max_age_of_zero_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="from 1"):
            stacked(MAX_AGE=0)

    def test_a_bucket_over_a_callable_age_refuses_to_mint(
        self, clock: FrozenClock
    ) -> None:
        _AGES.append(60)
        backend = stacked(MAX_AGE=f"{__name__}.age_of", BUCKET=300)
        with pytest.raises(ImproperlyConfigured, match="born expired"):
            backend.issue(request_for(), claims())


@pytest.mark.usefixtures("clock")
class TestBindings:
    """A bound token fails for any other subject, the subject never in the token."""

    def _session_request(self) -> HttpRequest:
        session = SessionStore()
        session.create()
        request = request_for()
        request.session = session
        return request

    def test_a_session_bound_token_fails_in_another_session(self) -> None:
        backend = stacked(BINDING="next.suspense.SessionBinding")
        request = self._session_request()
        token = backend.issue(request, claims())
        assert request.session.session_key not in token
        assert backend.redeem(request, token, target())
        other = self._session_request()
        assert _reason(backend, token, other) is TokenRejection.SIGNATURE
        assert _reason(backend, token, request_for()) is TokenRejection.BINDING

    def test_a_user_bound_token_fails_for_another_user_and_after_a_password_change(
        self,
    ) -> None:
        backend = stacked(BINDING="next.suspense.UserBinding")
        user = User(pk=3, username="ada", password="before")
        request = request_for()
        request.user = user
        token = backend.issue(request, claims())
        assert backend.redeem(request, token, target())
        other = request_for()
        other.user = User(pk=4, username="bob", password="before")
        assert _reason(backend, token, other) is TokenRejection.SIGNATURE
        anonymous = request_for()
        anonymous.user = AnonymousUser()
        assert _reason(backend, token, anonymous) is TokenRejection.SIGNATURE
        user.password = "after"
        assert _reason(backend, token, request) is TokenRejection.SIGNATURE

    def test_a_callable_binding_compares_none_as_a_literal(self) -> None:
        backend = stacked(BINDING=f"{__name__}.tenant")
        unbound = backend.issue(request_for(), claims())
        assert backend.redeem(request_for(), unbound, target())
        tenanted = request_for()
        tenanted.META["HTTP_X_TENANT"] = "acme"
        assert _reason(backend, unbound, tenanted) is TokenRejection.SIGNATURE
        bound = backend.issue(tenanted, claims())
        assert backend.redeem(tenanted, bound, target())
        assert _reason(backend, bound, request_for()) is TokenRejection.SIGNATURE

    def test_the_subject_is_bound_once_per_request(self) -> None:
        _ASKED.clear()
        backend = stacked(BINDING=f"{__name__}.counted")
        request, other = request_for(), request_for()
        tokens = [backend.issue(request, claims(n=n)) for n in range(3)]
        tokens.append(backend.issue(other, claims()))
        assert [request, other] == _ASKED
        assert backend.redeem(other, tokens[0], target())

    def test_a_login_within_the_request_binds_the_new_user(self) -> None:
        backend = stacked(BINDING="next.suspense.UserBinding")
        request = request_for()
        request.user = AnonymousUser()
        backend.issue(request, claims())
        request.user = User(pk=3, username="ada", password="p")
        assert backend.redeem(request, backend.issue(request, claims()), target())

    def test_a_cycled_session_within_the_request_binds_the_new_key(self) -> None:
        backend = stacked(BINDING="next.suspense.SessionBinding")
        request = self._session_request()
        backend.issue(request, claims())
        request.session.cycle_key()
        assert backend.redeem(request, backend.issue(request, claims()), target())

    def test_an_unbound_token_differs_from_a_none_bound_one(self) -> None:
        unbound = stacked().issue(request_for(), claims())
        backend = stacked(BINDING=f"{__name__}.tenant")
        assert unbound != backend.issue(request_for(), claims())
        assert _reason(backend, unbound) is TokenRejection.SIGNATURE


@pytest.mark.usefixtures("clock")
class TestStorageRejection:
    """A refusal of the storage reaches the caller of the stack with its own reason."""

    def test_a_missing_cache_entry_answers_with_a_reload(self) -> None:
        backend = stacked(STORAGE=STORAGE_PATHS["cache"])
        token = backend.issue(request_for(), claims(sku="A1"))
        address = b64decode(split(token)[2].split(".")[0])[1:]
        caches["default"].delete(cache_key(address))
        assert _reason(backend, token) is TokenRejection.STORAGE_MISS


@pytest.mark.usefixtures("clock")
class TestTarget:
    """The server's own resolution of the request decides, never the token."""

    @pytest.mark.parametrize(
        "changes",
        [
            {"component": "shop/other"},
            {"scope": "pages/other"},
            {"url_path": "/other/"},
            {"url_kwargs": (("id", "6"),)},
            {"url_kwargs": ()},
        ],
    )
    def test_a_token_for_another_target_is_refused(self, changes) -> None:
        backend = stacked()
        token = backend.issue(request_for(), claims())
        assert _reason(backend, token, **changes) is TokenRejection.URL_MISMATCH

    def test_the_overrides_of_the_resolved_component_only_shorten_the_lifetime(
        self, clock
    ) -> None:
        backend = stacked()
        short = backend.issue(request_for(), claims(), tokens=(("max_age", 60),))
        long = backend.issue(request_for(), claims())
        clock.advance(100)
        assert _reason(backend, short) is TokenRejection.EXPIRED
        assert backend.redeem(request_for(), long, target())
        assert _reason(backend, long, tokens=(("max_age", 60),)) is (
            TokenRejection.EXPIRED
        )

    def test_the_overrides_of_the_resolved_component_decide_the_size(self) -> None:
        backend = stacked()
        token = backend.issue(request_for(), claims())
        tight = (("max_bytes", 50),)
        assert _reason(backend, token, tokens=tight) is TokenRejection.TOO_LARGE

    def test_a_binding_override_binds_the_token(self) -> None:
        backend = stacked()
        request = request_for()
        request.user = User(pk=3, username="ada", password="x")
        token = backend.issue(request, claims(), tokens=(("binding", "user"),))
        assert backend.redeem(request, token, target(tokens=(("binding", "user"),)))
        assert _reason(backend, token, request) is TokenRejection.SIGNATURE

    def test_override_settings_are_built_once_per_set(self) -> None:
        backend = stacked()
        tokens = (("max_age", 60),)
        assert backend.settings_for(tokens) is backend.settings_for(tokens)
        assert backend.settings_for(()) is backend.settings

    def test_validate_refuses_an_override_it_cannot_run_under(self) -> None:
        backend = stacked()
        backend.validate((("max_age", 60),))
        with pytest.raises(ImproperlyConfigured, match="BUCKET"):
            backend.validate((("max_age", 60), ("bucket", 60)))


@pytest.mark.usefixtures("clock")
class TestRejection:
    """Every refusal is announced and logged, the client told only expired or not."""

    def test_the_signal_carries_the_reason_request_and_component(self) -> None:
        backend = stacked()
        request = request_for()
        with (
            capture_signals(boundary_token_rejected) as recorder,
            pytest.raises(BoundaryTokenError),
        ):
            backend.redeem(request, "garbage", target())
        event = recorder.first_for(boundary_token_rejected)
        assert event.sender is StackedTokenBackend
        assert event.kwargs["reason"] is TokenRejection.MALFORMED
        assert event.kwargs["request"] is request
        assert event.kwargs["component"] == "shop/card"

    def test_the_outward_message_is_one_for_every_refusal(self, clock) -> None:
        backend = stacked()
        token = backend.issue(request_for(), claims())
        messages: dict[TokenRejection, str] = {}
        for bad, changes in (
            ("garbage", {}),
            (flip(token, 10), {}),
            ("1.1." + "A" * 3000, {}),
            (token, {"component": "x"}),
        ):
            with pytest.raises(BoundaryTokenError) as caught:
                backend.redeem(request_for(), bad, target(**changes))
            messages[caught.value.reason] = str(caught.value)
        assert set(messages) == {
            TokenRejection.MALFORMED,
            TokenRejection.SIGNATURE,
            TokenRejection.TOO_LARGE,
            TokenRejection.URL_MISMATCH,
        }
        assert set(messages.values()) == {"The boundary token was refused."}
        clock.advance(600)
        with pytest.raises(BoundaryTokenError) as caught:
            backend.redeem(request_for(), token, target())
        assert caught.value.expired
        assert str(caught.value) == "The boundary token expired."

    def test_garbage_is_logged_once_per_quiet_period(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reset_failure_logs()
        backend = stacked()
        with caplog.at_level(logging.INFO, logger="next.suspense"):
            for _attempt in range(50):
                with pytest.raises(BoundaryTokenError):
                    backend.redeem(request_for(), "garbage", target())
        refused = [r for r in caplog.records if "was refused as" in r.getMessage()]
        assert len(refused) == 1
        assert refused[0].levelno == logging.WARNING
        reset_failure_logs()


class _Minimal(BoundaryTokenBackend):
    def issue(self, request, claims, *, tokens=()) -> str:
        return "token"

    def redeem(self, request, token, target) -> BoundaryClaims:
        raise BoundaryTokenError(TokenRejection.MALFORMED)


class TestBackendContract:
    """A backend implements issue and redeem, and inherits the rest."""

    def test_a_minimal_backend_inherits_the_defaults(self) -> None:
        backend = _Minimal({"OPTIONS": {"ANY": 1}})
        assert backend.options == {"ANY": 1}
        assert backend.validate((("max_age", 1),)) is None
        assert isinstance(backend.transport, HeaderTransport)

    def test_options_that_are_no_mapping_read_as_empty(self) -> None:
        assert _Minimal({"OPTIONS": "x"}).options == {}
        assert _Minimal().options == {}

    def test_a_minimal_backend_caps_a_prop_at_the_default_limit(self) -> None:
        assert _Minimal().prop_limit((("max_bytes", 9),)) == DEFAULT_LIMIT

    @pytest.mark.parametrize(
        ("storage", "limit"),
        [
            ("next.suspense.InlineStorage", 300),
            (STORAGE_PATHS["spill"], 9000),
            (STORAGE_PATHS["cache"], 9000),
        ],
    )
    def test_the_stack_caps_a_prop_at_what_its_storage_carries(
        self, storage: str, limit: int
    ) -> None:
        backend = stacked(STORAGE=storage, MAX_BYTES=300, MAX_STORED_BYTES=9000)
        assert backend.prop_limit() == limit

    def test_a_component_override_moves_the_prop_cap(self) -> None:
        assert stacked().prop_limit((("max_bytes", 1000),)) == 1000

    def test_the_stack_carries_its_configured_transport(self) -> None:
        backend = stacked(TRANSPORT="next.suspense.QueryTransport")
        assert isinstance(backend.transport, QueryTransport)

    @pytest.mark.parametrize(
        "options",
        [
            {"CODEC": "nowhere.Codec"},
            {"SEAL": "next.suspense.JsonCodec"},
            {"STORAGE": "next.suspense.tokens.TokenStorage"},
            {"TRANSPORT": None},
        ],
    )
    def test_a_seam_outside_its_root_is_refused(self, options) -> None:
        with pytest.raises(ImproperlyConfigured):
            stacked(**options)

    def test_the_target_matches_on_component_scope_and_url(self) -> None:
        assert target().matches(
            BoundaryClaims.of(
                "shop/card", "pages/shop", url_path="/shop/", url_kwargs={"id": 5}
            )
        )
        assert isinstance(target(), BoundaryTarget)


class TestPurposeSeparation:
    """No salt of a boundary token is shared with any realtime salt."""

    def test_no_salt_string_is_shared(self) -> None:
        sources = Path(next.realtime.__file__).parent.rglob("*.py")
        realtime = {
            salt
            for path in sources
            for salt in re.findall(r'"(next\.[a-z.]+\.v\d+)"', path.read_text())
        }
        assert {"next.realtime.subject.v1", "next.realtime.visitor.v1"} <= realtime
        tokens = {
            PURPOSE,
            purpose(VERSION, 1),
            STORE_SALT,
            KID_SALT,
            ENCRYPTION_INFO.decode(),
            "next.defer.binding",
        }
        assert tokens.isdisjoint(realtime)
        assert all(salt.startswith("next.defer.") for salt in tokens)
        assert not any(salt.startswith("next.defer.") for salt in realtime)
