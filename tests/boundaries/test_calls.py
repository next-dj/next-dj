from next.boundaries.calls import DEFERRALS_KEY, stable_key


class TestDeferralsKey:
    def test_the_sink_has_its_own_context_key(self) -> None:
        assert DEFERRALS_KEY == "_next_deferrals"


class TestStableKey:
    """The key is eight hex characters of the site, the same in every process."""

    def test_the_key_names_the_site(self) -> None:
        key = stable_key("page.html", 0)
        assert len(key) == 8
        assert key == stable_key("page.html", 0)
        assert key != stable_key("page.html", 1)
        assert key != stable_key("other.html", 0)
        assert stable_key("page.html", "0.1") != key
