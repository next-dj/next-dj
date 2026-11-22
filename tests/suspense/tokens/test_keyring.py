import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from next.suspense.tokens import FrozenClock, KeyRing
from next.suspense.tokens.keyring import REFRESH
from tests.support.tokens import KEY, NOW, OLD_KEY


class _Racing:
    """A lock whose holder finds the ring refreshed by another thread meanwhile."""

    def __init__(self, keys: KeyRing, fresh: tuple[str, ...]) -> None:
        self.keys = keys
        self.fresh = fresh

    def __enter__(self) -> None:
        self.keys._held = type(self.keys._held)(self.fresh, NOW, NOW + REFRESH)

    def __exit__(self, *exc: object) -> None:
        return None


class TestKeyRing:
    """``KeyRing`` reads the project secrets, a list, or a callable held a minute."""

    @override_settings(SECRET_KEY="s" * 50, SECRET_KEY_FALLBACKS=["f" * 50])
    def test_no_keys_read_the_project_secrets_each_time(self) -> None:
        keys = KeyRing()
        assert keys.current() == ("s" * 50, "f" * 50)
        with override_settings(SECRET_KEY="t" * 50, SECRET_KEY_FALLBACKS=[]):
            assert keys.current() == ("t" * 50,)

    @pytest.mark.parametrize(
        ("secret", "fallbacks"),
        [("s" * 31, []), ("s" * 50, ["a"]), ("s" * 50, ["f" * 50, "f" * 31])],
    )
    def test_a_short_project_secret_is_refused_at_each_read(
        self, secret: str, fallbacks: list[str]
    ) -> None:
        keys = KeyRing()
        with (
            override_settings(SECRET_KEY=secret, SECRET_KEY_FALLBACKS=fallbacks),
            pytest.raises(ImproperlyConfigured, match="SECRET_KEY_FALLBACKS") as caught,
        ):
            keys.current()
        assert "shorter than 32" in str(caught.value)
        assert secret not in str(caught.value)

    def test_a_list_of_keys_is_held(self) -> None:
        assert KeyRing([KEY, "o" * 32]).current() == (KEY, "o" * 32)

    @pytest.mark.parametrize("keys", [[], ["short"], [KEY, 32], "x"])
    def test_a_short_or_empty_list_is_refused(self, keys) -> None:
        with pytest.raises(ImproperlyConfigured):
            KeyRing(keys)

    def test_a_callable_is_asked_once_a_minute_and_checked_each_time(
        self, answers, clock: FrozenClock
    ) -> None:
        keys = KeyRing(answers.keys_path)
        answers.keys = [KEY]
        assert keys.current() == (KEY,)
        answers.keys = [OLD_KEY]
        for _read in range(100):
            assert keys.current() == (KEY,)
        assert answers.asked == 1
        clock.advance(REFRESH)
        assert keys.current() == (OLD_KEY,)
        assert answers.asked == 2
        clock.advance(REFRESH)
        answers.keys = ["short"]
        with pytest.raises(ImproperlyConfigured, match="shorter than 32") as caught:
            keys.current()
        assert "short'" not in str(caught.value)
        answers.keys = None
        with pytest.raises(ImproperlyConfigured, match="no list of keys"):
            keys.current()
        answers.keys = [KEY]
        assert keys.current() == (KEY,)

    def test_a_clock_set_back_refreshes_the_ring(
        self, answers, clock: FrozenClock
    ) -> None:
        keys = KeyRing(answers.keys_path)
        answers.keys = [KEY]
        assert keys.current() == (KEY,)
        answers.keys = [OLD_KEY]
        clock.at = NOW - 1
        assert keys.current() == (OLD_KEY,)

    def test_a_ring_another_thread_refreshed_is_not_asked_again(
        self, answers, clock: FrozenClock
    ) -> None:
        keys = KeyRing(answers.keys_path)
        answers.keys = [KEY]
        keys.current()
        answers.asked = 0
        keys._lock = _Racing(keys, (OLD_KEY,))
        clock.at = NOW + 1
        keys._held = type(keys._held)((KEY,), NOW, NOW)
        assert keys.current() == (OLD_KEY,)
        assert answers.asked == 0

    @pytest.mark.parametrize(
        ("path", "words"),
        [
            ("nowhere.ring", "does not import"),
            ("tests.support.tokens.NOW", "no callable"),
        ],
    )
    def test_a_path_that_names_no_callable_is_refused(self, path, words) -> None:
        with pytest.raises(ImproperlyConfigured, match=words):
            KeyRing(path)
