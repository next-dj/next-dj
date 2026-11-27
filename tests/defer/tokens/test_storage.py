import pytest
from django.core.cache import caches
from django.test import override_settings

from next.defer import BoundarySizeError, BoundaryTokenError, TokenRejection
from next.defer.tokens import CacheStorage, InlineStorage, SpillStorage, StorageTerms
from next.defer.tokens.storage import CACHED, INLINE, cache_key, content_address
from tests.support.tokens import KEY, OLD_KEY


def _context(**changes: object) -> StorageTerms:
    fields: dict[str, object] = {
        "lifetime": 600,
        "max_stored_bytes": 64,
        "cache_alias": "default",
        **changes,
    }
    return StorageTerms(**fields)


@pytest.fixture(autouse=True)
def _keys():
    with override_settings(SECRET_KEY=KEY, SECRET_KEY_FALLBACKS=[]):
        yield


def _reason(storage, reference: bytes, context: StorageTerms) -> TokenRejection:
    with pytest.raises(BoundaryTokenError) as caught:
        storage.get(reference, context)
    return caught.value.reason


class TestInlineStorage:
    """``InlineStorage`` carries the claims bytes behind its tag."""

    def test_bytes_round_trip(self) -> None:
        reference = InlineStorage().put(b"claims", _context())
        assert reference == INLINE + b"claims"
        assert InlineStorage().get(reference, _context()) == b"claims"

    def test_a_cache_reference_is_malformed(self) -> None:
        reason = _reason(InlineStorage(), CACHED + bytes(32), _context())
        assert reason is TokenRejection.MALFORMED


class TestCacheStorage:
    """``CacheStorage`` keeps claims under a content address that is their MAC."""

    def test_bytes_round_trip_under_a_content_address(self) -> None:
        reference = CacheStorage().put(b"claims", _context())
        assert reference == CACHED + content_address(b"claims", KEY)
        assert CacheStorage().get(reference, _context()) == b"claims"

    def test_equal_claims_share_one_entry(self) -> None:
        first = CacheStorage().put(b"claims", _context())
        assert CacheStorage().put(b"claims", _context()) == first
        assert CacheStorage().put(b"other", _context()) != first

    def test_only_bytes_are_stored(self) -> None:
        reference = CacheStorage().put(bytearray(b"claims"), _context())
        held = caches["default"].get(cache_key(reference[1:]))
        assert type(held) is bytes
        assert CacheStorage().get(reference, _context()) == b"claims"

    def test_the_entry_lives_as_long_as_the_token(self, monkeypatch) -> None:
        seen: list[object] = []
        cache = caches["default"]
        monkeypatch.setattr(cache, "set", lambda *args, timeout: seen.append(timeout))
        CacheStorage().put(b"claims", _context(lifetime=42))
        assert seen == [42]

    def test_an_entry_written_under_an_old_key_still_reads(self) -> None:
        with override_settings(SECRET_KEY=OLD_KEY):
            reference = CacheStorage().put(b"claims", _context())
        with override_settings(SECRET_KEY_FALLBACKS=[OLD_KEY]):
            assert CacheStorage().get(reference, _context()) == b"claims"

    def test_an_entry_of_a_removed_key_fails_its_mac(self) -> None:
        with override_settings(SECRET_KEY=OLD_KEY):
            reference = CacheStorage().put(b"claims", _context())
        reason = _reason(CacheStorage(), reference, _context())
        assert reason is TokenRejection.SIGNATURE

    @pytest.mark.parametrize(
        "forged",
        [
            pytest.param(b'{"forged":1}', id="poisoned-bytes"),
            pytest.param("claims", id="another-type"),
        ],
    )
    def test_a_forged_entry_fails_its_mac(self, forged: object) -> None:
        reference = CacheStorage().put(b"claims", _context())
        caches["default"].set(cache_key(reference[1:]), forged)
        reason = _reason(CacheStorage(), reference, _context())
        assert reason is TokenRejection.SIGNATURE

    def test_a_missing_entry_is_a_storage_miss(self) -> None:
        reference = CacheStorage().put(b"claims", _context())
        caches["default"].clear()
        reason = _reason(CacheStorage(), reference, _context())
        assert reason is TokenRejection.STORAGE_MISS

    def test_an_oversized_entry_is_refused_before_its_mac(self) -> None:
        reference = CacheStorage().put(b"claims", _context())
        caches["default"].set(cache_key(reference[1:]), b"x" * 65)
        reason = _reason(CacheStorage(), reference, _context())
        assert reason is TokenRejection.TOO_LARGE

    def test_claims_over_the_stored_cap_are_refused_at_mint(self) -> None:
        with pytest.raises(BoundarySizeError, match="MAX_STORED_BYTES"):
            CacheStorage().put(b"x" * 65, _context())

    @pytest.mark.parametrize(
        "reference", [INLINE + bytes(32), CACHED + bytes(31), CACHED + bytes(33)]
    )
    def test_a_reference_of_another_shape_is_malformed(self, reference) -> None:
        reason = _reason(CacheStorage(), reference, _context())
        assert reason is TokenRejection.MALFORMED

    def test_another_alias_is_a_storage_miss(self, settings) -> None:
        settings.CACHES = {
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
            "tokens": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "tokens",
            },
        }
        reference = CacheStorage().put(b"claims", _context(cache_alias="tokens"))
        assert CacheStorage().get(reference, _context(cache_alias="tokens"))
        reason = _reason(CacheStorage(), reference, _context())
        assert reason is TokenRejection.STORAGE_MISS


class TestSpillStorage:
    """``SpillStorage`` keeps claims inline and overflows them to the cache."""

    def test_claims_are_put_inline(self) -> None:
        reference = SpillStorage().put(b"x" * 80, _context())
        assert reference == INLINE + b"x" * 80
        assert SpillStorage().get(reference, _context()) == b"x" * 80

    def test_claims_whose_token_outgrew_its_cap_overflow_to_the_cache(self) -> None:
        reference = SpillStorage().overflow(b"x" * 9, _context())
        assert reference.startswith(CACHED)
        assert SpillStorage().get(reference, _context()) == b"x" * 9

    def test_other_storages_refuse_to_overflow(self) -> None:
        assert InlineStorage().overflow(b"x", _context()) is None
        assert CacheStorage().overflow(b"x", _context()) is None
