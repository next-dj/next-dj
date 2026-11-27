from __future__ import annotations

import pytest

from next.paths import ANCESTOR_MARK, PAGE_MARK, home_paths, normal_path, path_mark


_PATHS = {
    "home": "/",
    "plain": "/docs/section-2/topic-7/",
    "encoded": "/jos%C3%A9/men%c3%ba/a%20b/",
    "dotted": "/docs/section-2/../section-3/./topic-9",
}
_EXPECTED = {
    "home": "/",
    "plain": "/docs/section-2/topic-7",
    "encoded": "/jos%C3%A9/men%C3%BA/a%20b",
    "dotted": "/docs/section-3/topic-9",
}
_CURRENT = "/docs/section-2/topic-7"


class TestBenchNormalPath:
    """The path rule each link of a navigation container is compared by."""

    @pytest.mark.parametrize("shape", list(_PATHS))
    @pytest.mark.benchmark(group="paths")
    def test_normal_path(self, shape: str, benchmark) -> None:
        assert benchmark(normal_path, _PATHS[shape]) == _EXPECTED[shape]


class TestBenchPathMark:
    """The marks one link gains on the current page, by how the two paths relate."""

    @pytest.mark.parametrize(
        ("link", "mark"),
        [
            pytest.param(_CURRENT, PAGE_MARK, id="page"),
            pytest.param("/docs/section-2", ANCESTOR_MARK, id="ancestor"),
            pytest.param("/docs/section-4/topic-1", "", id="unrelated"),
            pytest.param("/", "", id="home"),
        ],
    )
    @pytest.mark.benchmark(group="paths")
    def test_path_mark(self, link: str, mark: str, benchmark) -> None:
        assert benchmark(path_mark, link, _CURRENT, home_paths) == mark

    @pytest.mark.benchmark(group="paths")
    def test_home_paths(self, benchmark) -> None:
        """The home paths an ancestor link is checked against, held per urlconf."""
        assert "/" in benchmark(home_paths)
