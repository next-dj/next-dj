from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from tests.support import isolated_form_registries, routed, write_page
from tests.support.access import (
    COMPONENTS,
    page_source,
    write_access_project,
    write_proxy,
)


@pytest.fixture(autouse=True)
def _form_registries():
    with isolated_form_registries():
        yield


def _permissions(tmp_path: Path, *args: str, base_dir: Path | None) -> list[str]:
    root = write_access_project(tmp_path / "pages")
    out = StringIO()
    with (
        routed(root, COMPONENT_BACKENDS=COMPONENTS),
        override_settings(BASE_DIR=base_dir),
    ):
        call_command("permissions", *args, stdout=out)
    return out.getvalue().splitlines()


class TestPermissionsCommand:
    """`manage.py permissions` prints the rule of every endpoint and its sources."""

    def test_one_page_with_its_action_and_component(self, tmp_path) -> None:
        assert _permissions(tmp_path, "/", base_dir=tmp_path) == [
            "Kind       Endpoint      Access                      Sources",
            (
                "page       /             public                      "
                "public (pages/page.py)"
            ),
            "action     guarded_ping  login, perm auth.add_group  pages/page.py",
            (
                "component  card          login                       "
                "pages/_components/card/component.py"
            ),
        ]

    def test_every_endpoint_without_a_url(self, tmp_path) -> None:
        lines = _permissions(tmp_path, base_dir=tmp_path)

        assert lines[0].split() == ["Kind", "Endpoint", "Access", "Sources"]
        assert [line.split(maxsplit=2)[:2] for line in lines[1:5]] == [
            ["page", "/"],
            ["page", "/broken/"],
            ["page", "/notes/<int:group>/"],
            ["page", "/open/"],
        ]
        assert lines[2].split()[2:] == ["broken", "pages/broken/proxy.py"]
        assert lines[3].split()[2:] == [
            "login,",
            "perm",
            "auth.change_group",
            "on",
            "auth.Group",
            "pages/notes/proxy.py,",
            "pages/notes/[int:group]/page.py",
        ]
        assert lines[4].split()[2:] == ["-", "-"]
        assert lines[-1].split() == [
            "component",
            "card",
            "login",
            "pages/_components/card/component.py",
        ]

    def test_without_a_base_dir_sources_stay_absolute(self, tmp_path) -> None:
        lines = _permissions(tmp_path, "/", base_dir=None)

        assert lines[1].endswith(f"public ({tmp_path / 'pages' / 'page.py'})")

    def test_a_url_no_page_answers_is_an_error(self, tmp_path) -> None:
        with pytest.raises(CommandError, match="No page answers the URL '/missing/'"):
            _permissions(tmp_path, "/missing/", base_dir=tmp_path)

    def test_a_lifted_page_names_the_file_that_lifted(self, tmp_path) -> None:
        root = tmp_path / "pages"
        write_proxy(root / "booking", "proxy.login_required()\n")
        receipt = root / "booking" / "[int:booking]" / "receipt"
        write_proxy(receipt, "proxy.public()\n")
        write_page(root, "booking/[int:booking]/receipt", page_source())
        out = StringIO()
        with routed(root), override_settings(BASE_DIR=tmp_path):
            call_command("permissions", "/booking/1/receipt/", stdout=out)
        assert out.getvalue().splitlines()[1].split() == [
            "page",
            "/booking/<int:booking>/receipt/",
            "public",
            "public",
            "(pages/booking/[int:booking]/receipt/proxy.py)",
        ]
