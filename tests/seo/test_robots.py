import errno
import logging
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from django.http import HttpRequest
from django.test import RequestFactory

from next.pages.responses import NO_STORE, cache_control
from next.seo import RobotsRule
from next.seo.discovery import BrokenSource, SeoRoot, SeoSource
from next.seo.robots import (
    DeclaredRobots,
    TextFile,
    declared_cache,
    declared_rules,
    is_sitemap_url,
    render_group,
    render_robots,
    robots_candidates,
    rule_pattern,
    rules_of,
)
from next.utils import PageRoot
from tests.support import touch_later


def _module(**attributes: object) -> types.ModuleType:
    module = types.ModuleType("robots")
    for name, value in attributes.items():
        setattr(module, name, value)
    return module


class TestRender:
    """Groups render in order, an empty one allowing everything, sitemaps last."""

    def test_no_rules_allow_everything_explicitly(self) -> None:
        assert render_robots((), ()) == "User-agent: *\nDisallow:\n"

    def test_an_empty_group_before_another_keeps_its_own_rule(self) -> None:
        text = render_robots(
            (RobotsRule(), RobotsRule(user_agent="BadBot", disallow="/")), ()
        )
        assert text == ("User-agent: *\nDisallow:\n\nUser-agent: BadBot\nDisallow: /\n")

    def test_groups_render_in_order_with_every_sitemap_last(self) -> None:
        rules = (
            RobotsRule(user_agent="*", disallow=["/private/"], crawl_delay=10),
            RobotsRule(user_agent=("a", "b"), allow=["/"], crawl_delay=0.5),
        )
        text = render_robots(
            rules, ("https://acme.example/sitemap.xml", "https://cdn.example/news.xml")
        )
        assert text == (
            "User-agent: *\n"
            "Disallow: /private/\n"
            "Crawl-delay: 10\n"
            "\n"
            "User-agent: a\n"
            "User-agent: b\n"
            "Allow: /\n"
            "Crawl-delay: 0.5\n"
            "\n"
            "Sitemap: https://acme.example/sitemap.xml\n"
            "Sitemap: https://cdn.example/news.xml\n"
        )

    def test_an_allow_alone_needs_no_empty_disallow(self) -> None:
        assert render_group(RobotsRule(allow="/")) == "User-agent: *\nAllow: /"


class TestDeclaredRules:
    """A `robots.py` lists its groups or resolves them per request."""

    def test_a_list_keeps_only_the_groups(self) -> None:
        rule = RobotsRule(disallow="/x/")
        assert rules_of([rule, "junk"]) == (rule,)
        assert rules_of("junk") == ()
        assert declared_rules(_module(rules=(rule,))) == (rule,)
        assert declared_rules(_module()) == ()

    def test_a_callable_is_left_to_the_request(self) -> None:
        assert declared_rules(_module(rules=list)) == ()

    def test_a_callable_takes_the_request_through_the_resolver(self) -> None:
        def rules(request: HttpRequest) -> list[RobotsRule]:
            return [
                RobotsRule(user_agent="Bingbot", disallow=f"/{request.get_host()}/")
            ]

        robots = DeclaredRobots(Path("robots.py"), _module(rules=rules))
        request = RequestFactory().get("/robots.txt", HTTP_HOST="testserver")
        assert robots.rules(request) == (
            RobotsRule(user_agent="Bingbot", disallow="/testserver/"),
        )

    def test_the_render_puts_the_own_sitemap_first(self) -> None:
        module = _module(
            rules=[RobotsRule(disallow="/x/")],
            sitemaps=["https://cdn.example/news.xml", "not a url"],
        )
        robots = DeclaredRobots(Path("robots.py"), module)
        assert robots.sitemaps == ("https://cdn.example/news.xml",)
        assert robots.render(None, "https://acme.example/sitemap.xml").endswith(
            "Sitemap: https://acme.example/sitemap.xml\n"
            "Sitemap: https://cdn.example/news.xml\n"
        )
        assert "Sitemap: https://acme" not in robots.render(None, None)

    def test_sitemaps_of_the_wrong_shape_read_as_none(self) -> None:
        robots = DeclaredRobots(
            Path("robots.py"), _module(sitemaps="https://x.example")
        )
        assert robots.sitemaps == ()

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (3600, cache_control(3600)),
            (0, cache_control(0)),
            (False, NO_STORE),
            ({"s_maxage": 60}, cache_control({"s_maxage": 60})),
            (-1, None),
            (True, None),
            ("60", None),
            (lambda: 60, None),
        ],
    )
    def test_the_cache_reads_every_static_page_form(self, value, expected) -> None:
        assert declared_cache(_module(cache=value)) == expected
        assert DeclaredRobots(Path("r.py"), _module(cache=value)).cache == expected

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://acme.example/sitemap.xml", True),
            ("http://acme.example/s.xml", True),
            ("/sitemap.xml", False),
            ("ftp://acme.example/s.xml", False),
            ("https://acme.example/s.xml\nUser-agent: evil", False),
            ("https:///s.xml", False),
            (3, False),
        ],
    )
    def test_a_sitemap_line_is_an_absolute_http_url(self, url, expected) -> None:
        assert is_sitemap_url(url) is expected


class TestTextFile:
    """A static file is served byte for byte, re-read on a new mtime."""

    def test_a_rewrite_is_picked_up_on_a_new_mtime(self, tmp_path) -> None:
        path = tmp_path / "robots.txt"
        path.write_bytes(b"one\n")
        source = TextFile(path)
        assert source.read() == b"one\n"
        path.write_bytes(b"two\n")
        touch_later(path)
        assert source.read() == b"two\n"

    def test_the_memo_answers_while_the_mtime_stands(self, tmp_path) -> None:
        path = tmp_path / "robots.txt"
        path.write_bytes(b"one\n")
        source = TextFile(path)
        source.read()
        with patch.object(Path, "read_bytes", side_effect=AssertionError):
            assert source.read() == b"one\n"

    def test_a_gone_file_answers_none(self, tmp_path) -> None:
        path = tmp_path / "robots.txt"
        path.write_bytes(b"one\n")
        source = TextFile(path)
        source.read()
        path.unlink()
        assert source.read() is None

    def test_a_failed_read_answers_the_last_good_bytes(self, tmp_path, caplog) -> None:
        path = tmp_path / "robots.txt"
        path.write_bytes(b"one\n")
        source = TextFile(path)
        source.read()
        touch_later(path)
        denied = PermissionError(errno.EACCES, "denied")
        with (
            patch.object(Path, "read_bytes", side_effect=denied),
            caplog.at_level(logging.ERROR, logger="next.seo"),
        ):
            assert source.read() == b"one\n"
        assert "the last good copy answers" in caplog.text

    def test_a_failed_first_read_raises(self, tmp_path) -> None:
        path = tmp_path / "robots.txt"
        path.write_bytes(b"one\n")
        denied = PermissionError(errno.EACCES, "denied")
        with (
            patch.object(Path, "read_bytes", side_effect=denied),
            pytest.raises(PermissionError),
        ):
            TextFile(path).read()


def _root(path: Path, **sources: object) -> SeoRoot:
    return SeoRoot(
        root=PageRoot(path, "Root"), label="p", section="p", trails={}, **sources
    )


class TestCandidates:
    """The sources pair with what they serve, in the order the routes prefer them."""

    def test_a_robots_py_precedes_its_robots_txt(self, tmp_path) -> None:
        module = _module()
        root = _root(
            tmp_path,
            robots=SeoSource(tmp_path / "robots.py", module, None),
            robots_file=tmp_path / "robots.txt",
        )
        [(first, declared), (second, static)] = robots_candidates((root,))
        assert (first, second) == (tmp_path / "robots.py", tmp_path / "robots.txt")
        assert isinstance(declared, DeclaredRobots)
        assert declared.module is module
        assert isinstance(static, TextFile)

    def test_a_failed_robots_py_holds_its_route(self, tmp_path) -> None:
        path = tmp_path / "robots.py"
        root = _root(tmp_path, robots=SeoSource(path, None, None))
        assert robots_candidates((root,)) == ((path, BrokenSource(path)),)


class TestRulePattern:
    """A robots path matches like a crawler, `*` anywhere and `$` at the end."""

    @pytest.mark.parametrize(
        ("rule", "path", "matches"),
        [
            ("/private/", "/private/x", True),
            ("/private/", "/public/", False),
            ("/*.pdf$", "/a/b.pdf", True),
            ("/*.pdf$", "/a/b.pdf?x", False),
            ("*", "/anything", True),
        ],
    )
    def test_matches_like_a_crawler(self, rule, path, matches) -> None:
        assert (rule_pattern(rule).match(path) is not None) is matches
