import hashlib

import pytest
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.utils.crypto import salted_hmac

from next.suspense import BoundaryTokenError, TokenRejection
from next.suspense.tokens import VERSION, EncryptedSeal, NoSeal, SignedSeal, TokenSeal
from next.suspense.tokens.context import TokenContext, binding_digest, purpose
from next.suspense.tokens.seal import (
    ENCRYPTION_INFO,
    KID_SALT,
    _mac_key,
    associated_data,
    crypto_library,
    encoded_length,
    encryption_info,
    forget_crypto,
    key_id,
    signed_salt,
)
from tests.support.crypto import FakeAESGCM, install_crypto
from tests.support.tokens import KEY, OLD_KEY, b64decode, b64encode, flip, flip_bit


_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _context(**changes: object) -> TokenContext:
    fields: dict[str, object] = {"version": VERSION, "epoch": 1, "ring": (KEY,)}
    return TokenContext(**{**fields, **changes})


def _unread() -> TokenContext:
    """Return a context whose ring and binding fail the test once they are read."""

    def refuse() -> object:
        msg = "a token of the wrong shape read the ring or the binding"
        raise AssertionError(msg)

    return _context(ring=refuse, digest=refuse)


def _signer(context: TokenContext) -> signing.Signer:
    """Return the Django signer whose text `SignedSeal` writes and reads."""
    return signing.Signer(
        key=context.keys[0],
        fallback_keys=list(context.keys[1:]),
        sep=".",
        salt=signed_salt(context),
        algorithm="sha256",
    )


def _reason(seal, sealed: str, context: TokenContext) -> TokenRejection:
    with pytest.raises(BoundaryTokenError) as caught:
        seal.open(sealed, context)
    return caught.value.reason


def _twin(text: str) -> str:
    """Return another base64 text that decodes to the bytes of `text`."""
    for char in _ALPHABET:
        twin = text[:-1] + char
        if twin != text and b64decode(twin) == b64decode(text):
            return twin
    msg = f"{text!r} leaves no unused bits in its last character"
    raise AssertionError(msg)


class _Measured(TokenSeal):
    """A seal that counts nothing, so the measuring default answers its lengths."""

    def seal(self, payload: bytes, context: TokenContext) -> str:
        return "x" * (len(payload) * 2 + 5)

    def open(self, sealed: str, context: TokenContext) -> bytes:
        return b""


@pytest.fixture()
def crypto(monkeypatch: pytest.MonkeyPatch) -> None:
    install_crypto(monkeypatch)


class TestPurpose:
    """Every salt names the claims version and the epoch."""

    def test_the_salt_names_version_and_epoch(self) -> None:
        assert purpose(1, 7) == "next.defer.boundary.v1.7"
        assert _context(epoch=7).purpose == f"next.defer.boundary.v{VERSION}.7"

    def test_the_binding_digest_hides_the_subject(self) -> None:
        digest = binding_digest("session:abc")
        assert binding_digest("") == ""
        assert len(digest) == 64
        assert "abc" not in digest
        assert digest != binding_digest("session:xyz")

    def test_the_binding_enters_the_signer_salt(self) -> None:
        assert signed_salt(_context()) == f"next.defer.boundary.v{VERSION}.1"
        bound = _context(digest="d" * 64)
        assert signed_salt(bound) == f"next.defer.boundary.v{VERSION}.1." + "d" * 64

    def test_the_binding_enters_the_associated_data(self) -> None:
        data = associated_data(_context(digest="d"))
        assert data == f"next.defer.boundary|{VERSION}|1|d".encode()

    def test_the_token_purpose_enters_the_key_info(self) -> None:
        info = encryption_info(_context(epoch=7))
        assert info == ENCRYPTION_INFO + f"|next.defer.boundary.v{VERSION}.7".encode()


class TestCanonicalText:
    """Only the canonical encoding of the bytes opens, so no two strings are one token."""

    @pytest.mark.parametrize("seal", [NoSeal, SignedSeal])
    def test_a_twin_of_the_last_character_is_malformed(self, seal) -> None:
        sealed = seal().seal(b"claim", _context())
        body, _, mac = sealed.partition(".")
        twin = _twin(body) + ("." + mac if mac else "")
        assert seal().open(sealed, _context()) == b"claim"
        assert _reason(seal(), twin, _unread()) is TokenRejection.MALFORMED

    def test_a_twin_of_an_encrypted_text_is_malformed(self, crypto) -> None:
        sealed = EncryptedSeal().seal(b"claim", _context())
        assert len(b64decode(sealed)) % 3
        reason = _reason(EncryptedSeal(), _twin(sealed), _unread())
        assert reason is TokenRejection.MALFORMED


class TestShapeBeforeSecrets:
    """A text of the wrong shape is refused before the ring or the binding is read."""

    @pytest.mark.parametrize(
        "sealed", ["", "A", "a.b", "é", "AAAA", "AAAA." + "A" * 42, "AB." + "A" * 43]
    )
    def test_a_signed_text_of_the_wrong_shape_reads_no_secret(self, sealed) -> None:
        assert _reason(SignedSeal(), sealed, _unread()) is TokenRejection.MALFORMED

    @pytest.mark.parametrize("sealed", ["", "A", "A" * 47, "AB", "not base64!"])
    def test_an_encrypted_text_of_the_wrong_shape_reads_no_secret(
        self, crypto, sealed
    ) -> None:
        assert _reason(EncryptedSeal(), sealed, _unread()) is TokenRejection.MALFORMED


class TestLength:
    """Every shipped seal counts the characters it writes for a reference size."""

    @pytest.mark.parametrize("size", [0, 1, 2, 3, 4, 100, 1501])
    def test_each_seal_counts_what_it_writes(self, crypto, size: int) -> None:
        for seal in (NoSeal(), SignedSeal(), EncryptedSeal()):
            sealed = seal.seal(bytes(size), _context())
            assert seal.length(size, _context()) == len(sealed), type(seal).__name__

    @pytest.mark.parametrize(("size", "chars"), [(0, 0), (1, 2), (2, 3), (3, 4)])
    def test_unpadded_base64_lengths(self, size: int, chars: int) -> None:
        assert encoded_length(size) == chars

    def test_another_seal_is_measured_by_sealing(self) -> None:
        assert _Measured().length(10, _context()) == 25


class TestSignedSeal:
    """``SignedSeal`` signs with the first key and verifies with every key."""

    def test_payload_round_trips_and_stays_readable(self) -> None:
        sealed = SignedSeal().seal(b"claims", _context())
        body, mac = sealed.split(".")
        assert b64decode(body) == b"claims"
        assert len(mac) == 43
        assert SignedSeal().open(sealed, _context()) == b"claims"

    def test_signing_is_deterministic(self) -> None:
        assert SignedSeal().seal(b"x", _context()) == SignedSeal().seal(
            b"x", _context()
        )

    def test_an_old_key_in_the_ring_still_verifies(self) -> None:
        sealed = SignedSeal().seal(b"claims", _context(ring=(OLD_KEY,)))
        assert SignedSeal().open(sealed, _context(ring=(KEY, OLD_KEY))) == b"claims"

    def test_a_removed_key_no_longer_verifies(self) -> None:
        sealed = SignedSeal().seal(b"claims", _context(ring=(OLD_KEY,)))
        assert _reason(SignedSeal(), sealed, _context()) is TokenRejection.SIGNATURE

    @pytest.mark.parametrize(
        "changes", [{"version": VERSION + 1}, {"epoch": 2}, {"digest": "d" * 64}]
    )
    def test_another_salt_fails(self, changes: dict[str, object]) -> None:
        sealed = SignedSeal().seal(b"claims", _context())
        reason = _reason(SignedSeal(), sealed, _context(**changes))
        assert reason is TokenRejection.SIGNATURE

    def test_a_token_signed_under_a_realtime_salt_fails(self) -> None:
        signer = signing.Signer(
            key=KEY, sep=".", salt="next.realtime.subject.v1", algorithm="sha256"
        )
        sealed = signer.sign(b64encode(b"claims"))
        assert _reason(SignedSeal(), sealed, _context()) is TokenRejection.SIGNATURE

    def test_every_body_and_mac_position_is_covered(self) -> None:
        sealed = SignedSeal().seal(b'{"c":"card","p":{"sku":"A1"}}', _context())
        last = sealed.index(".") - 1
        for index, char in enumerate(sealed):
            if char == ".":
                continue
            reason = _reason(SignedSeal(), flip(sealed, index), _context())
            refused = {TokenRejection.SIGNATURE}
            if index == last:
                refused.add(TokenRejection.MALFORMED)
            assert reason in refused, index

    @pytest.mark.parametrize(
        "mangle",
        [
            lambda sealed: sealed[:-1],
            lambda sealed: sealed + "A",
            lambda sealed: sealed.split(".")[0],
            lambda sealed: sealed + "." + sealed,
            lambda sealed: sealed.replace(".", ":"),
            lambda _sealed: "",
            lambda sealed: sealed + "\n",
            lambda sealed: "é" + sealed,
        ],
    )
    def test_truncation_and_concatenation_are_refused(self, mangle) -> None:
        sealed = SignedSeal().seal(b"claims", _context())
        reason = _reason(SignedSeal(), mangle(sealed), _context())
        assert reason in {TokenRejection.MALFORMED, TokenRejection.SIGNATURE}

    def test_a_signed_body_that_is_no_base64_is_malformed(self) -> None:
        signer = _signer(_context())
        assert _reason(SignedSeal(), signer.sign("A"), _context()) is (
            TokenRejection.MALFORMED
        )

    @pytest.mark.parametrize("digest", ["", "d" * 64])
    def test_the_text_is_what_a_django_signer_writes(self, digest: str) -> None:
        context = _context(digest=digest)
        sealed = SignedSeal().seal(b"claims", context)
        assert sealed == _signer(context).sign(b64encode(b"claims"))
        assert _signer(context).unsign(sealed) == b64encode(b"claims")

    def test_a_django_signer_text_under_an_old_key_opens(self) -> None:
        sealed = _signer(_context(ring=(OLD_KEY,))).sign(b64encode(b"claims"))
        assert SignedSeal().open(sealed, _context(ring=(KEY, OLD_KEY))) == b"claims"

    def test_the_key_of_a_salt_is_derived_once(self) -> None:
        for _ in range(3):
            SignedSeal().seal(b"claims", _context())
        assert _mac_key.cache_info().misses == 1
        assert _mac_key.cache_info().hits == 2

    def test_the_derived_keys_stay_bounded(self) -> None:
        bound = _mac_key.cache_info().maxsize
        assert bound is not None
        for index in range(bound + 8):
            SignedSeal().seal(b"claims", _context(digest=f"{index:064d}"))
        assert _mac_key.cache_info().currsize == bound

    def test_forgetting_drops_the_derived_keys(self) -> None:
        SignedSeal().seal(b"claims", _context())
        forget_crypto()
        assert _mac_key.cache_info().currsize == 0


class TestNoSeal:
    """``NoSeal`` encodes without protection, for tests only."""

    def test_payload_round_trips(self) -> None:
        sealed = NoSeal().seal(b"claims", _context())
        assert NoSeal().open(sealed, _context()) == b"claims"
        assert NoSeal().available()
        assert not NoSeal.encrypts

    @pytest.mark.parametrize("sealed", ["", "a.b", "A", "é"])
    def test_text_that_is_no_base64_is_malformed(self, sealed: str) -> None:
        assert _reason(NoSeal(), sealed, _context()) is TokenRejection.MALFORMED


@pytest.mark.usefixtures("crypto")
class TestEncryptedSeal:
    """``EncryptedSeal`` encrypts under AES-GCM with a key derived for each token."""

    def test_payload_round_trips_and_is_hidden(self) -> None:
        sealed = EncryptedSeal().seal(b"secret-props", _context())
        assert b"secret-props" not in b64decode(sealed)
        assert EncryptedSeal().open(sealed, _context()) == b"secret-props"
        assert EncryptedSeal.encrypts
        assert EncryptedSeal().available()

    def test_the_layout_is_kid_salt_ciphertext_and_tag(self) -> None:
        blob = b64decode(EncryptedSeal().seal(b"claims", _context()))
        assert len(blob) == 4 + 16 + len(b"claims") + 16
        assert blob[:4] == key_id(KEY)

    def test_every_token_carries_a_fresh_salt_under_one_key_id(self) -> None:
        first = b64decode(EncryptedSeal().seal(b"x", _context()))
        second = b64decode(EncryptedSeal().seal(b"x", _context()))
        assert first[:4] == second[:4]
        assert first[4:20] != second[4:20]
        assert first != second

    def test_each_token_has_its_own_key_and_the_nonce_is_fixed(self) -> None:
        for _attempt in range(50):
            EncryptedSeal().seal(b"x", _context())
        keys = {key for key, _nonce in FakeAESGCM.sealed}
        assert len(keys) == 50
        assert {nonce for _key, nonce in FakeAESGCM.sealed} == {bytes(12)}

    def test_the_key_is_derived_from_the_ring_key_and_the_salt(self) -> None:
        library = crypto_library()
        blob = b64decode(EncryptedSeal().seal(b"x", _context()))
        derived = library.HKDF(
            algorithm=library.SHA256(),
            length=32,
            salt=blob[4:20],
            info=encryption_info(_context()),
        ).derive(KEY.encode())
        assert derived != KEY.encode()[:32]
        plain = library.AESGCM(derived).decrypt(
            bytes(12), blob[20:], associated_data(_context())
        )
        assert plain == b"x"

    def test_the_key_id_names_the_ring_key_without_revealing_it(self) -> None:
        mac = salted_hmac(KID_SALT, b"kid", secret=KEY, algorithm="sha256")
        assert key_id(KEY) == mac.digest()[:4]
        assert key_id(KEY) != key_id(OLD_KEY)
        assert key_id(KEY) != hashlib.sha256(KEY.encode()).digest()[:4]

    @pytest.mark.parametrize(
        ("index", "region"), [(0, "kid"), (3, "kid"), (4, "salt"), (19, "salt")]
    )
    def test_a_flipped_kid_or_salt_bit_fails(self, index: int, region: str) -> None:
        sealed = EncryptedSeal().seal(b"claims", _context())
        reason = _reason(EncryptedSeal(), flip_bit(sealed, index), _context())
        assert reason is TokenRejection.SIGNATURE, region

    def test_every_ciphertext_and_tag_byte_is_covered(self) -> None:
        sealed = EncryptedSeal().seal(b"claims", _context())
        for index in range(20, len(b64decode(sealed))):
            reason = _reason(EncryptedSeal(), flip_bit(sealed, index), _context())
            assert reason is TokenRejection.SIGNATURE, index

    @pytest.mark.parametrize(
        "changes", [{"version": VERSION + 1}, {"epoch": 2}, {"digest": "d" * 64}]
    )
    def test_other_associated_data_fails(self, changes: dict[str, object]) -> None:
        sealed = EncryptedSeal().seal(b"claims", _context())
        reason = _reason(EncryptedSeal(), sealed, _context(**changes))
        assert reason is TokenRejection.SIGNATURE

    def test_an_old_key_in_the_ring_still_opens(self) -> None:
        sealed = EncryptedSeal().seal(b"claims", _context(ring=(OLD_KEY,)))
        opened = EncryptedSeal().open(sealed, _context(ring=(KEY, OLD_KEY)))
        assert opened == b"claims"

    def test_a_removed_key_no_longer_opens(self) -> None:
        sealed = EncryptedSeal().seal(b"claims", _context(ring=(OLD_KEY,)))
        assert _reason(EncryptedSeal(), sealed, _context()) is TokenRejection.SIGNATURE

    def test_a_kid_that_matches_a_key_whose_tag_fails_is_refused(self) -> None:
        sealed = b64decode(EncryptedSeal().seal(b"claims", _context()))
        forged = b64encode(sealed[:20] + bytes(len(sealed) - 20))
        assert _reason(EncryptedSeal(), forged, _context()) is TokenRejection.SIGNATURE


class TestEncryptedSealWithoutTheLibrary:
    """``EncryptedSeal`` names the extra it needs when ``cryptography`` is missing."""

    def test_it_refuses_to_build(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_crypto(monkeypatch, present=False)
        assert crypto_library() is None
        with pytest.raises(ImproperlyConfigured, match=r"next-dj\[crypto\]"):
            EncryptedSeal()

    def test_a_seal_built_before_the_library_went_reports_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_crypto(monkeypatch)
        seal = EncryptedSeal()
        install_crypto(monkeypatch, present=False)
        assert not seal.available()
        with pytest.raises(ImproperlyConfigured):
            seal.seal(b"x", _context())


class TestEncryptedSealOnTheRealLibrary:
    """The seal against the installed ``cryptography``, skipped without it."""

    def test_payload_round_trips_and_tampering_fails(self) -> None:
        pytest.importorskip("cryptography")
        sealed = EncryptedSeal().seal(b"claims", _context())
        assert EncryptedSeal().open(sealed, _context()) == b"claims"
        for index in (0, 4, 19, 20, len(b64decode(sealed)) - 1):
            reason = _reason(EncryptedSeal(), flip_bit(sealed, index), _context())
            assert reason is TokenRejection.SIGNATURE, index

    def test_the_layout_decrypts_with_the_library_itself(self) -> None:
        pytest.importorskip("cryptography")
        library = crypto_library()
        blob = b64decode(EncryptedSeal().seal(b"claims", _context()))
        assert blob[:4] == key_id(KEY)
        derived = library.HKDF(
            algorithm=library.SHA256(),
            length=32,
            salt=blob[4:20],
            info=encryption_info(_context()),
        ).derive(KEY.encode())
        plain = library.AESGCM(derived).decrypt(
            bytes(12), blob[20:], associated_data(_context())
        )
        assert plain == b"claims"
