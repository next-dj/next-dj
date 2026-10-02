from __future__ import annotations

import types
from pathlib import Path

import pytest
from django.test import RequestFactory

from next.seo import RobotsRule
from next.seo.robots import DeclaredRobots, TextFile, render_robots
from next.seo.views import robots_view
from tests.support import WITH_BASE, routed, write_tree


AGENTS = (
    "GPTBot",
    "ClaudeBot",
    "Google-Extended",
    "Applebot-Extended",
    "Meta-ExternalAgent",
    "CCBot",
    "Bytespider",
    "OAI-SearchBot",
    "Claude-SearchBot",
    "PerplexityBot",
    "ChatGPT-User",
    "Claude-User",
    "Perplexity-User",
    "Meta-ExternalFetcher",
)
RULES = tuple(
    RobotsRule(user_agent=agent, disallow=("/private/", "/search/*?q="))
    for agent in AGENTS
)


class TestBenchRobots:
    """Rendering `/robots.txt` from declared groups or a static file."""

    @pytest.mark.benchmark(group="seo.robots")
    def test_render_every_ai_group(self, benchmark) -> None:
        sitemaps = ("https://acme.example/sitemap.xml",)
        assert render_robots(RULES, sitemaps).count("User-agent:") == len(AGENTS)
        benchmark(render_robots, RULES, sitemaps)

    @pytest.mark.benchmark(group="seo.robots")
    def test_rules_of_a_declared_module(self, benchmark) -> None:
        module = types.ModuleType("robots")
        module.rules = list(RULES)
        robots = DeclaredRobots(Path("robots.py"), module)
        benchmark(robots.render, None, "https://acme.example/sitemap.xml")

    @pytest.mark.benchmark(group="seo.robots")
    def test_static_file_read_on_a_warm_memo(self, tmp_path: Path, benchmark) -> None:
        path = tmp_path / "robots.txt"
        path.write_bytes(b"User-agent: *\nDisallow:\n")
        source = TextFile(path)
        assert source.read() is not None
        benchmark(source.read)

    @pytest.mark.benchmark(group="seo.robots")
    def test_robots_view(self, tmp_path: Path, benchmark) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        request = RequestFactory().get("/robots.txt")
        with routed(root, **WITH_BASE):
            assert robots_view(request).status_code == 200
            benchmark(robots_view, request)
