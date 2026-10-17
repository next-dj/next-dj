import re
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from next.boundaries import imports as imports_module, manager as manager_module
from tests.support import isolated_form_registries, write_urlconf
from tests.support.boundaries import CANVAS_APPS, boundary_project


_TWO_IMPORTS = "from crm.utils import first\nfrom crm.utils import second\n"

_PROJECT = {
    "shop": {"views.py": _TWO_IMPORTS},
    "crm": {"utils.py": "first = second = third = 1\n"},
}

_BROKEN = {"shop": {"views.py": "def broken(:\n"}}

_STRAY_RECORD = (
    'version = 1\n[[violation]]\nfile = "elsewhere.py"\ncode = "next.W136"\ncount = 1\n'
)


def _run(*args: str) -> list[str]:
    out = StringIO()
    call_command("next", "boundaries", *args, stdout=out)
    return out.getvalue().splitlines()


@pytest.fixture()
def canvas_project(tmp_path):
    urlconf = write_urlconf(tmp_path, "canvas_urls", 'path("", include("next.urls"))')
    with (
        isolated_form_registries(),
        boundary_project(tmp_path, CANVAS_APPS, urlconf=urlconf),
    ):
        yield


class TestDebt:
    """Without an app the subcommand prints the debt per app and code."""

    def test_no_findings(self, tmp_path) -> None:
        with boundary_project(tmp_path, {"shop": {"views.py": "x = 1\n"}}):
            assert _run() == ["No boundary findings. Mode: warn."]

    def test_found_and_recorded_per_app_and_code(self, tmp_path) -> None:
        (tmp_path / "boundaries.toml").write_text(_STRAY_RECORD)

        with boundary_project(tmp_path, _PROJECT, framework={"BOUNDARIES": "strict"}):
            lines = _run()

        assert lines == [
            "App   Code       Found  Recorded",
            "-     next.W136  0      1",
            "shop  next.W136  2      0",
            "Findings: 2, recorded in boundaries.toml: 1. Mode: strict.",
        ]

    def test_a_broken_import_scan_is_an_error(self, tmp_path) -> None:
        with (
            boundary_project(tmp_path, _BROKEN),
            pytest.raises(CommandError, match="The import graph of the project"),
        ):
            _run()


class TestRefreeze:
    """`--refreeze` writes the ratchet and says what it left unrecorded."""

    def test_create_then_shrink(self, tmp_path) -> None:
        path = tmp_path / "boundaries.toml"

        with boundary_project(tmp_path, _PROJECT):
            created = _run("--refreeze")
            (tmp_path / "shop" / "views.py").write_text(
                _TWO_IMPORTS + "from crm.utils import third\n"
            )
            manager_module.boundaries_manager.reset()
            shrunk = _run("--refreeze")

        assert created == [f"Created {path}. Entries: 1, findings: 2."]
        assert shrunk == [
            f"Shrank {path}. Entries: 1, findings: 2.",
            (
                "Findings left unrecorded: 1. A refreeze only shrinks an existing file, "
                "so fix them, or delete boundaries.toml and refreeze again to record "
                "them."
            ),
        ]

    def test_it_takes_no_app(self, tmp_path) -> None:
        with (
            boundary_project(tmp_path, _PROJECT),
            pytest.raises(CommandError, match="takes no app"),
        ):
            _run("shop", "--refreeze")

        assert not (tmp_path / "boundaries.toml").exists()

    def test_it_needs_a_base_dir(self, tmp_path) -> None:
        with (
            boundary_project(tmp_path, _PROJECT, BASE_DIR=None),
            pytest.raises(CommandError, match="BASE_DIR is not set"),
        ):
            _run("--refreeze")

    def test_it_refuses_a_broken_import_scan(self, tmp_path) -> None:
        with (
            boundary_project(tmp_path, _BROKEN),
            pytest.raises(CommandError, match="The import graph of the project"),
        ):
            _run("--refreeze")

        assert not (tmp_path / "boundaries.toml").exists()


@pytest.mark.usefixtures("canvas_project")
class TestCanvas:
    """With an app the subcommand prints its Module Canvas, section by section."""

    def test_every_section(self) -> None:
        assert _run("shop") == [
            "shop (shop)",
            "",
            "Models",
            "  Cart",
            "  Order (Order.buyer -> crm.Client)",
            "",
            "Publishes",
            "  signal placed",
            "  service place_order",
            "  service refund",
            "",
            "Listens",
            "  crm.signals.changed of crm (shop/signals.py:9)",
            "",
            "DI keys",
            "  shop_cart (shop/deps.py:4)",
            "",
            "Pages",
            "Kind    Endpoint  Access  Sources",
            "page    /         public  public (shop/pages/page.py)",
            "page    /orders/  -       -",
            "action  cancel    login   shop/pages/page.py",
            "action  place     -       -",
            "",
            "Risks",
            (
                "  place (shop/pages/orders/page.py) is open to anyone, and nothing "
                "declares it public."
            ),
            "",
            "Depends on",
            "  crm: fk x1, import x1, signal x1",
            "",
            "Used by",
            "  crm: import x1",
        ]

    def test_empty_sections_show_a_dash(self) -> None:
        assert _run("crm") == [
            "crm (crm)",
            "",
            "Models",
            "  Client",
            "",
            "Publishes",
            "  signal changed",
            "  service billing.bill",
            "",
            "Listens",
            "  -",
            "",
            "DI keys",
            "  -",
            "",
            "Pages",
            "  -",
            "",
            "Risks",
            "  -",
            "",
            "Depends on",
            "  shop: import x1",
            "",
            "Used by",
            "  shop: fk x1, import x1, signal x1",
        ]

    def test_an_unknown_label(self) -> None:
        with pytest.raises(
            CommandError,
            match=re.escape(
                "'nope' is no application of the project. Its applications: crm, shop."
            ),
        ):
            _run("nope")


class TestWithoutGrimp:
    """Without grimp every mode stops with the install hint."""

    @pytest.mark.parametrize("args", [(), ("shop",), ("--refreeze",)])
    def test_the_install_hint(self, tmp_path, monkeypatch, args) -> None:
        monkeypatch.setattr(imports_module, "AVAILABLE", False)

        with (
            boundary_project(tmp_path, _PROJECT),
            pytest.raises(
                CommandError, match=r"Install the next\.dj\[boundaries\] extra"
            ),
        ):
            _run(*args)


class TestUncheckedProject:
    """A broken record or unchecked strict boundaries stop the debt table."""

    def test_a_broken_record_is_an_error(self, tmp_path) -> None:
        (tmp_path / "boundaries.toml").write_text("version = [\n")

        with (
            boundary_project(tmp_path, _PROJECT),
            pytest.raises(CommandError, match=r"boundaries\.toml cannot be used"),
        ):
            _run()

    @pytest.mark.parametrize("args", [(), ("shop",), ("--refreeze",)])
    def test_strict_without_base_dir_is_an_error(self, tmp_path, args) -> None:
        strict = {"BOUNDARIES": "strict"}
        message = (
            "NEXT_FRAMEWORK['BOUNDARIES'] is 'strict', but the boundaries go "
            "unchecked, because BASE_DIR is not set. Install the next.dj[boundaries] "
            "extra and point BASE_DIR at the project."
        )

        with (
            boundary_project(tmp_path, _PROJECT, framework=strict, BASE_DIR=None),
            pytest.raises(CommandError, match=re.escape(message)),
        ):
            _run(*args)
