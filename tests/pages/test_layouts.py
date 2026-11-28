from pathlib import Path

import pytest
from django.http import HttpRequest
from django.template import Context, Template, TemplateSyntaxError

from next.pages.layouts import (
    META,
    SLOT,
    LayoutChain,
    closing,
    context_response_id,
    enclose_region,
    enclosed,
    forget_chains,
    held_chain,
    hold_chain,
    layout_id,
    mint_response_id,
    opening,
    request_response_id,
)
from next.pages.loaders import LayoutTemplateLoader, layout_ids, module_version
from next.pages.placeholder import slot_problem
from next.requests import NEXT_ATTR, next_of
from next.testing import override_next_settings


class TestLayoutId:
    """A layout id names the path below its root and the source, nothing else."""

    def test_short_hex_stable_across_processes(self) -> None:
        root = Path("/srv/pages")
        ident = layout_id(
            root / "blog" / "layout.djx", "<main>{% template %}</main>", (root,)
        )
        assert ident == "fa8f41da3ed4c69f"

    def test_the_root_location_does_not_count(self, tmp_path: Path) -> None:
        first = tmp_path / "one" / "pages"
        second = tmp_path / "two" / "pages"
        source = "<main>{% template %}</main>"
        assert layout_id(first / "layout.djx", source, (first,)) == layout_id(
            second / "layout.djx", source, (second,)
        )

    def test_an_edit_or_another_path_changes_it(self, tmp_path: Path) -> None:
        root = tmp_path / "pages"
        base = layout_id(root / "layout.djx", "a", (root,))
        assert layout_id(root / "layout.djx", "b", (root,)) != base
        assert layout_id(root / "x" / "layout.djx", "a", (root,)) != base

    def test_the_deepest_root_wins_and_no_root_keeps_the_path(
        self, tmp_path: Path
    ) -> None:
        inner = tmp_path / "pages" / "app"
        layout = inner / "layout.djx"
        nested = layout_id(layout, "a", (tmp_path / "pages", inner))
        assert nested == layout_id(layout, "a", (inner,))
        assert layout_id(layout, "a", ()) != nested


class TestSeal:
    """The response id lives on the request a page view routes."""

    def test_a_request_mints_one_response_id_and_keeps_it(self) -> None:
        request = HttpRequest()
        assert request_response_id(request) is None
        assert NEXT_ATTR not in vars(request)
        response_id = mint_response_id(request)
        assert len(response_id) == 8
        assert mint_response_id(request) == response_id == next_of(request).response_id
        assert request_response_id(request) == response_id

    def test_regions_enclose_only_with_a_response_id(self) -> None:
        request = HttpRequest()
        context = Context({"request": request})
        assert context_response_id(context) is None
        assert enclose_region(context, META, "<title>t</title>") == "<title>t</title>"
        response_id = mint_response_id(request)
        assert enclose_region(context, META, "x") == enclosed(META, response_id, "x")
        assert enclosed(SLOT, response_id, "x", "id") == (
            f"{opening(SLOT, response_id, 'id')}x{closing(SLOT, response_id, 'id')}"
        )


class TestChain:
    """The id chain of a page is held beside its skeleton."""

    def test_layout_ids_outer_first(self, tmp_path: Path) -> None:
        (tmp_path / "layout.djx").write_text("<html>{% template %}</html>")
        section = tmp_path / "sec"
        section.mkdir()
        (section / "layout.djx").write_text("<section>{% template %}</section>")
        (section / "skip").mkdir()
        (section / "skip" / "layout.djx").write_text("<p>no placeholder</p>")
        page_file = section / "skip" / "page.py"
        ids = layout_ids(page_file)
        assert len(ids) == 2
        assert layout_ids(page_file) is ids

    def test_a_moved_version_or_a_stale_stamp_drops_the_chain(
        self, tmp_path: Path
    ) -> None:
        page_file = tmp_path / "page.py"
        layout = tmp_path / "layout.djx"
        layout.write_text("x")
        version = module_version()
        hold_chain(page_file, LayoutChain((), ((layout, -1),), version))
        assert held_chain(page_file, version) is not None
        assert held_chain(page_file, version + 1) is None
        forget_chains()
        assert held_chain(page_file, version) is None

    @pytest.mark.usefixtures("watched_template_edits")
    def test_watched_edits_compare_the_stamps(self, tmp_path: Path) -> None:
        page_file = tmp_path / "page.py"
        layout = tmp_path / "layout.djx"
        layout.write_text("x")
        version = module_version()
        hold_chain(page_file, LayoutChain((), ((layout, -1),), version))
        assert held_chain(page_file, version) is None


class TestComposition:
    """Composition splices the slot tag only while the router is on."""

    def _tree(self, tmp_path: Path) -> Path:
        (tmp_path / "layout.djx").write_text("<main>{% template %}</main>")
        return tmp_path / "leaf" / "page.py"

    def test_a_disabled_router_leaves_the_slot_unwrapped(self, tmp_path: Path) -> None:
        page_file = self._tree(tmp_path)
        with override_next_settings(ROUTER={"ENABLED": False}):
            composed = LayoutTemplateLoader().compose_body("<p>x</p>", page_file)
        assert composed == "<main>{% #template %}<p>x</p>{% /template %}</main>"

    def test_an_enabled_router_wraps_the_slot(self, tmp_path: Path) -> None:
        page_file = self._tree(tmp_path)
        with override_next_settings(ROUTER={"ENABLED": True}):
            composed = LayoutTemplateLoader().compose_body("<p>x</p>", page_file)
        (ident,) = layout_ids(page_file)
        assert composed == (
            f"<main>{{% next:slot {ident} %}}{{% #template %}}<p>x</p>"
            "{% /template %}{% /next:slot %}</main>"
        )


class TestLayoutSlotNode:
    """The slot tag writes the enclosing comments only for a routed request."""

    def test_a_slot_without_a_response_id_renders_plain(self) -> None:
        source = "<main>{% next:slot abc %}<p>x</p>{% /next:slot %}</main>"
        assert Template(source).render(Context({})) == "<main><p>x</p></main>"

    def test_a_slot_with_a_response_id_renders_enclosed(self) -> None:
        request = HttpRequest()
        response_id = mint_response_id(request)
        source = "{% next:slot abc %}<p>x</p>{% /next:slot %}"
        html = Template(source).render(Context({"request": request}))
        assert html == enclosed(SLOT, response_id, "<p>x</p>", "abc")

    def test_an_extends_content_takes_the_rest(self) -> None:
        source = "{% next:slot abc %}{% extends base %}{% /next:slot %}"
        base = Template("<b>base</b>")
        assert Template(source).render(Context({"base": base})) == "<b>base</b>"

    def test_the_tag_takes_one_id(self) -> None:
        with pytest.raises(TemplateSyntaxError, match="takes the id of one layout"):
            Template("{% next:slot %}{% /next:slot %}")


class TestSlotProblem:
    """A placeholder the slot comments cannot wrap is named."""

    @pytest.mark.parametrize(
        ("source", "problem"),
        [
            ("<main>{% template %}</main>", None),
            ("<p>no placeholder</p>", None),
            ("<!-- <div --><main>{% template %}</main>", None),
            ('<div title="{% template %}"></div>', "inside an HTML attribute"),
            ("<head><title>{% template %}</title></head>", "inside a <title>"),
            (
                "{% verbatim %}{% template %}{% endverbatim %}<main>{% template %}</main>",
                "inside a {% verbatim %} block",
            ),
            ("{% verbatim %}<p>x</p>{% endverbatim %}{% template %}", None),
        ],
    )
    def test_the_slot_problem_of_a_layout_source_is_named(
        self, source: str, problem: str | None
    ) -> None:
        assert slot_problem(source) == problem
