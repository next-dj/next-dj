import pytest

from next.testing.html import assert_has_class, assert_missing_class, find_anchor


class TestFindAnchor:
    """`find_anchor` returns the first matching `<a>...</a>` substring."""

    def test_matches_by_href_and_text(self) -> None:
        html = (
            '<nav><a href="/a" class="x">Home</a><a href="/b" class="y">Docs</a></nav>'
        )
        assert find_anchor(html, href="/b", text="Docs") == (
            '<a href="/b" class="y">Docs</a>'
        )

    def test_matches_by_href_only(self) -> None:
        html = '<a href="/x">one</a><a href="/y">two</a>'
        assert find_anchor(html, href="/y") == '<a href="/y">two</a>'

    def test_matches_by_text_only(self) -> None:
        html = '<a href="/x">Alpha</a><a href="/y">Beta</a>'
        assert find_anchor(html, text="Beta") == '<a href="/y">Beta</a>'

    def test_returns_first_when_no_filters(self) -> None:
        html = '<a href="/x">one</a><a href="/y">two</a>'
        assert find_anchor(html) == '<a href="/x">one</a>'

    def test_single_quotes(self) -> None:
        html = "<a href='/q' class='z'>Quoted</a>"
        assert find_anchor(html, href="/q", text="Quoted") == (
            "<a href='/q' class='z'>Quoted</a>"
        )

    def test_nested_text_elements(self) -> None:
        html = '<a href="/n"><span class="icon"></span> Stats</a>'
        assert find_anchor(html, text="Stats") == (
            '<a href="/n"><span class="icon"></span> Stats</a>'
        )

    def test_text_is_substring_match(self) -> None:
        html = '<a href="/x">Hello, world</a>'
        assert find_anchor(html, text="world") == '<a href="/x">Hello, world</a>'

    def test_raises_when_nothing_matches_href(self) -> None:
        html = '<a href="/x">one</a>'
        with pytest.raises(LookupError, match=r"href='/missing'"):
            find_anchor(html, href="/missing")

    def test_raises_when_nothing_matches_text(self) -> None:
        html = '<a href="/x">one</a>'
        with pytest.raises(LookupError, match=r"text='nope'"):
            find_anchor(html, text="nope")

    def test_raises_when_no_anchors_at_all(self) -> None:
        with pytest.raises(LookupError):
            find_anchor("<div>no anchors here</div>", href="/x")


class TestAssertHasClass:
    """`assert_has_class` checks whitespace-separated class tokens."""

    def test_single_class(self) -> None:
        assert_has_class('<a class="alpha">x</a>', "alpha")

    def test_multi_class(self) -> None:
        assert_has_class('<a class="alpha beta gamma">x</a>', "beta")

    def test_missing_token_raises(self) -> None:
        with pytest.raises(AssertionError, match="alpha"):
            assert_has_class('<a class="beta">x</a>', "alpha")

    def test_no_class_attr_raises(self) -> None:
        with pytest.raises(AssertionError, match="no classes"):
            assert_has_class('<a href="/x">x</a>', "alpha")

    def test_empty_class_attr_raises(self) -> None:
        with pytest.raises(AssertionError):
            assert_has_class('<a class="">x</a>', "alpha")

    def test_raises_on_fragment_without_tag(self) -> None:
        with pytest.raises(LookupError):
            assert_has_class("just text", "alpha")


class TestAssertMissingClass:
    """`assert_missing_class` is the inverse check."""

    def test_token_absent(self) -> None:
        assert_missing_class('<a class="alpha beta">x</a>', "gamma")

    def test_no_class_attr(self) -> None:
        assert_missing_class('<a href="/x">x</a>', "alpha")

    def test_token_present_raises(self) -> None:
        with pytest.raises(AssertionError, match="alpha"):
            assert_missing_class('<a class="alpha beta">x</a>', "alpha")
