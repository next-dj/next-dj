from pathlib import Path
from unittest.mock import patch

import pytest
from django.template import Context
from django.test import override_settings

from next.pages import skeletons
from next.pages.skeletons import (
    declared_zones,
    render_skeleton,
    skeleton_path,
    skeleton_zone,
    zone_skeleton,
)
from next.seeding import TEMPLATE_PATH_KEY


def _page(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    template = directory / "template.djx"
    template.write_text(body)
    return template


class TestSkeletonNames:
    """A `loading.<zone>.djx` file name names its zone, any other name none."""

    @pytest.mark.parametrize(
        ("name", "zone"),
        [
            pytest.param("loading.rows.djx", "rows", id="zone"),
            pytest.param("loading.audit-table.djx", "audit-table", id="slug"),
            pytest.param("loading.djx", None, id="boundary-file"),
            pytest.param("loading..djx", None, id="empty-zone"),
            pytest.param("error.rows.djx", None, id="other-prefix"),
            pytest.param("loading.rows.html", None, id="other-suffix"),
        ],
    )
    def test_the_zone_a_name_names(self, name: str, zone: str | None) -> None:
        assert skeleton_zone(Path("/srv") / name) == zone

    def test_the_path_of_a_skeleton(self) -> None:
        assert skeleton_path(Path("/srv/pages"), "rows") == Path(
            "/srv/pages/loading.rows.djx"
        )


class TestDeclaredZones:
    """The zones a template file declares come from Django's own lexer."""

    def test_every_reached_zone_is_named(self, tmp_path: Path) -> None:
        source = _page(
            tmp_path,
            '{% zone "a" %}{% zone \'b\' tag="ul" %}{% endzone %}{% endzone %}'
            "{{ value }}text{% %}{% zone %}{% zone name %}",
        )
        assert declared_zones(source) == frozenset({"a", "b"})

    def test_commented_and_verbatim_zones_are_text(self, tmp_path: Path) -> None:
        source = _page(
            tmp_path,
            '{% comment %}{% zone "c" %}{% endcomment %}'
            '{% verbatim %}{% zone "v" %}{% endverbatim %}{% zone "a" %}{% endzone %}',
        )
        assert declared_zones(source) == frozenset({"a"})

    def test_a_missing_file_declares_nothing(self, tmp_path: Path) -> None:
        assert declared_zones(tmp_path / "template.djx") == frozenset()

    def test_a_page_module_declares_its_template_string(self, tmp_path: Path) -> None:
        source = tmp_path / "page.py"
        source.write_text("template = '{% zone \"pz\" %}{% endzone %}'\n")
        assert declared_zones(source) == frozenset({"pz"})

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("x = 1\n", id="no-template"),
            pytest.param("template = 3\n", id="not-a-string"),
        ],
    )
    def test_a_page_module_without_a_template_string_declares_nothing(
        self, tmp_path: Path, text: str
    ) -> None:
        source = tmp_path / "page.py"
        source.write_text(text)
        assert declared_zones(source) == frozenset()

    def test_an_unchanged_file_is_scanned_once(self, tmp_path: Path) -> None:
        source = _page(tmp_path, '{% zone "a" %}{% endzone %}')
        assert declared_zones(source) is declared_zones(source)


class TestZoneSkeleton:
    """The page body and its layouts each hold the skeletons of their own zones."""

    def test_the_page_body_skeleton(self, tmp_path: Path) -> None:
        template = _page(tmp_path / "list", '{% zone "rows" %}{% endzone %}')
        skeleton = template.parent / "loading.rows.djx"
        skeleton.write_text("S")
        assert zone_skeleton(template, "rows") == skeleton

    def test_a_layout_skeleton(self, tmp_path: Path) -> None:
        (tmp_path / "layout.djx").write_text(
            '{% zone "nav" %}{% endzone %}{% template %}'
        )
        (tmp_path / "loading.nav.djx").write_text("S")
        template = _page(tmp_path / "list", "<p>body</p>")
        assert zone_skeleton(template, "nav") == tmp_path / "loading.nav.djx"

    def test_an_inline_page_template_skeleton(self, tmp_path: Path) -> None:
        (tmp_path / "page.py").write_text(
            "template = '{% zone \"pz\" %}{% endzone %}'\n"
        )
        (tmp_path / "loading.pz.djx").write_text("S")
        skeleton = zone_skeleton(tmp_path / "template.djx", "pz")
        assert skeleton == tmp_path / "loading.pz.djx"

    def test_a_skeleton_beside_the_wrong_template_is_not_used(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "loading.rows.djx").write_text("S")
        template = _page(tmp_path / "list", '{% zone "rows" %}{% endzone %}')
        assert zone_skeleton(template, "rows") is None

    def test_a_zone_without_a_file_has_none(self, tmp_path: Path) -> None:
        template = _page(tmp_path, '{% zone "rows" %}{% endzone %}')
        assert zone_skeleton(template, "rows") is None

    def test_the_walk_is_held_until_the_registry_reloads(self, tmp_path: Path) -> None:
        template = _page(tmp_path, '{% zone "rows" %}{% endzone %}')
        assert zone_skeleton(template, "rows") is None
        (tmp_path / "loading.rows.djx").write_text("S")
        assert zone_skeleton(template, "rows") is None
        with patch.object(skeletons, "module_version", return_value=-1):
            assert zone_skeleton(template, "rows") == tmp_path / "loading.rows.djx"

    @override_settings(DEBUG=True)
    def test_a_watching_process_walks_again(self, tmp_path: Path) -> None:
        template = _page(tmp_path, '{% zone "rows" %}{% endzone %}')
        assert zone_skeleton(template, "rows") is None
        (tmp_path / "loading.rows.djx").write_text("S")
        assert zone_skeleton(template, "rows") == tmp_path / "loading.rows.djx"


class TestRenderSkeleton:
    """A skeleton renders over the zone's context, its own path as the template."""

    def test_the_context_and_the_path(self, tmp_path: Path) -> None:
        skeleton = tmp_path / "loading.rows.djx"
        skeleton.write_text("{{ who }}|{{ current_template_path }}")
        out = render_skeleton(skeleton, Context({"who": "<i>", TEMPLATE_PATH_KEY: "x"}))
        assert out == f"&lt;i&gt;|{skeleton}"

    def test_autoescape_follows_the_zone(self, tmp_path: Path) -> None:
        skeleton = tmp_path / "loading.rows.djx"
        skeleton.write_text("{{ who }}")
        out = render_skeleton(skeleton, Context({"who": "<i>"}, autoescape=False))
        assert out == "<i>"
