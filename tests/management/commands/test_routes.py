from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import override_settings

from tests.support import (
    installed_apps,
    isolated_form_registries,
    routed,
    write_app,
    write_page,
    write_urlconf,
)
from tests.support.access import (
    COMPONENTS,
    PAGE_HEAD,
    login_required_middleware,
    write_access_project,
)
from tests.support.cases.intercepts import zone_body


def _project(root: Path) -> str:
    write_app(root, "blog", {"pages": ("about",)})
    write_app(root, "audit", {"pages": ("log",)})
    write_page(root / "site", "contact")
    return write_urlconf(
        root,
        "routes_urls",
        'path("org/<int:org>/audit/", pages("audit"))',
        'path("", include("next.urls"))',
    )


def _routes(root: Path, **overrides: object) -> list[str]:
    out = StringIO()
    with installed_apps(
        root, "blog", "audit", urlconf=_project(root), dirs=[root / "site"], **overrides
    ):
        call_command("routes", stdout=out)
    return out.getvalue().splitlines()


class TestRoutesCommand:
    """`manage.py routes` prints every page route in resolution order under a header."""

    def test_the_table_names_every_route(self, tmp_path) -> None:
        assert _routes(tmp_path, BASE_DIR=tmp_path) == [
            (
                "URL                        Name               "
                "File                      Access"
            ),
            (
                "/org/<int:org>/audit/log/  audit:page_log     "
                "audit/pages/log/page.py   -"
            ),
            (
                "/about/                    next:page_about    "
                "blog/pages/about/page.py  -"
            ),
            (
                "/contact/                  next:page_contact  "
                "site/contact/page.py      -"
            ),
        ]

    def test_a_file_outside_the_base_dir_keeps_its_absolute_path(
        self, tmp_path
    ) -> None:
        elsewhere = tmp_path / "project"
        elsewhere.mkdir()

        lines = _routes(tmp_path, BASE_DIR=elsewhere)

        assert str(tmp_path / "blog" / "pages" / "about" / "page.py") in lines[2]

    def test_without_a_base_dir_every_path_is_absolute(self, tmp_path) -> None:
        lines = _routes(tmp_path, BASE_DIR=None)

        assert str(tmp_path / "site" / "contact" / "page.py") in lines[3]

    def test_a_project_without_pages_prints_the_header_alone(self, tmp_path) -> None:
        out = StringIO()

        with installed_apps(tmp_path):
            call_command("routes", stdout=out)

        assert out.getvalue().splitlines() == ["URL  Name  File  Access"]


class TestRoutesAccessColumn:
    """The Access column spells the rule each page answers to."""

    @pytest.fixture()
    def access_lines(self, tmp_path):
        root = write_access_project(tmp_path / "pages")

        def run(**framework: object) -> list[str]:
            out = StringIO()
            with (
                isolated_form_registries(),
                routed(root, COMPONENT_BACKENDS=COMPONENTS, **framework),
                override_settings(BASE_DIR=tmp_path),
            ):
                call_command("routes", stdout=out)
            return [
                line.split("page.py", 1)[1].strip()
                for line in out.getvalue().splitlines()[1:]
            ]

        return run

    def test_each_rule_in_short_words(self, access_lines) -> None:
        assert access_lines() == [
            "public",
            "broken",
            "login, perm auth.change_group on auth.Group",
            "-",
        ]

    def test_the_middleware_closes_an_open_page(self, access_lines) -> None:
        with login_required_middleware():
            assert access_lines()[3] == "login (default)"

    def test_the_setting_closes_an_open_page(self, access_lines) -> None:
        assert access_lines(LOGIN_REQUIRED=True)[3] == "login (default)"


class TestRoutesRouteGroups:
    """A grouped page shows its group-free URL and name, and the group in its file."""

    def test_the_row_leaves_the_group_out_and_the_file_keeps_it(self, tmp_path) -> None:
        write_page(tmp_path / "site", "(auth)/login")
        out = StringIO()

        with installed_apps(tmp_path, dirs=[tmp_path / "site"], BASE_DIR=tmp_path):
            call_command("routes", stdout=out)

        [row] = out.getvalue().splitlines()[1:]
        assert row.split() == [
            "/login/",
            "next:page_login",
            "site/(auth)/login/page.py",
            "-",
        ]


class TestRoutesIntercepts:
    """An intercept page is listed under its target with the zones it serves."""

    @staticmethod
    def _site(root: Path, source: str = PAGE_HEAD) -> None:
        write_page(root / "site", "feed/photo")
        write_page(
            root / "site", "feed/(.)photo", source, body=zone_body("peek", "info")
        )

    def _lines(self, root: Path) -> list[str]:
        out = StringIO()
        with installed_apps(root, dirs=[root / "site"], BASE_DIR=root):
            call_command("routes", stdout=out)
        return out.getvalue().splitlines()

    def test_the_table_gains_an_intercept_column(self, tmp_path) -> None:
        self._site(tmp_path, f"{PAGE_HEAD}page.login_required()\n")

        assert self._lines(tmp_path) == [
            (
                "URL           Name                  "
                "File                        Access  Intercept"
            ),
            ("/feed/photo/  next:page_feed_photo  site/feed/photo/page.py     -"),
            (
                "/feed/photo/  -                     "
                "site/feed/(.)photo/page.py  login   peek, info"
            ),
        ]

    def test_a_virtual_intercept_is_listed_by_its_template(self, tmp_path) -> None:
        write_page(tmp_path / "site", "feed/photo")
        directory = tmp_path / "site" / "feed" / "(.)photo"
        directory.mkdir(parents=True)
        (directory / "template.djx").write_text(zone_body("peek"))

        row = self._lines(tmp_path)[2]

        assert row.split()[2] == "site/feed/(.)photo/template.djx"

    def test_a_tree_without_intercepts_keeps_the_four_columns(self, tmp_path) -> None:
        write_page(tmp_path / "site", "feed/photo")

        lines = self._lines(tmp_path)

        assert lines[0].split() == ["URL", "Name", "File", "Access"]
        assert all(line == line.rstrip() for line in lines)

    def test_a_tree_mounted_twice_lists_the_intercept_under_both_urls(
        self, tmp_path
    ) -> None:
        write_app(tmp_path, "blog", {"pages": ("feed/photo",)})
        write_page(
            tmp_path / "blog" / "pages",
            "feed/(.)photo",
            PAGE_HEAD,
            body=zone_body("peek"),
        )
        urlconf = write_urlconf(
            tmp_path,
            "twice_urls",
            'path("a/", pages("blog", namespace="a"))',
            'path("b/", pages("blog", namespace="b"))',
        )
        out = StringIO()

        with installed_apps(tmp_path, "blog", urlconf=urlconf, BASE_DIR=tmp_path):
            call_command("routes", stdout=out)

        rows = [line.split() for line in out.getvalue().splitlines()[1:]]
        assert [(row[0], row[1], row[-1]) for row in rows] == [
            ("/a/feed/photo/", "a:page_feed_photo", "-"),
            ("/a/feed/photo/", "-", "peek"),
            ("/b/feed/photo/", "b:page_feed_photo", "-"),
            ("/b/feed/photo/", "-", "peek"),
        ]
