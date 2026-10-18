import re
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from next.boundaries import imports as imports_module, manager as manager_module
from tests.support import isolated_form_registries, write_urlconf
from tests.support.boundaries import CANVAS_APPS, app_config, boundary_project


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

    def test_a_broken_import_scan_is_an_error(self, tmp_path, monkeypatch) -> None:
        builds = []
        build = manager_module._build

        def counted():
            builds.append(1)
            return build()

        monkeypatch.setattr(manager_module, "_build", counted)

        with (
            boundary_project(tmp_path, _BROKEN),
            pytest.raises(CommandError, match="The import graph of the project"),
        ):
            _run()

        assert len(builds) == 1

    def test_broken_declarations_come_first(self, tmp_path) -> None:
        apps = {"shop": {"apps.py": app_config("shop", isolated="yes")}}

        with boundary_project(tmp_path, apps):
            lines = _run()

        assert lines == [
            "Declarations",
            (
                "  shop/apps.py:6 next.E175 shop declares isolated as str instead of "
                "a bool."
            ),
            "",
            "No boundary findings. Mode: warn.",
        ]


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


class TestOff:
    """Without `BOUNDARIES` every mode stops, saying how to turn them on."""

    @pytest.mark.parametrize(
        "mode", [None, "strct", True], ids=["unset", "word", "bool"]
    )
    @pytest.mark.parametrize("args", [(), ("shop",), ("--refreeze",), ("sync",)])
    def test_every_mode_stops(self, tmp_path, args, mode) -> None:
        message = (
            "Boundaries are off, set NEXT_FRAMEWORK['BOUNDARIES'] to 'warn' or "
            "'strict'."
        )

        with (
            boundary_project(tmp_path, _PROJECT, framework={"BOUNDARIES": mode}),
            pytest.raises(CommandError, match=f"^{re.escape(message)}$"),
        ):
            _run(*args)

        assert not (tmp_path / "boundaries.toml").exists()

    def test_without_grimp_the_ast_reader_runs(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(imports_module, "AVAILABLE", False)

        with boundary_project(tmp_path, _PROJECT):
            lines = _run()

        assert lines[-1] == "Findings: 2, recorded in boundaries.toml: 0. Mode: warn."


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
            "unchecked, because BASE_DIR is not set. Point BASE_DIR at the folder "
            "that holds the project apps."
        )

        with (
            boundary_project(tmp_path, _PROJECT, framework=strict, BASE_DIR=None),
            pytest.raises(CommandError, match=re.escape(message)),
        ):
            _run(*args)


_DECLARED = {
    "proj.shop": {
        "apps.py": app_config("proj.shop", depends_on=("proj.crm",), isolated=True),
        "views.py": (
            "from proj.crm.services import notify\nfrom proj.billing.services import pay\n"
        ),
    },
    "proj.crm": {
        "apps.py": app_config("proj.crm", depends_on=("proj.billing",)),
        "services.py": "def notify():\n    pass\n",
    },
    "proj.billing": {"services.py": "def pay():\n    pass\n"},
}


class TestExact:
    """`--exact` lists each unused `depends_on` entry and fails when there is one."""

    def test_an_unused_entry_fails(self, tmp_path) -> None:
        out = StringIO()

        with (
            boundary_project(tmp_path, _DECLARED),
            pytest.raises(CommandError) as raised,
        ):
            call_command("next", "boundaries", "--exact", stdout=out)

        assert out.getvalue().splitlines() == [
            "App   Code       Found  Recorded",
            "shop  next.W150  1      0",
            "Findings: 1, recorded in boundaries.toml: 0. Mode: warn.",
            "",
            "Unused declarations",
            "  crm declares proj.billing, and no edge leads there.",
        ]
        assert str(raised.value) == "Entries of depends_on no edge uses: 1."
        assert raised.value.returncode == 1

    def test_every_entry_used(self, tmp_path) -> None:
        apps = {
            "shop": {
                "apps.py": app_config("shop", depends_on=("crm",)),
                "views.py": "from crm.services import notify\n",
            },
            "crm": {"services.py": "def notify():\n    pass\n"},
        }

        with boundary_project(tmp_path, apps):
            assert _run("--exact") == [
                "No boundary findings. Mode: warn.",
                "",
                "Every depends_on entry is used.",
            ]

    def test_nothing_declared(self, tmp_path) -> None:
        with boundary_project(tmp_path, {"shop": {"views.py": "x = 1\n"}}):
            assert _run("--exact") == [
                "No boundary findings. Mode: warn.",
                "",
                "No app declares depends_on, so there is nothing to compare.",
            ]


_SYNC_APPS = {
    "shop": {
        "apps.py": app_config("shop"),
        "views.py": "from crm.services import notify\n",
    },
    "crm": {
        "apps.py": app_config("crm", depends_on=()),
        "services.py": "def notify():\n    pass\n",
    },
}

_SYNC_DIFF = [
    "--- a/shop/apps.py",
    "+++ b/shop/apps.py",
    "@@ -3,3 +3,4 @@",
    " ",
    " class Config(AppConfig):",
    '     name = "shop"',
    '+    depends_on = ("crm",)',
]


class TestSync:
    """`sync` shows the diff of the names it adds, and `--add` writes them."""

    def test_the_preview_writes_nothing(self, tmp_path) -> None:
        with boundary_project(tmp_path, _SYNC_APPS):
            before = (tmp_path / "shop" / "apps.py").read_bytes()
            lines = _run("sync")
            after = (tmp_path / "shop" / "apps.py").read_bytes()

        assert lines == [*_SYNC_DIFF, "Run with --add to write it."]
        assert after == before

    def test_add_writes_the_same_diff(self, tmp_path) -> None:
        with boundary_project(tmp_path, _SYNC_APPS):
            lines = _run("sync", "--add")
            manager_module.boundaries_manager.reset()
            again = _run("sync")

        assert lines == _SYNC_DIFF
        assert (tmp_path / "shop" / "apps.py").read_text() == (
            app_config("shop") + '    depends_on = ("crm",)\n'
        )
        assert again == ["Nothing to add."]

    @pytest.mark.parametrize("key", ["shop", "crm"])
    def test_one_app(self, tmp_path, key) -> None:
        with boundary_project(tmp_path, _SYNC_APPS):
            lines = _run("sync", key)

        assert lines == (
            [*_SYNC_DIFF, "Run with --add to write it."]
            if key == "shop"
            else ["Nothing to add."]
        )

    @pytest.mark.parametrize(
        ("apps", "head"),
        [
            (_SYNC_APPS, [*_SYNC_DIFF, "Run with --add to write it."]),
            ({"crm": _SYNC_APPS["crm"]}, ["Nothing to add."]),
        ],
        ids=["with_changes", "without_changes"],
    )
    def test_an_app_without_its_own_config_is_skipped(
        self, tmp_path, apps, head
    ) -> None:
        with boundary_project(tmp_path, {**apps, "plain": {"apps.py": "VALUE = 1\n"}}):
            lines = _run("sync")

        assert lines == [
            *head,
            (
                "Skipped apps with no AppConfig class of their own in the project: 1 "
                "(plain). Give each an apps.py with an AppConfig subclass to sync it."
            ),
        ]

    def test_an_unknown_app(self, tmp_path) -> None:
        with (
            boundary_project(tmp_path, _SYNC_APPS),
            pytest.raises(CommandError, match="'nope' is no application"),
        ):
            _run("sync", "nope")

    def test_a_refusal_writes_no_file(self, tmp_path) -> None:
        apps = {
            **_SYNC_APPS,
            "crm": {
                **_SYNC_APPS["crm"],
                "apps.py": app_config("crm", "depends_on = () + ()"),
                "views.py": "from shop.services import x\n",
            },
            "billing": {},
        }
        apps["shop"] = {**apps["shop"], "services.py": "x = 1\n"}
        message = (
            "crm/apps.py, line 6: depends_on is no literal tuple of strings. Edit "
            "depends_on there by hand."
        )

        with boundary_project(tmp_path, apps):
            files = [tmp_path / label / "apps.py" for label in ("billing", "shop")]
            before = [path.read_bytes() for path in files]
            with pytest.raises(CommandError, match=re.escape(message)):
                _run("sync", "--add")
            after = [path.read_bytes() for path in files]

        assert after == before


_KERNEL_APPS = {
    "core": {"services.py": "def ping():\n    pass\n"},
    "shop": {"views.py": "from core.services import ping\n"},
}

_KERNEL_LINE = (
    "Shared kernel core: 3 modules, 5 lines, used by 1 app. Keep it small, and "
    "change it with the consent of the apps that use it."
)


class TestSharedKernel:
    """The debt names the shared kernel with its size and its users."""

    def test_after_no_findings(self, tmp_path) -> None:
        with boundary_project(tmp_path, _KERNEL_APPS):
            assert _run() == ["No boundary findings. Mode: warn.", _KERNEL_LINE]

    def test_after_the_table(self, tmp_path) -> None:
        apps = {**_KERNEL_APPS, "core": {**_KERNEL_APPS["core"], "utils.py": "x = 1\n"}}
        apps["shop"] = {
            "views.py": "from core.services import ping\nfrom core.utils import x\n"
        }

        with boundary_project(tmp_path, apps):
            lines = _run()

        assert lines == [
            "App   Code       Found  Recorded",
            "shop  next.W136  1      0",
            "Findings: 1, recorded in boundaries.toml: 0. Mode: warn.",
            _KERNEL_LINE.replace("3 modules, 5 lines", "4 modules, 6 lines"),
        ]

    def test_no_kernel_no_line(self, tmp_path) -> None:
        with boundary_project(tmp_path, {"shop": {"views.py": "x = 1\n"}}):
            assert _run() == ["No boundary findings. Mode: warn."]


class TestDeclaredCanvas:
    """The canvas finds an app by label or name and shows what it declares."""

    @pytest.mark.parametrize("key", ["shop", "proj.shop"])
    def test_isolated_and_undeclared(self, tmp_path, key) -> None:
        with boundary_project(tmp_path, _DECLARED):
            lines = _run(key)

        assert lines[:2] == ["shop (proj/shop)", "Isolated: yes"]
        assert lines[-7:] == [
            "",
            "Depends on",
            "  billing: import x1 (undeclared)",
            "  crm: import x1",
            "",
            "Used by",
            "  -",
        ]

    def test_an_app_without_declarations(self, tmp_path) -> None:
        with boundary_project(tmp_path, _DECLARED):
            lines = _run("billing")

        assert lines == [
            "billing (proj/billing)",
            "",
            "Models",
            "  -",
            "",
            "Publishes",
            "  service pay",
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
            "  -",
            "",
            "Used by",
            "  shop: import x1",
        ]

    def test_an_unknown_key_lists_the_apps(self, tmp_path) -> None:
        with (
            boundary_project(tmp_path, _DECLARED),
            pytest.raises(
                CommandError,
                match=re.escape(
                    "'proj.nope' is no application of the project. Its applications: "
                    "billing, crm, shop."
                ),
            ),
        ):
            _run("proj.nope")


class TestOptionsRefused:
    """Options that do not go together stop before any scan."""

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (("shop", "crm"), "Only sync takes a second app."),
            (("--add",), "--add only goes with sync."),
            (("shop", "--add"), "--add only goes with sync."),
            (
                ("sync", "--refreeze"),
                "--refreeze records the whole project and takes no app.",
            ),
            (
                ("sync", "--exact"),
                "--exact compares the whole project and takes no app.",
            ),
            (
                ("shop", "--exact"),
                "--exact compares the whole project and takes no app.",
            ),
        ],
    )
    def test_the_refusal(self, tmp_path, monkeypatch, args, message) -> None:
        monkeypatch.setattr(imports_module, "AVAILABLE", False)

        with (
            boundary_project(tmp_path, _PROJECT),
            pytest.raises(CommandError, match=f"^{re.escape(message)}$"),
        ):
            _run(*args)
