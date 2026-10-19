from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import override_settings

from tests.support import isolated_form_registries, routed, write_page
from tests.support.access import (
    COMPONENTS,
    login_required_middleware,
    page_source,
    write_access_project,
    write_proxy,
)


VIEWS_URLCONF = "tests.permissions.urls_views"

SUBCLASS = "tests.permissions.test_access.LoginRequiredSubclass"


@pytest.fixture(autouse=True)
def _form_registries():
    with isolated_form_registries():
        yield


def _permissions(
    tmp_path: Path,
    *args: str,
    base_dir: Path | None,
    urlconf: str = "tests.urls.urls_namespaced",
) -> list[str]:
    root = write_access_project(tmp_path / "pages")
    out = StringIO()
    with (
        routed(root, urlconf=urlconf, COMPONENT_BACKENDS=COMPONENTS),
        override_settings(BASE_DIR=base_dir),
    ):
        call_command("permissions", *args, stdout=out)
    return out.getvalue().splitlines()


def _row(lines: list[str], kind: str, endpoint: str) -> list[str]:
    [found] = [
        line.split()[2:] for line in lines if line.split()[:2] == [kind, endpoint]
    ]
    return found


class TestPermissionsCommand:
    """`manage.py permissions` prints the rule of every endpoint and its sources."""

    def test_one_page_with_its_action_and_component(self, tmp_path) -> None:
        assert _permissions(tmp_path, "/", base_dir=tmp_path) == [
            (
                "Kind       Endpoint                      Access                      "
                "Sources"
            ),
            (
                "page       /                             public                      "
                "public (pages/page.py)"
            ),
            (
                "action     guarded_ping (pages/page.py)  login, perm auth.add_group  "
                "pages/page.py"
            ),
            (
                "component  card                          login                       "
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
        assert [line.split()[0] for line in lines[5:]].count("component") == 1
        assert lines[-1].split() == [
            "view",
            "/_next/form/<str:uid>/",
            "per",
            "action",
            "see",
            "the",
            "action",
            "rows",
        ]

    def test_an_action_names_the_file_declaring_it(self, tmp_path) -> None:
        lines = _permissions(tmp_path, base_dir=tmp_path)

        assert _row(lines, "action", "open_ping") == ["(pages/open/page.py)", "-", "-"]

    def test_without_a_base_dir_sources_stay_absolute(self, tmp_path) -> None:
        lines = _permissions(tmp_path, "/", base_dir=None)

        assert lines[1].endswith(f"public ({tmp_path / 'pages' / 'page.py'})")
        assert f"guarded_ping ({tmp_path / 'pages' / 'page.py'})" in lines[2]

    def test_a_url_nothing_answers_is_an_error(self, tmp_path) -> None:
        with pytest.raises(
            CommandError, match="No page or view answers the URL '/missing/'"
        ):
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


class TestPermissionsUnderTheMiddleware:
    """What `LoginRequiredMiddleware` closes names the middleware as its source."""

    def test_a_closed_page_and_action_name_the_middleware(self, tmp_path) -> None:
        with login_required_middleware():
            lines = _permissions(tmp_path, "/open/", base_dir=tmp_path)

        assert [line.split()[-3:] for line in lines[1:]] == [
            ["login", "(middleware)", "LoginRequiredMiddleware"],
            ["login", "(middleware)", "LoginRequiredMiddleware"],
            ["(pages/open/page.py)", "login", "pages/open/page.py"],
        ]

    def test_a_subclass_names_itself(self, tmp_path) -> None:
        with override_settings(MIDDLEWARE=[*settings.MIDDLEWARE, SUBCLASS]):
            lines = _permissions(tmp_path, "/open/", base_dir=tmp_path)

        assert lines[1].split()[-1] == "LoginRequiredSubclass"


class TestPermissionsOfViews:
    """A view outside the page trees is a row, read off its `login_required` mark."""

    def test_every_view_follows_the_page_endpoints(self, tmp_path) -> None:
        lines = _permissions(tmp_path, base_dir=tmp_path, urlconf=VIEWS_URLCONF)

        views = [line.split() for line in lines if line.startswith("view ")]
        assert views[:3] == [
            ["view", "/sw.js", "-", "-"],
            [
                "view",
                "/api/report/",
                "public",
                "public",
                "(tests.permissions.urls_views.Report)",
            ],
            [
                "view",
                "/robots.txt",
                "public",
                "public",
                "(tests.permissions.urls_views.robots)",
            ],
        ]
        assert all(line.split()[0] != "view" for line in lines[: -len(views)])

    def test_under_the_middleware_an_unmarked_view_is_closed(self, tmp_path) -> None:
        with login_required_middleware():
            lines = _permissions(
                tmp_path, "/sw.js", base_dir=tmp_path, urlconf=VIEWS_URLCONF
            )

        assert lines == [
            "Kind  Endpoint  Access              Sources",
            "view  /sw.js    login (middleware)  LoginRequiredMiddleware",
        ]

    def test_a_url_answers_for_a_view_under_an_include(self, tmp_path) -> None:
        lines = _permissions(
            tmp_path, "/api/report/", base_dir=tmp_path, urlconf=VIEWS_URLCONF
        )

        assert lines[1:] == [
            "view  /api/report/  public  public (tests.permissions.urls_views.Report)"
        ]
