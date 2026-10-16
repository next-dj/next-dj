from io import StringIO
from pathlib import Path

from django.core.management import call_command

from tests.support import installed_apps, write_app, write_page, write_urlconf


def _project(root: Path) -> str:
    write_app(root, "wallets", {"pages": ("ops",)}, mounts={"pages": "w/<int:wallet>/"})
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
        root,
        "wallets",
        "blog",
        "audit",
        urlconf=_project(root),
        dirs=[root / "site"],
        **overrides,
    ):
        call_command("next", "routes", stdout=out)
    return out.getvalue().splitlines()


class TestRoutesSubcommand:
    """`next routes` prints every page route in resolution order under a header."""

    def test_the_table_names_every_source(self, tmp_path) -> None:
        assert _routes(tmp_path, BASE_DIR=tmp_path) == [
            (
                "URL                        Name                        App      "
                "File                       Source"
            ),
            (
                "/org/<int:org>/audit/log/  audit:page_log              audit    "
                "audit/pages/log/page.py    urls.py"
            ),
            (
                "/w/<int:wallet>/ops/       next:page_w_int_wallet_ops  wallets  "
                "wallets/pages/ops/page.py  mounts"
            ),
            (
                "/about/                    next:page_about             blog     "
                "blog/pages/about/page.py   root"
            ),
            (
                "/contact/                  next:page_contact           -        "
                "site/contact/page.py       root"
            ),
        ]

    def test_a_file_outside_the_base_dir_keeps_its_absolute_path(
        self, tmp_path
    ) -> None:
        elsewhere = tmp_path / "project"
        elsewhere.mkdir()

        lines = _routes(tmp_path, BASE_DIR=elsewhere)

        assert str(tmp_path / "blog" / "pages" / "about" / "page.py") in lines[3]

    def test_without_a_base_dir_every_path_is_absolute(self, tmp_path) -> None:
        lines = _routes(tmp_path, BASE_DIR=None)

        assert str(tmp_path / "site" / "contact" / "page.py") in lines[4]

    def test_a_project_without_pages_prints_the_header_alone(self, tmp_path) -> None:
        out = StringIO()

        with installed_apps(tmp_path):
            call_command("next", "routes", stdout=out)

        assert out.getvalue().splitlines() == ["URL  Name  App  File  Source"]
