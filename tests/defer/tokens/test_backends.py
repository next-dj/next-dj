import base64
import hashlib
import itertools
import logging
import re
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.cache import SessionStore
from django.core import signing
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.test import override_settings

import next.realtime
from next.defer import (
    BoundaryPropsError,
    BoundarySizeError,
    BoundaryStorageError,
    BoundaryTokenError,
    TokenRejection,
    boundary_token_rejected,
)
from next.defer.tokens import (
    VERSION,
    VERSIONS,
    BoundaryClaims,
    BoundaryTarget,
    SignedSeal,
    StackedTokenBackend,
    StorageTerms,
    TokenStorage,
    codec,
)
from next.defer.tokens.carrier import BodyCarrier, HeaderCarrier
from next.defer.tokens.props import DEFAULT_LIMIT
from next.defer.tokens.seal import FERNET_INFO, PURPOSE, token_salt
from next.defer.tokens.storage import STORE_SALT, cache_key
from next.testing import FrozenClock, capture_signals, reset_failure_logs
from tests.support.crypto import install_crypto
from tests.support.tokens import (
    KEY,
    NOW,
    OLD_KEY,
    SEAL_PATHS,
    STORAGE_PATHS,
    MinimalTokenBackend,
    b64decode,
    b64encode,
    claims,
    fail_cache,
    flip,
    request_for,
    split,
    stacked,
    target,
)


_AGES: list[object] = []


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


class ProjectInlineStorage(TokenStorage):
    """A storage of a project that carries the claims in the token."""

    inline = True

    def put(self, data: bytes, terms: StorageTerms) -> bytes:
        return data

    def get(self, reference: bytes, terms: StorageTerms) -> bytes:
        return reference


def _noise(size: int) -> str:
    """Return `size` characters zlib cannot shrink much, so a token grows with them."""
    raw = hashlib.shake_256(b"noise").digest(size)
    return base64.urlsafe_b64encode(raw).decode()[:size]


def _body(token: str, subject: str = "") -> bytes:
    """Return the reference a signed token seals, read with the project key."""
    version, epoch, profile, sealed = split(token)
    salt = token_salt(int(version), int(epoch), profile, subject)
    return SignedSeal().open(sealed, salt)


def _reason(backend, token: str, request: HttpRequest | None = None, **changes):
    with pytest.raises(BoundaryTokenError) as caught:
        backend.redeem(request or request_for(), token, target(**changes))
    return caught.value.reason


def _forged(version: int, epoch: int, data: bytes) -> str:
    sealed = SignedSeal().seal(b"i" + data, token_salt(version, epoch, "0", ""))
    return f"{version}.{epoch}.0.{sealed}"


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
    return codec.dumps(BoundaryClaims(**fields))


@pytest.mark.usefixtures("clock")
class TestRoundTrip:
    """Every combination of the shipped seams mints a token that redeems."""

    @pytest.mark.parametrize(
        ("seal", "storage"), list(itertools.product(SEAL_PATHS, STORAGE_PATHS))
    )
    def test_every_seam_combination_round_trips(
        self, monkeypatch: pytest.MonkeyPatch, seal: str, storage: str
    ) -> None:
        install_crypto(monkeypatch)
        backend = stacked(SEAL=SEAL_PATHS[seal], STORAGE=STORAGE_PATHS[storage])
        token = backend.issue(request_for(), claims(sku="A1", count=3))
        redeemed = backend.redeem(request_for(), token, target())
        assert redeemed.props == (("count", 3), ("sku", "A1"))
        assert redeemed.perms == ("shop.view",)
        assert (redeemed.issued, redeemed.expires) == (NOW, NOW + 600)

    def test_the_token_reads_version_epoch_profile_and_a_url_safe_seal(self) -> None:
        token = stacked(EPOCH=7).issue(request_for(), claims())
        assert re.fullmatch(r"2\.7\.0\.\.?[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}", token)

    def test_the_signed_body_is_the_tagged_claims(self) -> None:
        token = stacked().issue(request_for(), claims())
        assert split(token)[:3] == (str(VERSION), "1", "0")
        assert _body(token).startswith(b"i{")

    def test_claims_that_compress_ride_compressed(self) -> None:
        backend = stacked(MAX_BYTES=256)
        token = backend.issue(request_for(), claims(blob="x" * 2000))
        assert len(token) <= 256
        assert split(token)[3].startswith(".")
        assert backend.redeem(request_for(), token, target()).props[0][1] == "x" * 2000

    def test_the_minted_expiry_is_sealed_in_the_claims(self) -> None:
        token = stacked(MAX_AGE=90).issue(request_for(), claims())
        assert b'"e":1000090' in _body(token)

    def test_the_cache_storage_token_hides_the_props(self) -> None:
        token = stacked(STORAGE=STORAGE_PATHS["cache"]).issue(
            request_for(), claims(sku="SECRET")
        )
        assert "SECRET" not in token
        assert b"SECRET" not in _body(token)


@pytest.mark.usefixtures("clock")
class TestTampering:
    """Any change to a token, its salt or its keys is refused before claims are read."""

    def test_every_character_of_body_and_mac_is_covered(self) -> None:
        backend = stacked()
        token = backend.issue(request_for(), claims(sku="A1"))
        for index in range(len("2.1.0."), len(token)):
            if token[index] == ".":
                continue
            assert _reason(backend, flip(token, index)) is (TokenRejection.SIGNATURE), (
                index
            )

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
            sep=".", salt="next.realtime.subject.v1", algorithm="sha256"
        )
        token = "2.1.0." + signer.sign(b64encode(b"i" + _payload()))
        assert _reason(backend, token) is TokenRejection.SIGNATURE

    def test_a_rewritten_version_or_epoch_header_is_refused(self) -> None:
        backend = stacked()
        token = backend.issue(request_for(), claims())
        assert _reason(backend, "3" + token[1:]) is TokenRejection.SIGNATURE
        assert _reason(backend, "2.2" + token[3:]) is TokenRejection.SIGNATURE

    def test_an_epoch_with_a_leading_zero_is_malformed(self, monkeypatch) -> None:
        backend = stacked(EPOCH=1)
        token = backend.issue(request_for(), claims())
        assert _reason(backend, "2.01" + token[3:]) is TokenRejection.MALFORMED
        assert _reason(backend, "2.00" + token[3:]) is TokenRejection.MALFORMED

    def test_a_token_of_a_past_epoch_answers_with_a_reload(self) -> None:
        token = stacked(EPOCH=1).issue(request_for(), claims())
        assert _reason(stacked(EPOCH=2), token) is TokenRejection.EXPIRED

    def test_a_forged_token_claiming_the_current_epoch_is_refused(self) -> None:
        token = stacked(EPOCH=1).issue(request_for(), claims())
        forged = "2.2.0." + split(token)[3]
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

    @pytest.mark.parametrize("seal", ["signed", "encrypted"])
    def test_a_token_minted_before_a_key_rotation_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch, seal: str
    ) -> None:
        install_crypto(monkeypatch)
        backend = stacked(SEAL=SEAL_PATHS[seal])
        with override_settings(SECRET_KEY=OLD_KEY):
            token = backend.issue(request_for(), claims())
        with override_settings(SECRET_KEY=KEY, SECRET_KEY_FALLBACKS=[OLD_KEY]):
            redeemed = backend.redeem(request_for(), token, target())
        assert redeemed.component == "shop/card"

    @pytest.mark.parametrize("seal", ["signed", "encrypted"])
    def test_a_token_of_a_retired_key_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, seal: str
    ) -> None:
        install_crypto(monkeypatch)
        backend = stacked(SEAL=SEAL_PATHS[seal])
        with override_settings(SECRET_KEY=OLD_KEY):
            token = backend.issue(request_for(), claims())
        with override_settings(SECRET_KEY=KEY, SECRET_KEY_FALLBACKS=[]):
            assert _reason(backend, token) is TokenRejection.SIGNATURE

    def test_an_encrypted_token_with_a_flipped_byte_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_crypto(monkeypatch)
        backend = stacked(SEAL=SEAL_PATHS["encrypted"])
        token = backend.issue(request_for(), claims())
        version, epoch, profile, sealed = split(token)
        raw = b64decode(sealed)
        for index in (0, 1, 9, 25, len(raw) - 33, len(raw) - 1):
            flipped = bytearray(raw)
            flipped[index] ^= 0x80
            forged = f"{version}.{epoch}.{profile}.{b64encode(bytes(flipped))}"
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
        ["garbage", "2.1.0.a=b", "2.1.0.", "2.1.AB." + "A" * 43, "2.1." + "A" * 3000],
    )
    def test_a_token_refused_for_its_shape_reads_no_user(
        self, monkeypatch: pytest.MonkeyPatch, token: str
    ) -> None:
        calls: list[str] = []

        class Watched(HttpRequest):
            @property
            def user(self):
                calls.append("user")
                return AnonymousUser()

        backend = stacked(BINDING="user")
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
        forged = "2.1.0.AAAA." + "A" * 43
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
        big = claims(blob=_noise(400))
        with pytest.raises(BoundaryPropsError, match="MAX_BYTES"):
            stacked(MAX_BYTES=256).issue(request_for(), big)
        backend = stacked(MAX_BYTES=256, STORAGE=STORAGE_PATHS["spill"])
        token = backend.issue(request_for(), big)
        assert len(token) <= 256
        assert backend.redeem(request_for(), token, target()).props[0][1] == _noise(400)

    @pytest.mark.parametrize("seal", ["signed", "encrypted", "none"])
    @pytest.mark.parametrize("size", [1200, 1500, 1900, 5000])
    def test_spill_storage_never_refuses_what_the_cache_carries(
        self, monkeypatch: pytest.MonkeyPatch, seal: str, size: int
    ) -> None:
        install_crypto(monkeypatch)
        backend = stacked(STORAGE=STORAGE_PATHS["spill"], SEAL=SEAL_PATHS[seal])
        token = backend.issue(request_for(), claims(q=_noise(size)))
        assert len(token) <= 2048
        assert backend.redeem(request_for(), token, target()).props[0][1] == _noise(
            size
        )

    @pytest.mark.parametrize("seal", ["signed", "encrypted", "none"])
    def test_spill_storage_keeps_inline_every_token_that_fits(
        self, monkeypatch: pytest.MonkeyPatch, seal: str
    ) -> None:
        install_crypto(monkeypatch)
        spill = stacked(STORAGE=STORAGE_PATHS["spill"], SEAL=SEAL_PATHS[seal])
        inline = stacked(SEAL=SEAL_PATHS[seal])
        longest = 0
        for size in range(1000, 1900, 3):
            try:
                fitting = inline.issue(request_for(), claims(q=_noise(size)))
            except BoundaryPropsError:
                fitting = None
            token = spill.issue(request_for(), claims(q=_noise(size)))
            if fitting is None:
                assert len(token) < 200, size
            else:
                longest = max(longest, len(fitting))
                assert len(token) == len(fitting), size
        assert 2048 - 16 <= longest <= 2048

    def test_a_cap_no_reference_fits_is_refused_at_mint(self) -> None:
        backend = stacked(MAX_BYTES=40, STORAGE=STORAGE_PATHS["spill"])
        with pytest.raises(BoundaryPropsError, match="MAX_BYTES"):
            backend.issue(request_for(), claims())


class TestExpiry:
    """A token lives from its mint second for its lifetime, never longer."""

    def test_the_last_second_is_accepted_and_the_next_is_expired(
        self, clock: FrozenClock
    ) -> None:
        backend = stacked(MAX_AGE=60)
        token = backend.issue(request_for(), claims())
        clock.advance(59)
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
        backend = stacked(BINDING="next.defer.tokens.binding.SessionBinding")
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
        backend = stacked(BINDING="next.defer.tokens.binding.UserBinding")
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
        backend = stacked(BINDING="next.defer.tokens.binding.UserBinding")
        request = request_for()
        request.user = AnonymousUser()
        backend.issue(request, claims())
        request.user = User(pk=3, username="ada", password="p")
        assert backend.redeem(request, backend.issue(request, claims()), target())

    def test_a_cycled_session_within_the_request_binds_the_new_key(self) -> None:
        backend = stacked(BINDING="next.defer.tokens.binding.SessionBinding")
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
        address = _body(token)[1:]
        caches["default"].delete(cache_key(address))
        assert _reason(backend, token) is TokenRejection.STORAGE_MISS

    def test_a_cache_that_fails_to_answer_asks_for_a_reload_and_logs_it(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reset_failure_logs()
        backend = stacked(STORAGE=STORAGE_PATHS["cache"])
        token = backend.issue(request_for(), claims(sku="A1"))
        monkeypatch.setattr(caches["default"], "get", fail_cache)
        with (
            caplog.at_level(logging.ERROR, logger="next.defer"),
            pytest.raises(BoundaryTokenError) as caught,
        ):
            backend.redeem(request_for(), token, target())
        assert caught.value.reason is TokenRejection.STORAGE_DOWN
        assert caught.value.expired
        assert isinstance(caught.value.__cause__, ConnectionError)
        (record,) = caplog.records
        assert record.levelno == logging.ERROR
        assert "the token storage failed to answer" in record.getMessage()
        assert record.exc_info[1] is caught.value
        reset_failure_logs()

    def test_a_cache_that_fails_to_keep_the_claims_refuses_the_mint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(caches["default"], "set", fail_cache)
        backend = stacked(STORAGE=STORAGE_PATHS["cache"])
        with pytest.raises(BoundaryStorageError, match="failed to keep the claims"):
            backend.issue(request_for(), claims(sku="A1"))


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

    def test_the_overrides_of_the_resolved_component_decide_the_lifetime(
        self, clock
    ) -> None:
        backend = stacked()
        overrides = (("max_age", 60),)
        short = backend.issue(request_for(), claims(), tokens=overrides)
        long = backend.issue(request_for(), claims())
        clock.advance(100)
        assert _reason(backend, short, tokens=overrides) is TokenRejection.EXPIRED
        assert backend.redeem(request_for(), long, target())

    def test_a_token_settles_only_under_the_overrides_it_was_minted_under(self) -> None:
        backend = stacked()
        overrides = (("max_age", 60),)
        long = backend.issue(request_for(), claims())
        short = backend.issue(request_for(), claims(), tokens=overrides)
        assert _reason(backend, long, tokens=overrides) is TokenRejection.URL_MISMATCH
        assert _reason(backend, short) is TokenRejection.URL_MISMATCH

    def test_the_overrides_of_the_resolved_component_decide_the_size(self) -> None:
        backend = stacked()
        token = backend.issue(request_for(), claims())
        tight = (("max_bytes", 50),)
        assert _reason(backend, token, tokens=tight) is TokenRejection.TOO_LARGE

    def test_a_binding_override_binds_the_token(self) -> None:
        backend = stacked()
        request = request_for()
        request.user = User(pk=3, username="ada", password="x")
        token = backend.issue(request, claims(), tokens=(("binding", "none"),))
        assert backend.redeem(request, token, target(tokens=(("binding", "none"),)))
        assert _reason(backend, token, request) is TokenRejection.SIGNATURE

    def test_override_settings_are_built_once_per_set(self) -> None:
        backend = stacked()
        tokens = (("max_age", 60),)
        assert backend.settings_for(tokens) is backend.settings_for(tokens)
        assert backend.settings_for(()) is backend.settings

    def test_validate_refuses_an_override_it_cannot_run_under(self) -> None:
        backend = stacked()
        backend.validate((("max_age", 60),))
        with pytest.raises(ImproperlyConfigured, match="have no use"):
            backend.validate((("bucket", 60),))


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
            (flip(token, 12), {}),
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
        with caplog.at_level(logging.INFO, logger="next.defer"):
            for _attempt in range(50):
                with pytest.raises(BoundaryTokenError):
                    backend.redeem(request_for(), "garbage", target())
        refused = [
            (record.name, record.levelno)
            for record in caplog.records
            if "was refused as" in record.getMessage()
        ]
        assert refused == [
            ("next.defer", logging.WARNING),
            ("django.security.BoundaryTokenError", logging.WARNING),
        ]
        reset_failure_logs()

    def test_a_stale_token_stays_off_the_security_log(
        self, clock, caplog: pytest.LogCaptureFixture
    ) -> None:
        reset_failure_logs()
        backend = stacked()
        token = backend.issue(request_for(), claims())
        clock.advance(600)
        with caplog.at_level(logging.INFO):
            assert _reason(backend, token) is TokenRejection.EXPIRED
        names = {record.name for record in caplog.records}
        assert "next.defer" in names
        assert not any(name.startswith("django.security") for name in names)
        reset_failure_logs()

    def test_the_log_names_the_reason_and_what_causes_it(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reset_failure_logs()
        with caplog.at_level(logging.WARNING, logger="next.defer"):
            _reason(stacked(), "garbage")
        message = caplog.records[0].getMessage()
        assert "malformed, its text is not one the token backend mints" in message
        reset_failure_logs()

    def test_a_raising_receiver_leaves_the_refusal_as_it_was(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def broken(**kwargs: object) -> None:
            msg = "receiver failed"
            raise RuntimeError(msg)

        boundary_token_rejected.connect(broken, weak=False)
        try:
            with caplog.at_level(logging.ERROR, logger="django.dispatch"):
                assert _reason(stacked(), "garbage") is TokenRejection.MALFORMED
        finally:
            boundary_token_rejected.disconnect(broken)
        (record,) = [r for r in caplog.records if r.name == "django.dispatch"]
        assert isinstance(record.exc_info[1], RuntimeError)


class TestBackendContract:
    """A backend implements issue, open and settle, and inherits the rest."""

    def test_a_minimal_backend_inherits_the_defaults(self) -> None:
        backend = MinimalTokenBackend({"OPTIONS": {"ANY": 1}})
        assert backend.options == {"ANY": 1}
        assert backend.validate((("max_age", 1),)) is None
        assert isinstance(backend.carrier, HeaderCarrier)

    def test_options_that_are_no_mapping_read_as_empty(self) -> None:
        assert MinimalTokenBackend({"OPTIONS": "x"}).options == {}
        assert MinimalTokenBackend().options == {}

    def test_a_minimal_backend_caps_a_prop_at_the_default_limit(self) -> None:
        assert MinimalTokenBackend().prop_limit((("max_bytes", 9),)) == DEFAULT_LIMIT

    @pytest.mark.parametrize(
        ("storage", "limit"),
        [
            ("next.defer.InlineStorage", 300),
            (STORAGE_PATHS["spill"], 9000),
            (STORAGE_PATHS["cache"], 9000),
        ],
    )
    def test_the_stack_caps_a_prop_at_what_its_storage_carries(
        self, storage: str, limit: int
    ) -> None:
        backend = stacked(STORAGE=storage, MAX_BYTES=300, MAX_STORED_BYTES=9000)
        assert backend.prop_limit() == limit

    def test_a_project_storage_that_keeps_claims_inline_caps_at_max_bytes(self) -> None:
        backend = stacked(
            STORAGE=f"{__name__}.ProjectInlineStorage",
            MAX_BYTES=300,
            MAX_STORED_BYTES=9000,
        )
        assert backend.prop_limit() == 300

    def test_a_component_override_moves_the_prop_cap(self) -> None:
        assert stacked().prop_limit((("max_bytes", 1000),)) == 1000

    def test_the_stack_carries_its_configured_carrier(self) -> None:
        backend = stacked(CARRIER="body")
        assert isinstance(backend.carrier, BodyCarrier)

    @pytest.mark.parametrize("name", ["next.defer.HeaderCarrier", "cookie", None])
    def test_a_carrier_the_client_lacks_is_refused(self, name) -> None:
        with pytest.raises(ImproperlyConfigured, match="must be one of"):
            stacked(CARRIER=name)

    @pytest.mark.parametrize(
        "options",
        [
            {"SEAL": "nowhere.Seal"},
            {"SEAL": "next.defer.InlineStorage"},
            {"STORAGE": "next.defer.tokens.TokenStorage"},
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
        tokens = {PURPOSE, token_salt(VERSION, 1, "0", ""), STORE_SALT, FERNET_INFO}
        assert tokens.isdisjoint(realtime)
        assert all(salt.startswith("next.defer.") for salt in tokens)
        assert not any(salt.startswith("next.defer.") for salt in realtime)
