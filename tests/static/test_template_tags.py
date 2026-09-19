from __future__ import annotations

import pytest
from django.template import Context, Template, TemplateSyntaxError
from django.test import RequestFactory, override_settings

from next.static import StaticAsset, StaticCollector
from next.static.manager import default_manager
from tests.support import (
    EMPTY_ASSET_CASES,
    PREFIXED_BACKENDS,
    STATIC_NAME_CASES,
    EmptyAssetCase,
    static_names_resolved_by,
)


STYLES_PLACEHOLDER = "<!-- next:styles -->"
SCRIPTS_PLACEHOLDER = "<!-- next:scripts -->"

PASSTHROUGH_CASES = [c for c in STATIC_NAME_CASES if not c.expected and c.reference]
PASSTHROUGH = pytest.mark.parametrize(
    "reference", [pytest.param(c.reference, id=c.id) for c in PASSTHROUGH_CASES]
)


def _render(
    source: str, context_data: dict | None = None
) -> tuple[str, StaticCollector]:
    collector = StaticCollector()
    template = Template(source)
    ctx = Context(context_data or {})
    ctx["_static_collector"] = collector
    return template.render(ctx), collector


class TestCollectPlaceholderTags:
    def test_collect_styles_emits_placeholder(self) -> None:
        out, _ = _render("{% load next_static %}{% collect_styles %}")
        assert out == STYLES_PLACEHOLDER

    def test_collect_scripts_emits_placeholder(self) -> None:
        out, _ = _render("{% load next_static %}{% collect_scripts %}")
        assert out == SCRIPTS_PLACEHOLDER


class TestUseStyleScriptInlineTags:
    def test_use_style_registers_and_emits_nothing(self) -> None:
        out, coll = _render('{% load next_static %}{% use_style "https://cdn/a.css" %}')
        assert out == ""
        assert [a.url for a in coll.assets_in_slot("styles")] == ["https://cdn/a.css"]

    def test_use_script_registers_and_emits_nothing(self) -> None:
        out, coll = _render('{% load next_static %}{% use_script "https://cdn/a.js" %}')
        assert out == ""
        assert [a.url for a in coll.assets_in_slot("scripts")] == ["https://cdn/a.js"]

    def test_empty_url_is_ignored(self) -> None:
        out, coll = _render('{% load next_static %}{% use_style "" %}')
        assert out == ""
        assert coll.assets_in_slot("styles") == ()

    def test_no_collector_in_context_is_no_op(self) -> None:
        template = Template('{% load next_static %}{% use_style "https://cdn/a.css" %}')
        result = template.render(Context({}))
        assert result == ""


class TestUseScriptKindArgument:
    def test_use_module_registers_the_module_kind(self) -> None:
        out, coll = _render(
            '{% load next_static %}{% use_module "https://cdn/a.mjs" %}'
        )
        assert out == ""
        scripts = coll.assets_in_slot("scripts")
        assert [(a.url, a.kind) for a in scripts] == [("https://cdn/a.mjs", "module")]

    def test_use_script_kind_argument_matches_use_module(self) -> None:
        out, coll = _render(
            '{% load next_static %}{% use_script "https://cdn/a.mjs" kind="module" %}'
        )
        assert out == ""
        scripts = coll.assets_in_slot("scripts")
        assert [(a.url, a.kind) for a in scripts] == [("https://cdn/a.mjs", "module")]

    def test_unregistered_kind_raises_out_of_the_render(self) -> None:
        with pytest.raises(KeyError, match="wasm"):
            _render('{% load next_static %}{% use_script "a.wasm" kind="wasm" %}')

    def test_one_url_under_two_kinds_is_not_deduped(self) -> None:
        _, coll = _render(
            '{% load next_static %}{% use_script "https://cdn/x.js" %}'
            '{% use_module "https://cdn/x.js" %}'
        )
        scripts = coll.assets_in_slot("scripts")
        assert [(a.url, a.kind) for a in scripts] == [
            ("https://cdn/x.js", "js"),
            ("https://cdn/x.js", "module"),
        ]

    def test_block_use_module_is_not_registered(self) -> None:
        with pytest.raises(TemplateSyntaxError, match="#use_module"):
            Template("{% load next_static %}{% #use_module %}x{% /use_module %}")


class TestBlockUseStyleScript:
    def test_inline_style_block(self) -> None:
        out, coll = _render(
            "{% load next_static %}{% #use_style %}body{color:red}{% /use_style %}"
        )
        assert out == ""
        styles = coll.assets_in_slot("styles")
        assert len(styles) == 1
        assert styles[0].inline == "body{color:red}"
        assert styles[0].url == ""

    def test_inline_script_block(self) -> None:
        out, coll = _render(
            "{% load next_static %}{% #use_script %}window.x=1;{% /use_script %}"
        )
        assert out == ""
        scripts = coll.assets_in_slot("scripts")
        assert len(scripts) == 1
        assert scripts[0].inline == "window.x=1;"

    def test_block_interpolates_context(self) -> None:
        out, coll = _render(
            "{% load next_static %}"
            "{% #use_script %}window.user = '{{ user }}';{% /use_script %}",
            {"user": "alice"},
        )
        assert out == ""
        scripts = coll.assets_in_slot("scripts")
        assert scripts[0].inline == "window.user = 'alice';"

    def test_blank_block_body_is_ignored(self) -> None:
        out, coll = _render(
            "{% load next_static %}{% #use_style %}   \n   {% /use_style %}"
        )
        assert out == ""
        assert coll.assets_in_slot("styles") == ()

    def test_block_without_collector_noop(self) -> None:
        template = Template(
            "{% load next_static %}{% #use_style %}body{}{% /use_style %}"
        )
        assert template.render(Context({})) == ""


class TestPrependOrdering:
    """The use tags insert ahead of co-located files through collector prepend."""

    def test_use_style_lands_at_front(self) -> None:
        _, coll = _render(
            '{% load next_static %}{% use_style "https://cdn/a.css" %}'
            '{% use_style "https://cdn/b.css" %}'
        )
        urls = [a.url for a in coll.assets_in_slot("styles")]
        assert urls == ["https://cdn/a.css", "https://cdn/b.css"]

    def test_use_script_and_use_module_share_one_prepend_run(self) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="https://cdn/colocated.js", kind="js"))
        template = Template(
            '{% load next_static %}{% use_script "https://cdn/a.js" %}'
            '{% use_module "https://cdn/b.mjs" %}'
        )
        template.render(Context({"_static_collector": collector}))
        urls = [a.url for a in collector.assets_in_slot("scripts")]
        assert urls == [
            "https://cdn/a.js",
            "https://cdn/b.mjs",
            "https://cdn/colocated.js",
        ]


class TestUseTagsResolveNames:
    """Every use tag hands its value to the backend before the collector sees it."""

    @pytest.mark.parametrize(
        ("tag", "slot", "reference", "expected"),
        [
            ("use_style", "styles", "css/app.css", "/static/css/app.css"),
            ("use_script", "scripts", "js/app.js", "/static/js/app.js"),
            ("use_module", "scripts", "js/app.mjs", "/static/js/app.mjs"),
        ],
    )
    def test_a_bare_name_reaches_the_collector_as_a_public_url(
        self, tag: str, slot: str, reference: str, expected: str
    ) -> None:
        _, coll = _render(
            "{% load next_static %}{% " + tag + " ref %}", {"ref": reference}
        )
        assert [a.url for a in coll.assets_in_slot(slot)] == [expected]

    @PASSTHROUGH
    def test_a_ready_url_round_trips_byte_identical(self, reference: str) -> None:
        _, coll = _render(
            "{% load next_static %}{% use_style ref %}", {"ref": reference}
        )
        assert [a.url for a in coll.assets_in_slot("styles")] == [reference]

    def test_an_empty_value_never_reaches_the_backend(self) -> None:
        _, coll = _render("{% load next_static %}{% use_style ref %}", {"ref": ""})
        assert coll.assets_in_slot("styles") == ()

    def test_two_spellings_of_one_asset_collapse_to_one(self) -> None:
        _, coll = _render(
            "{% load next_static %}"
            '{% use_style "css/app.css" %}'
            '{% use_style "/static/css/app.css" %}'
        )
        assert [a.url for a in coll.assets_in_slot("styles")] == ["/static/css/app.css"]


class TestAssetTag:
    """`{% asset %}` answers with a URL and registers nothing."""

    def test_a_bare_name_resolves_to_its_public_url(self) -> None:
        out, _ = _render('{% load next_static %}{% asset "css/app.css" %}')
        assert out == "/static/css/app.css"

    @PASSTHROUGH
    def test_a_ready_url_round_trips_byte_identical(self, reference: str) -> None:
        out, _ = _render("{% load next_static %}{% asset ref %}", {"ref": reference})
        assert out == reference

    def test_nothing_lands_on_the_collector(self) -> None:
        _, coll = _render('{% load next_static %}{% asset "css/app.css" %}')
        assert coll.assets_in_slot("styles") == ()
        assert coll.assets_in_slot("scripts") == ()

    def test_a_render_without_a_collector_still_answers(self) -> None:
        template = Template('{% load next_static %}{% asset "css/app.css" %}')
        assert template.render(Context({})) == "/static/css/app.css"

    def test_as_binds_the_url_to_a_variable(self) -> None:
        out, _ = _render(
            "{% load next_static %}"
            '{% asset "css/app.css" as href %}<link href="{{ href }}">'
        )
        assert out == '<link href="/static/css/app.css">'

    def test_a_per_request_backend_moves_the_value(self, reset_default: None) -> None:
        with override_settings(NEXT_FRAMEWORK=PREFIXED_BACKENDS):
            out, _ = _render(
                '{% load next_static %}{% asset "css/app.css" %}',
                {"request": RequestFactory().get("/")},
            )
        assert out == "/pfx/static/css/app.css"

    def test_the_project_version_reaches_a_tag_asset(self, reset_default: None) -> None:
        with override_settings(NEXT_FRAMEWORK={"STATIC_VERSION": "2026.9.19"}):
            out, _ = _render('{% load next_static %}{% asset "css/app.css" %}')
        assert out == "/static/css/app.css?v=2026.9.19"

    def test_a_per_call_version_wins_over_the_project_value(
        self, reset_default: None
    ) -> None:
        with override_settings(NEXT_FRAMEWORK={"STATIC_VERSION": "2026.9.19"}):
            out, _ = _render(
                '{% load next_static %}{% asset "css/app.css" version="rc1" %}'
            )
        assert out == "/static/css/app.css?v=rc1"

    def test_a_per_call_version_lands_on_a_ready_url(self) -> None:
        out, _ = _render(
            '{% load next_static %}{% asset "/static/app.css" version=build %}',
            {"build": 42},
        )
        assert out == "/static/app.css?v=42"


class TestAssetTagRefusesAnEmptyReference:
    """An unset or non-string reference renders nothing and never reaches a backend."""

    @pytest.mark.parametrize("case", EMPTY_ASSET_CASES, ids=lambda case: case.id)
    def test_a_refused_reference_renders_nothing(self, case: EmptyAssetCase) -> None:
        out, _ = _render(
            "{% load next_static %}{% asset ref %}", {"ref": case.reference}
        )
        assert out == ""

    def test_an_unset_variable_renders_nothing(self) -> None:
        out, _ = _render('{% load next_static %}<link href="{% asset missing %}">')
        assert out == '<link href="">'

    def test_a_project_version_never_lands_on_an_unset_variable(
        self, reset_default: None
    ) -> None:
        with override_settings(NEXT_FRAMEWORK={"STATIC_VERSION": "2026.9.19"}):
            out, _ = _render('{% load next_static %}<link href="{% asset missing %}">')
        assert out == '<link href="">'
        assert "?v=" not in out

    def test_the_guard_runs_before_the_backend(self, reset_default: None) -> None:
        with static_names_resolved_by({"css/app.css": "/static/css/app.css"}) as url:
            refused, _ = _render('{% load next_static %}{% asset "" %}')
            assert refused == ""
            url.assert_not_called()
            _render('{% load next_static %}{% asset "css/app.css" %}')
            assert url.call_count == 1

    def test_as_binds_an_empty_string(self) -> None:
        out, _ = _render(
            '{% load next_static %}{% asset "" as href %}<link href="{{ href }}">'
        )
        assert out == '<link href="">'


class TestAssetTagPipedIntoAUseTag:
    """A resolved URL bound by `{% asset %}` keeps one version through injection."""

    def test_the_version_is_stamped_once_on_the_injected_tag(
        self, reset_default: None
    ) -> None:
        with override_settings(NEXT_FRAMEWORK={"STATIC_VERSION": "2026.9.19"}):
            _, collector = _render(
                "{% load next_static %}"
                '{% asset "css/app.css" as href %}{% use_style href %}'
            )
            injected = default_manager.inject(
                f"<head>{STYLES_PLACEHOLDER}</head>", collector
            )

        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/css/app.css?v=2026.9.19"
        ]
        assert '<link rel="stylesheet" href="/static/css/app.css?v=2026.9.19">' in (
            injected
        )
        assert "v=2026.9.19&v=" not in injected
