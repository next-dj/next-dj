from __future__ import annotations

import pytest
from django.template import Context, Template, engines

from next.static.collector import StaticCollector


# Touching the engines triggers the global init that registers the next tags,
# so warm it up here and keep the bench body on render cost alone.
engines.all()


class TestBenchStaticTags:
    @pytest.mark.benchmark(group="templatetags.static")
    def test_use_script_dedup(self, benchmark) -> None:
        """``{% use_script %}`` + dedup — 3 identical URLs, one dedups twice."""
        template = Template(
            "{% load next_static %}"
            "{% use_script 'https://cdn.example.com/a.js' %}"
            "{% use_script 'https://cdn.example.com/a.js' %}"
            "{% use_script 'https://cdn.example.com/a.js' %}"
        )

        def run() -> str:
            collector = StaticCollector()
            ctx = Context({"_static_collector": collector})
            return template.render(ctx)

        benchmark(run)

    @pytest.mark.benchmark(group="templatetags.static")
    def test_inline_script_block(self, benchmark) -> None:
        """``{% #use_script %}`` inline — body rendered, stripped, deduped."""
        template = Template(
            "{% load next_static %}{% #use_script %}console.log('hi');{% /use_script %}"
        )

        def run() -> str:
            collector = StaticCollector()
            ctx = Context({"_static_collector": collector})
            return template.render(ctx)

        benchmark(run)

    @pytest.mark.benchmark(group="templatetags.static")
    def test_collect_placeholders(self, benchmark) -> None:
        """``{% collect_styles %}`` / ``{% collect_scripts %}`` — placeholder tags."""
        template = Template(
            "{% load next_static %}"
            "<head>{% collect_styles %}</head>"
            "<body>{% collect_scripts %}</body>"
        )

        def run() -> str:
            ctx = Context({})
            return template.render(ctx)

        benchmark(run)
