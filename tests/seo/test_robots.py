import os
import types
from pathlib import Path

from next.seo import Rule
from next.seo.discovery import SeoRoot, SeoSource
from next.seo.robots import (
    DEFAULT_RULES,
    RobotsFile,
    RobotsRules,
    declared_rules,
    robots_candidates,
    rules_from_module,
)
from next.utils import PageRoot


def render_rules(
    rules: tuple[Rule, ...], host: str | None, sitemap_url: str | None
) -> str:
    return RobotsRules(Path("robots.py"), rules, host).render(sitemap_url)


class TestRobotsRulesRender:
    """The rules render group by group with `Host` and `Sitemap` trailing."""

    def test_no_rules_allow_everything(self) -> None:
        assert render_rules((), None, None) == "User-agent: *\nAllow: /\n"

    def test_the_sitemap_line_trails_the_default_group(self) -> None:
        text = render_rules((), None, "https://acme.example/sitemap.xml")
        assert text == (
            "User-agent: *\nAllow: /\n\nSitemap: https://acme.example/sitemap.xml\n"
        )

    def test_a_bare_disallow_string_renders_one_line(self) -> None:
        text = render_rules((Rule(disallow="/admin/"),), None, None)
        assert text == "User-agent: *\nDisallow: /admin/\n"

    def test_groups_render_in_order_with_host_and_sitemap_last(self) -> None:
        rules = (
            Rule(user_agent="*", disallow=["/private/"], crawl_delay=10),
            Rule(user_agent=("a", "b"), allow=["/"]),
        )
        text = render_rules(rules, "acme.example", "https://acme.example/sitemap.xml")
        assert text == (
            "User-agent: *\n"
            "Disallow: /private/\n"
            "Crawl-delay: 10\n"
            "\n"
            "User-agent: a\n"
            "User-agent: b\n"
            "Allow: /\n"
            "\n"
            "Host: acme.example\n"
            "Sitemap: https://acme.example/sitemap.xml\n"
        )

    def test_host_alone_still_trails(self) -> None:
        text = render_rules((Rule(),), "acme.example", None)
        assert text == "User-agent: *\n\nHost: acme.example\n"


class TestRulesFromModule:
    """A `robots.py` yields its rules and host, a wrong shape reads as nothing."""

    def test_reads_rules_and_host(self, tmp_path) -> None:
        module = types.ModuleType("robots")
        module.rules = [Rule(disallow=["/x/"]), "not a rule"]
        module.host = "acme.example"
        source = rules_from_module(tmp_path / "robots.py", module)
        assert source == RobotsRules(
            path=tmp_path / "robots.py",
            rules=(Rule(disallow=["/x/"]),),
            host="acme.example",
        )

    def test_wrong_shapes_read_as_nothing(self, tmp_path) -> None:
        module = types.ModuleType("robots")
        module.rules = "User-agent: *"
        module.host = 5
        source = rules_from_module(tmp_path / "robots.py", module)
        assert source.rules == ()
        assert source.host is None
        assert source.render(None) == render_rules(DEFAULT_RULES, None, None)


class TestRobotsFile:
    """A static `robots.txt` is served byte for byte, re-read when its mtime moves."""

    def test_reads_the_bytes_as_they_are(self, tmp_path) -> None:
        path = tmp_path / "robots.txt"
        path.write_bytes(b"\xff\xfeUser-agent: *\r\n")
        assert RobotsFile(path).read() == b"\xff\xfeUser-agent: *\r\n"

    def test_the_memo_serves_while_the_mtime_stands(self, tmp_path) -> None:
        path = tmp_path / "robots.txt"
        path.write_bytes(b"one\n")
        source = RobotsFile(path)
        first = source.read()
        stat = path.stat()
        path.write_bytes(b"two\n")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert source.read() is first

    def test_a_moved_mtime_is_picked_up(self, tmp_path) -> None:
        path = tmp_path / "robots.txt"
        path.write_bytes(b"one\n")
        source = RobotsFile(path)
        assert source.read() == b"one\n"
        path.write_bytes(b"two\n")
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        assert source.read() == b"two\n"

    def test_a_vanished_file_answers_none_and_drops_the_memo(self, tmp_path) -> None:
        path = tmp_path / "robots.txt"
        path.write_bytes(b"one\n")
        stat = path.stat()
        source = RobotsFile(path)
        source.read()
        path.unlink()
        assert source.read() is None
        path.write_bytes(b"two\n")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert source.read() == b"two\n"

    def test_carries_no_instance_dictionary(self) -> None:
        assert not hasattr(RobotsFile(Path("/x")), "__dict__")


def _seo_root(
    path: Path, *, robots: SeoSource | None = None, robots_file: Path | None = None
) -> SeoRoot:
    return SeoRoot(
        root=PageRoot(path=path, label="Root"),
        label=path.name,
        section=path.name,
        sitemap=None,
        robots=robots,
        robots_file=robots_file,
    )


class TestDeclaredRules:
    """Only the `Rule` groups of a sequence count as declared rules."""

    def test_only_rule_groups_are_read(self) -> None:
        rule = Rule(disallow=("/x/",))
        module = types.ModuleType("robots")
        module.rules = [rule, "nope"]
        assert declared_rules(module) == (rule,)

    def test_anything_but_a_sequence_reads_as_none(self) -> None:
        module = types.ModuleType("robots")
        module.rules = "nope"
        assert declared_rules(module) == ()


class TestRobotsCandidates:
    """Each source pairs with what it serves, a `robots.py` ahead of its file."""

    def test_robots_py_precedes_the_file_and_trees_keep_their_order(
        self, tmp_path
    ) -> None:
        module = types.ModuleType("robots")
        first = _seo_root(
            tmp_path / "a",
            robots=SeoSource(tmp_path / "a" / "robots.py", module, None),
            robots_file=tmp_path / "a" / "robots.txt",
        )
        second = _seo_root(tmp_path / "b", robots_file=tmp_path / "b" / "robots.txt")
        candidates = robots_candidates((first, second))
        assert [path for path, _served in candidates] == [
            tmp_path / "a" / "robots.py",
            tmp_path / "a" / "robots.txt",
            tmp_path / "b" / "robots.txt",
        ]
        assert isinstance(candidates[0][1], RobotsRules)
        assert isinstance(candidates[2][1], RobotsFile)

    def test_a_robots_py_that_failed_to_import_serves_nothing(self, tmp_path) -> None:
        root = _seo_root(tmp_path, robots=SeoSource(tmp_path / "robots.py", None, None))
        assert robots_candidates((root,)) == ((tmp_path / "robots.py", None),)
