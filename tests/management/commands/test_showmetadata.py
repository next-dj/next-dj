from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from tests.support import USER_URLCONF, routed, write_page, write_tree


def _show(path: str) -> str:
    out = StringIO()
    call_command("showmetadata", path, stdout=out)
    return out.getvalue()


class TestShowMetadata:
    """`showmetadata` names the source of every key the chain of a page sets."""

    def test_every_key_names_its_segment(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=())
        write_page(
            root,
            "",
            "metadata = {'title': 'Home', 'description': 'Site'}\n",
            body="<p>x</p>",
        )
        leaf = write_page(
            root,
            "about",
            "from next.pages import page\n\n"
            "@page.metadata\n"
            "def about_metadata():\n"
            "    return {'title': 'About'}\n",
            body="<p>x</p>",
        )
        with routed(root):
            printed = _show("/about/")
        assert printed == (
            f"{leaf}\n"
            f"  description: {root / 'page.py'}\n"
            f"  title: {root / 'page.py'}\n"
            f"  *: about_metadata in {leaf}\n"
        )

    @pytest.mark.parametrize(
        ("metadata", "source"),
        [
            ({}, "NEXT_FRAMEWORK['SITE']['NAME']"),
            (
                {"DEFAULTS": {"site_name": "Own"}},
                "NEXT_FRAMEWORK['METADATA']['DEFAULTS']",
            ),
        ],
        ids=["site_scope", "defaults"],
    )
    def test_the_site_name_names_the_setting_it_came_from(
        self, tmp_path, metadata, source
    ) -> None:
        root = write_tree(tmp_path / "pages", pages=())
        write_page(root, "", "metadata = {}\n", body="<p>x</p>")
        with routed(root, SITE={"NAME": "Acme"}, METADATA=metadata):
            printed = _show("/")
        assert f"  site_name: {source}\n" in printed

    def test_an_unrouted_path_is_an_error(self, tmp_path) -> None:
        with (
            routed(write_tree(tmp_path / "pages")),
            pytest.raises(CommandError, match="resolves to no URL"),
        ):
            _show("/nowhere/")

    def test_a_path_of_another_view_is_an_error(self, tmp_path) -> None:
        with (
            routed(write_tree(tmp_path / "pages"), urlconf=USER_URLCONF),
            pytest.raises(CommandError, match="which is no page"),
        ):
            _show("/sitemap.xml")

    @pytest.mark.parametrize(
        ("source", "match"),
        [
            ("metadata = {'title': 1}\n", "metadata key 'title'"),
            (
                (
                    "from next.pages import page\n\n"
                    "metadata = {'title': 'Home'}\n\n"
                    "@page.metadata\n"
                    "def home_metadata():\n"
                    "    return {}\n"
                ),
                "declares both a metadata dict",
            ),
        ],
        ids=["shape", "conflict"],
    )
    def test_a_refused_chain_is_an_error(self, tmp_path, source, match) -> None:
        root = write_tree(tmp_path / "pages", pages=())
        write_page(root, "", source, body="<p>x</p>")
        with routed(root), pytest.raises(CommandError, match=match):
            _show("/")
