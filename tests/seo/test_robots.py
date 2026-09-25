import os
import types
from pathlib import Path

from next.seo import Rule
from next.seo.robots import (
    DEFAULT_RULES,
    RobotsFile,
    RobotsRules,
    render_rules,
    rules_from_module,
)


class TestRenderRules:
    def test_no_rules_allow_everything(self) -> None:
        assert render_rules((), None, None) == "User-agent: *\nAllow: /\n"

    def test_the_sitemap_line_trails_the_default_group(self) -> None:
        text = render_rules((), None, "https://acme.example/sitemap.xml")
        assert text == (
            "User-agent: *\nAllow: /\n\nSitemap: https://acme.example/sitemap.xml\n"
        )

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
        source = RobotsFile(path)
        source.read()
        path.unlink()
        assert source.read() is None
        assert source._held is None

    def test_carries_no_instance_dictionary(self) -> None:
        assert not hasattr(RobotsFile(Path("/x")), "__dict__")
