import os
from pathlib import Path

import pytest

from next.pages.loaders import layout_chain
from next.pages.placeholder import ROOT_MARKER
from tests.support import (
    build_page_request,
    routed,
    touch_later,
    unified_view,
    write_page,
)
from tests.support.access import client_as, page_source


_OUTER = "<outer>{% template %}</outer>"
_EMBED = f"{ROOT_MARKER}<embed>{{% template %}}</embed>"
_EMBED_PLAIN = "<embed>{% template %}</embed>"

_SHELL_OUTER = (
    '<html data-shell="outer"><head>{% metadata %}</head>'
    '<body>{% zone "chrome" %}<nav>chrome</nav>{% endzone %}{% template %}</body>'
    "</html>"
)
_SHELL_EMBED = (
    f'{ROOT_MARKER}<html data-shell="embed"><head>{{% metadata %}}</head>'
    "<body>{% template %}</body></html>"
)
_SITE_PAGE = page_source(
    'page.login_required(inherit=True)\nmetadata = {"title": {"template": "{title} | Site"}}\n',
    template="<p>home</p>",
)
_EMBED_PAGE = 'metadata = {"title": "Embedded"}\ntemplate = "<p>embedded</p>"\n'


def _embedded_page(tmp_path: Path) -> Path:
    """Write a page two levels below a root layout, with a layout above the root."""
    (tmp_path / "layout.djx").write_text(_OUTER)
    embed = tmp_path / "site" / "embed"
    embed.mkdir(parents=True)
    (embed / "layout.djx").write_text(_EMBED)
    page_file = embed / "x" / "page.py"
    page_file.parent.mkdir()
    page_file.write_text("x = 1")
    (page_file.parent / "template.djx").write_text("<h1>body</h1>")
    return page_file


def _render(page_instance, page_file: Path) -> str:
    response = unified_view(page_instance, page_file)(build_page_request())
    return response.content.decode()


class TestSkeletonSources:
    """What a page under a root layout watches for its layout skeleton."""

    @pytest.mark.usefixtures("watched_template_edits")
    def test_editing_a_layout_above_the_root_leaves_the_skeleton_fresh(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _embedded_page(tmp_path)
        templates = page_instance._templates
        templates.record_skeleton(page_file)

        touch_later(tmp_path / "layout.djx", "<changed>{% template %}</changed>")

        assert templates.skeleton_is_stale(page_file) is False

    @pytest.mark.usefixtures("watched_template_edits")
    def test_creating_a_layout_above_the_root_leaves_the_skeleton_fresh(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _embedded_page(tmp_path)
        templates = page_instance._templates
        templates.record_skeleton(page_file)

        (tmp_path / "site" / "layout.djx").write_text(_OUTER)

        assert templates.skeleton_is_stale(page_file) is False

    @pytest.mark.usefixtures("watched_template_edits")
    def test_removing_the_marker_makes_the_skeleton_stale(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _embedded_page(tmp_path)
        templates = page_instance._templates
        templates.record_skeleton(page_file)

        touch_later(tmp_path / "site" / "embed" / "layout.djx", _EMBED_PLAIN)

        assert templates.skeleton_is_stale(page_file) is True

    @pytest.mark.usefixtures("watched_template_edits")
    def test_adding_the_marker_makes_the_skeleton_stale(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _embedded_page(tmp_path)
        layout = tmp_path / "site" / "embed" / "layout.djx"
        touch_later(layout, _EMBED_PLAIN)
        templates = page_instance._templates
        templates.record_skeleton(page_file)

        touch_later(layout, _EMBED)

        assert templates.skeleton_is_stale(page_file) is True

    @pytest.mark.usefixtures("watched_template_edits")
    def test_a_new_layout_below_the_root_makes_the_skeleton_stale(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _embedded_page(tmp_path)
        templates = page_instance._templates
        templates.record_skeleton(page_file)

        (page_file.parent / "layout.djx").write_text("<near>{% template %}</near>")

        assert templates.skeleton_is_stale(page_file) is True


class TestComposedSources:
    """The composed layer of a page under a root layout watches the same cut."""

    @pytest.mark.usefixtures("watched_template_edits")
    def test_editing_a_layout_above_the_root_leaves_the_composition_fresh(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _embedded_page(tmp_path)
        templates = page_instance._templates
        templates.record_composed(page_file)

        touch_later(tmp_path / "layout.djx", "<changed>{% template %}</changed>")

        assert templates.composed_is_stale(page_file) is False

    @pytest.mark.usefixtures("watched_template_edits")
    def test_creating_a_layout_above_the_root_leaves_the_composition_fresh(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _embedded_page(tmp_path)
        templates = page_instance._templates
        templates.record_composed(page_file)

        (tmp_path / "site" / "layout.djx").write_text(_OUTER)

        assert templates.composed_is_stale(page_file) is False

    @pytest.mark.usefixtures("watched_template_edits")
    def test_toggling_the_marker_makes_the_composition_stale(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _embedded_page(tmp_path)
        templates = page_instance._templates
        templates.record_composed(page_file)

        touch_later(tmp_path / "site" / "embed" / "layout.djx", _EMBED_PLAIN)

        assert templates.composed_is_stale(page_file) is True


class TestLayoutTextsReset:
    """A rewrite that keeps the mtime reaches the chain once the template caches drop."""

    def test_clearing_the_template_caches_rereads_a_same_tick_rewrite(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _embedded_page(tmp_path)
        layout = tmp_path / "site" / "embed" / "layout.djx"
        assert layout_chain(page_file) == (layout.resolve(),)
        stat = layout.stat()

        layout.write_text(_EMBED_PLAIN)
        os.utime(layout, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert layout_chain(page_file) == (layout.resolve(),)

        page_instance.clear_template_caches()

        assert layout_chain(page_file) == (
            layout.resolve(),
            (tmp_path / "layout.djx").resolve(),
        )


class TestRenderedUnderWatchedEdits:
    """A served page follows the marker and ignores layouts beyond the cut."""

    @pytest.mark.usefixtures("watched_template_edits")
    def test_the_page_renders_inside_the_root_alone(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _embedded_page(tmp_path)

        assert _render(page_instance, page_file) == "<embed><h1>body</h1></embed>"

    @pytest.mark.usefixtures("watched_template_edits")
    def test_an_edit_beyond_the_cut_changes_nothing(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _embedded_page(tmp_path)
        first = _render(page_instance, page_file)

        touch_later(tmp_path / "layout.djx", "<changed>{% template %}</changed>")

        assert _render(page_instance, page_file) == first

    @pytest.mark.usefixtures("watched_template_edits")
    def test_removing_the_marker_wraps_the_page_in_the_outer_layout(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _embedded_page(tmp_path)
        assert "<outer>" not in _render(page_instance, page_file)

        touch_later(tmp_path / "site" / "embed" / "layout.djx", _EMBED_PLAIN)

        assert _render(page_instance, page_file) == (
            "<outer><embed><h1>body</h1></embed></outer>"
        )

    @pytest.mark.usefixtures("watched_template_edits")
    def test_adding_the_marker_drops_the_outer_layout(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _embedded_page(tmp_path)
        layout = tmp_path / "site" / "embed" / "layout.djx"
        touch_later(layout, _EMBED_PLAIN)
        assert "<outer>" in _render(page_instance, page_file)

        touch_later(layout, _EMBED)

        assert _render(page_instance, page_file) == "<embed><h1>body</h1></embed>"

    @pytest.mark.usefixtures("watched_template_edits")
    def test_a_dynamic_page_follows_the_marker_through_its_skeleton(
        self, page_instance, tmp_path
    ) -> None:
        (tmp_path / "layout.djx").write_text(_OUTER)
        embed = tmp_path / "embed"
        embed.mkdir()
        layout = embed / "layout.djx"
        layout.write_text(_EMBED)
        page_file = embed / "page.py"
        page_file.write_text("def render(request):\n    return '<p>dynamic</p>'\n")

        assert _render(page_instance, page_file) == "<embed><p>dynamic</p></embed>"

        touch_later(layout, _EMBED_PLAIN)

        assert _render(page_instance, page_file) == (
            "<outer><embed><p>dynamic</p></embed></outer>"
        )


@pytest.mark.django_db()
class TestNestedRootLayoutRequest:
    """A page under a nested root layout, served through the real URL machinery."""

    @pytest.fixture()
    def site(self, root: Path) -> Path:
        (root / "layout.djx").write_text(_SHELL_OUTER)
        write_page(root, "", _SITE_PAGE)
        embed = root / "embed"
        embed.mkdir()
        (embed / "layout.djx").write_text(_SHELL_EMBED)
        write_page(root, "embed/view", _EMBED_PAGE)
        return root

    def test_the_page_renders_its_own_document_once(self, site) -> None:
        with routed(site):
            response = client_as("visitor").get("/embed/view/")

        body = response.content.decode()
        assert response.status_code == 200
        assert body.count("<html") == 1
        assert 'data-shell="embed"' in body
        assert "<p>embedded</p>" in body

    def test_no_outer_layout_markup_reaches_the_page(self, site) -> None:
        with routed(site):
            body = client_as("visitor").get("/embed/view/").content.decode()

        assert 'data-shell="outer"' not in body
        assert "chrome" not in body
        assert ROOT_MARKER not in body

    def test_the_tree_root_page_keeps_the_outer_layout(self, site) -> None:
        with routed(site):
            body = client_as("visitor").get("/").content.decode()

        assert 'data-shell="outer"' in body
        assert 'data-shell="embed"' not in body
        assert "<nav>chrome</nav>" in body

    def test_ancestor_metadata_still_renders_in_the_root_layout(self, site) -> None:
        with routed(site):
            body = client_as("visitor").get("/embed/view/").content.decode()

        assert "<title>Embedded | Site</title>" in body

    def test_access_declared_in_an_ancestor_is_still_enforced(self, site) -> None:
        with routed(site):
            response = client_as("anonymous").get("/embed/view/")

        assert response.status_code == 302

    def test_a_zone_of_the_cut_layout_is_unknown_to_the_page(self, site) -> None:
        with routed(site):
            response = client_as("visitor").get_zones("/embed/view/", "chrome")

        assert response.status_code == 400
        assert response.content == b"unknown zone"

    def test_the_same_zone_stays_known_to_a_page_outside_the_cut(self, site) -> None:
        with routed(site):
            response = client_as("visitor").get_zones("/", "chrome")

        assert response.status_code == 200
