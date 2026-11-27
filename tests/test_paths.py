from collections.abc import Iterator
from unittest.mock import patch
from urllib.parse import quote, unquote

import pytest
from django.test import override_settings
from django.test.utils import override_script_prefix
from django.urls import set_urlconf

from next import paths
from next.paths import (
    ANCESTOR_MARK,
    PAGE_MARK,
    Homes,
    home_paths,
    normal_path,
    path_mark,
)
from tests.support import (
    I18N,
    I18N_PREFIXED_URLCONF,
    I18N_ROUTED,
    I18N_URLCONF,
    NAMESPACED_URLCONF,
)


def _unread() -> frozenset[str]:
    return pytest.fail("the homes were read")


@pytest.fixture()
def mounted() -> Iterator[None]:
    with override_script_prefix("/app/"):
        yield


@pytest.fixture()
def request_urlconf() -> Iterator[None]:
    yield
    set_urlconf(None)


class TestNormalPath:
    """Two spellings of one path normalise to the same string."""

    @pytest.mark.parametrize(
        ("location", "expected"),
        [
            ("/", "/"),
            ("/docs/", "/docs"),
            ("/docs", "/docs"),
            ("/docs/./intro/../", "/docs"),
            ("/../..", "/"),
            ("/blog/caf%c3%a9/", "/blog/caf%C3%A9"),
            ("/blog/café/", "/blog/caf%C3%A9"),
            ("/a%3Db%3Bc/", "/a=b;c"),
            ("/a=b;c", "/a=b;c"),
            ("/a%20b", "/a%20b"),
        ],
    )
    def test_normalised(self, location, expected) -> None:
        assert normal_path(location) == expected

    @pytest.mark.parametrize(
        "segment", ["docs", "a=b;c", "v1.2~x_y-z", "(site)", "a:b@c", "!$&'*+,"]
    )
    def test_a_segment_no_encoding_touches_is_not_encoded_again(self, segment) -> None:
        with patch("next.paths.quote") as quoted:
            assert normal_path(f"/{segment}/") == f"/{segment}"
        quoted.assert_not_called()

    @pytest.mark.parametrize("char", [chr(code) for code in range(1, 256)])
    def test_every_character_normalises_as_its_encoding_does(self, char) -> None:
        segment = f"a{char}b"
        expected = quote(unquote(segment), safe="!$&'()*+,;=:@")
        if char == "/":
            expected = "a/b"
        assert normal_path(f"/{segment}") == f"/{expected}"


class TestPathMark:
    """Equal paths are the page, a segment prefix an ancestor unless it is a home."""

    @pytest.mark.parametrize(
        ("link", "current", "expected"),
        [
            ("/docs/intro", "/docs/intro", PAGE_MARK),
            ("/", "/", PAGE_MARK),
            ("/de", "/de", PAGE_MARK),
            ("/", "/docs", ""),
            ("/doc", "/docs/intro", ""),
            ("/docs/intro/more", "/docs/intro", ""),
        ],
    )
    def test_the_homes_stay_unread(self, link, current, expected) -> None:
        assert path_mark(link, current, _unread) == expected

    def test_an_ancestor(self) -> None:
        assert path_mark("/docs", "/docs/intro", frozenset) == ANCESTOR_MARK

    def test_a_home_is_exact_only(self) -> None:
        assert path_mark("/de", "/de/blog", lambda: frozenset({"/de"})) == ""

    def test_exact_drops_the_ancestor(self) -> None:
        assert path_mark("/docs", "/docs/intro", _unread, exact=True) == ""
        assert path_mark("/docs", "/docs", _unread, exact=True) == PAGE_MARK


class TestHomePaths:
    """The homes are `/`, the script prefix and each root `i18n_patterns()` serves."""

    @override_settings(ROOT_URLCONF=NAMESPACED_URLCONF)
    def test_a_plain_project(self) -> None:
        assert home_paths() == {"/"}

    @override_settings(ROOT_URLCONF=NAMESPACED_URLCONF)
    @pytest.mark.usefixtures("mounted")
    def test_a_mounted_project(self) -> None:
        assert home_paths() == {"/", "/app"}

    @override_settings(**I18N_ROUTED)
    def test_the_unprefixed_default_language_adds_no_root(self) -> None:
        assert home_paths() == {"/", "/de"}

    @override_settings(**I18N, ROOT_URLCONF=I18N_PREFIXED_URLCONF)
    def test_a_prefixed_default_language(self) -> None:
        assert home_paths() == {"/", "/en", "/de"}

    @override_settings(**I18N, ROOT_URLCONF=I18N_PREFIXED_URLCONF)
    @pytest.mark.usefixtures("mounted")
    def test_a_mounted_i18n_project(self) -> None:
        assert home_paths() == {"/", "/app", "/app/en", "/app/de"}

    @override_settings(
        ROOT_URLCONF=I18N_URLCONF,
        LANGUAGE_CODE="en",
        LANGUAGES=[("en", "English"), ("pt-BR", "Portuguese")],
    )
    def test_a_root_is_spelled_as_get_language_spells_it(self) -> None:
        assert home_paths() == {"/", "/pt-br"}

    @override_settings(**I18N, ROOT_URLCONF=NAMESPACED_URLCONF)
    @pytest.mark.usefixtures("request_urlconf")
    def test_the_urlconf_of_the_request_wins(self) -> None:
        set_urlconf(I18N_PREFIXED_URLCONF)
        assert home_paths() == {"/", "/en", "/de"}


class TestHomes:
    """One pass over several links reads the home paths when a link first asks."""

    def test_the_paths_are_read_once_for_the_pass(self) -> None:
        homes = Homes()
        with patch("next.paths.home_paths", return_value=frozenset({"/"})) as read:
            assert homes() == {"/"}
            assert homes() == {"/"}
        read.assert_called_once_with()

    def test_another_pass_reads_them_again(self) -> None:
        answers = [frozenset({"/"}), frozenset({"/de"})]
        with patch("next.paths.home_paths", side_effect=answers):
            assert Homes()() == {"/"}
            assert Homes()() == {"/de"}


class TestNormalPathMemo:
    """A settled path is returned untouched and a recoded segment is kept, bounded."""

    @pytest.mark.parametrize("location", ["/", "/docs", "/docs/intro", "/a=b;c/..b"])
    def test_a_settled_path_comes_back_as_the_object_passed(self, location) -> None:
        assert normal_path(location) is location

    @pytest.mark.parametrize(
        ("location", "expected"),
        [
            ("", "/"),
            ("docs/intro", "/intro"),
            ("/a//b/", "/a//b"),
            ("/a//", "/a/"),
            ("/docs/.", "/docs"),
            ("/docs/..", "/"),
            ("/docs/...", "/docs/..."),
            ("/docs/.x/", "/docs/.x"),
        ],
    )
    def test_an_unsettled_shape_takes_the_full_rule(self, location, expected) -> None:
        assert normal_path(location) == expected

    def test_a_recoded_segment_is_encoded_once(self) -> None:
        paths._recoded.cache_clear()
        for _ in range(3):
            assert normal_path("/jos%c3%a9/") == "/jos%C3%A9"
        info = paths._recoded.cache_info()
        assert (info.misses, info.hits) == (1, 2)

    def test_the_recoded_segments_stay_bounded(self) -> None:
        paths._recoded.cache_clear()
        bound = paths._recoded.cache_info().maxsize
        assert bound == paths._SEGMENT_MEMO
        for index in range(bound + 8):
            normal_path(f"/%7b{index}")
        assert paths._recoded.cache_info().currsize == bound

    def test_a_segment_past_the_limit_is_never_kept(self) -> None:
        paths._recoded.cache_clear()
        segment = "%7b" * paths._SEGMENT_LIMIT
        assert normal_path(f"/{segment}") == "/" + "%7B" * paths._SEGMENT_LIMIT
        assert paths._recoded.cache_info().currsize == 0
