import json
import re
from pathlib import Path
from unittest import mock

import pytest
from django.template import Context, Template
from django.test import RequestFactory

from next.paths import ANCESTOR_MARK, PAGE_MARK
from next.router.digest import normalise
from next.router.links import TemplateLinks
from next.router.manager import client_router
from next.router.navigation import foresee
from next.views.containers import compile_containers
from next.views.fixed import SlotView, fixed_digests, slot_view
from tests.support import routed
from tests.support.navigation import (
    declared_by,
    envelope_json,
    navigate,
    write_layout_page,
)


ROOT = "a" * 16
INNER = "b" * 16
STATIC = (
    "{% load next_static pages %}<html><head>{% collect_styles %}{% metadata %}</head>"
    "<body><nav>x</nav>{% next:slot " + ROOT + " %}<div>"
    "{% next:slot " + INNER + " %}{% #template %}{% /template %}{% /next:slot %}</div>"
    "{% /next:slot %}{% collect_scripts %}</body></html>"
)


class TestFixedDigests:
    """A layout written as fixed markup takes its digest from its id, once."""

    def test_a_fixed_chain_digests_every_layout(self) -> None:
        found = fixed_digests(STATIC, [ROOT, INNER])
        root, head = found[ROOT]
        assert len(root) == 32
        assert head == root[:16]
        assert found[INNER][1] is None
        assert len(found[INNER][0]) == 16

    def test_the_digests_are_held_for_the_skeleton(self) -> None:
        assert fixed_digests(STATIC, [ROOT, INNER]) is fixed_digests(STATIC, [ROOT])

    def test_a_variable_in_the_chrome_stops_the_chain(self) -> None:
        skeleton = STATIC.replace("<div>", "<div>{{ user }}")
        assert list(fixed_digests(skeleton, [ROOT, INNER])) == [ROOT]

    def test_a_root_without_a_body_is_one_digest(self) -> None:
        skeleton = (
            "{% next:slot " + ROOT + " %}{% #template %}{% /template %}{% /next:slot %}"
        )
        assert fixed_digests(skeleton, [ROOT])[ROOT][1] is None

    def test_a_missing_slot_and_a_broken_skeleton_hold_nothing(self) -> None:
        assert fixed_digests("<p>no slot</p>", [ROOT]) == {}
        assert fixed_digests("{% if %}", [ROOT]) == {}


class TestMarksLeaveTheDigest:
    def test_the_active_link_marks_are_cut(self) -> None:
        marked = f'<nav><a href="/"{PAGE_MARK}>H</a><a href="/d/"{ANCESTOR_MARK}>D</a>'
        assert (
            normalise(marked, "response_id")
            == '<nav><a href="/">H</a><a href="/d/">D</a>'
        )


FIXED_ROOT = (
    '<!doctype html><html lang="en"><head>{% metadata %}{% collect_styles %}</head>'
    '<body><nav><a href="/sec/a/">A</a><a href="/sec/b/">B</a></nav>'
    "{% csrf_token %}{% template %}{% collect_scripts %}</body></html>"
)
FIXED_SECTION = "<section><h2>Section</h2>{% template %}</section>"
SLOT_BODY = (
    '<h1>{{ title }}</h1><nav><a href="/sec/a/">A</a></nav>'
    '{% load components %}{% component "card" %}'
)
_RESPONSE_ID = re.compile(r"[0-9a-f]{16}(?=--)|[0-9a-f]{32}")


def _fixed_tree(root: Path, *, section: str = FIXED_SECTION) -> dict[str, Path]:
    root.mkdir()
    (root / "layout.djx").write_text(FIXED_ROOT)
    folder = root / "sec"
    folder.mkdir()
    (folder / "layout.djx").write_text(section)
    card = folder / "_components" / "card"
    card.mkdir(parents=True)
    (card / "component.djx").write_text('<p class="card">card</p>')
    (card / "component.css").write_text(".card { color: red }")
    return {
        name: write_layout_page(folder / name, SLOT_BODY.replace("{{ title }}", name))
        for name in ("a", "b")
    }


def _navigation(pages: dict[str, Path], tmp_path: Path) -> dict[str, object]:
    with routed(tmp_path / "pages", ROUTER={"ENABLED": True}):
        layouts = declared_by(pages["a"])
        return envelope_json(navigate(pages["b"], layouts=layouts))


def _without_ids(found: object) -> object:
    return json.loads(_RESPONSE_ID.sub("id", json.dumps(found, sort_keys=True)))


class TestSlotView:
    """A navigation renders the kept slot alone where the chrome around it is fixed."""

    def _template(self, chrome: str) -> Template:
        return Template(
            "{% load next_static pages %}"
            + chrome.replace(
                "SLOT", "{% next:slot " + ROOT + " %}<p>{{ x }}</p>{% /next:slot %}"
            )
        )

    def test_the_slot_renders_alone_with_the_chrome_effects(self) -> None:
        template = self._template("<head>{% metadata %}</head><nav>n</nav>SLOT")
        view = slot_view(template, ROOT)
        assert view is not None
        assert slot_view(template, ROOT) is view
        assert view.render(Context({"x": "in"})) == "<p>in</p>"

    @pytest.mark.parametrize(
        "chrome",
        [
            '{% zone "z" %}<p>z</p>{% endzone %}SLOT',
            "<p>{{ user }}</p>SLOT",
            "<p>no slot</p>",
        ],
    )
    def test_a_chrome_that_writes_what_a_navigation_sends_has_none(
        self, chrome: str
    ) -> None:
        assert slot_view(self._template(chrome), ROOT) is None

    def test_an_effect_after_the_slot_runs_too(self) -> None:
        template = self._template("SLOT{% csrf_token %}")
        view = slot_view(template, ROOT)
        assert view is not None
        assert view.render(Context({"x": "in"})) == "<p>in</p>"

    def test_a_dynamic_outer_chrome_has_none(self) -> None:
        template = Template(
            "{% load next_static pages %}{% next:slot " + ROOT + " %}<p>{{ user }}</p>"
            "{% next:slot " + INNER + " %}<p>in</p>{% /next:slot %}{% /next:slot %}"
        )
        assert slot_view(template, INNER) is None

    def test_a_slot_inside_a_container_has_none(self) -> None:
        template = self._template("<nav>SLOT</nav>")
        assert compile_containers(template, TemplateLinks())
        assert slot_view(template, ROOT) is None

    def test_a_bound_template_keeps_its_binding(self) -> None:
        template = self._template("SLOT")
        view = slot_view(template, ROOT)
        assert view is not None
        context = Context({"x": "kept"})
        with context.bind_template(template):
            assert view.render(context) == "<p>kept</p>"


@pytest.mark.usefixtures("router_on")
class TestForeseenNavigation:
    """A navigation keeping fixed chrome answers as the whole render would."""

    def test_the_envelope_matches_the_whole_render(self, tmp_path: Path) -> None:
        pages = _fixed_tree(tmp_path / "pages")
        foreseen = _navigation(pages, tmp_path)
        with mock.patch("next.router.navigation.foresee", return_value=None):
            whole = _navigation(pages, tmp_path)
        assert _without_ids(foreseen) == _without_ids(whole)
        [slot] = [op for op in foreseen["ops"] if op["op"] == "morph"]
        assert slot["html"].startswith('<h1>b</h1><nav><a href="/sec/a/"')

    def test_the_slot_renders_alone(self, tmp_path: Path) -> None:
        pages = _fixed_tree(tmp_path / "pages")
        calls: list[str] = []
        original = SlotView.render

        def counted(view: SlotView, context: Context) -> str:
            html = original(view, context)
            calls.append(html)
            return html

        with mock.patch.object(SlotView, "render", counted):
            _navigation(pages, tmp_path)
        assert len(calls) == 1
        assert calls[0].startswith("<h1>b</h1>")

    def test_a_render_that_clears_the_router_loads_hard(self, tmp_path: Path) -> None:
        pages = _fixed_tree(tmp_path / "pages")
        pages["b"].write_text(
            pages["b"].read_text()
            + "from django.http import HttpRequest\n"
            + "from next.router import router_clear\n"
            + "@context('cleared')\n"
            + "def cleared(request: HttpRequest):\n"
            + "    router_clear(request)\n"
            + "    return True\n"
        )
        envelope = _navigation(pages, tmp_path)
        assert [op["op"] for op in envelope["ops"]] == ["visit"]

    def test_a_dynamic_chrome_renders_whole(self, tmp_path: Path) -> None:
        pages = _fixed_tree(
            tmp_path / "pages", section="<section>{{ title }}{% template %}</section>"
        )
        with mock.patch.object(SlotView, "render") as never:
            _navigation(pages, tmp_path)
        never.assert_not_called()


class TestForesee:
    def test_a_cut_of_the_project_foresees_nothing(self, tmp_path: Path) -> None:
        request = RequestFactory().get("/")
        with mock.patch.object(client_router, "cut", return_value=object()):
            assert foresee(request, tmp_path / "page.py", {}) is None
