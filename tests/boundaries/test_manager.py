import pytest
from django.conf import settings as django_settings
from django.test import override_settings

from next.boundaries import imports as imports_module
from next.boundaries.baseline import Debt
from next.boundaries.declared import NO_DECLARATIONS, Declaration
from next.boundaries.errors import BaselineError, ImportScanError
from next.boundaries.manager import BoundariesManager, Refrozen, boundaries_manager
from next.boundaries.rules import (
    BASELINE,
    CYCLE,
    GROWN,
    SCAN,
    STALE,
    STRICT_OFF,
    UNRECORDED,
)
from next.boundaries.signals import baseline_refrozen
from next.testing import capture_signals
from tests.support.boundaries import app_config, boundary_project


_W = "next.W136"

_TWO_IMPORTS = "from crm.utils import first\nfrom crm.utils import second\n"

_PROJECT = {
    "shop": {"views.py": _TWO_IMPORTS},
    "crm": {"utils.py": "first = second = third = 1\n"},
}

_CYCLE = {
    "alpha": {"services.py": "from beta.models import thing\n"},
    "beta": {"models.py": "thing = 1\n", "services.py": "import alpha.services\n"},
}

_STRICT = {"BOUNDARIES": "strict"}

_SHOP_NEEDS_CRM = app_config("shop", depends_on=("crm",))

_SPLIT = {
    "shop": {"views.py": "from crm.utils import first\n"},
    "crm": {"utils.py": "first = 1\n", "views.py": "from blog.helpers import x\n"},
    "blog": {"helpers.py": "x = 1\n"},
}

_DECLARED = {**_SPLIT, "shop": {**_SPLIT["shop"], "apps.py": _SHOP_NEEDS_CRM}}

_QUIET_CRM = {**_SPLIT, "crm": {"utils.py": "first = 1\n"}}


def _rows(report) -> list[tuple[str, int, str]]:
    return [(finding.file, finding.line, finding.code) for finding in report.findings]


def _without_crm() -> override_settings:
    boundaries_manager.reset()
    return override_settings(
        INSTALLED_APPS=[
            name for name in django_settings.INSTALLED_APPS if name != "crm"
        ]
    )


def _rescan_with(root, text: str) -> None:
    (root / "shop" / "views.py").write_text(text)
    boundaries_manager.reset()


class TestModes:
    """Without a ratchet warn shows the findings and strict also fails on them."""

    def test_warn_without_a_file(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT):
            report = boundaries_manager.report()

        assert report.mode == "warn"
        assert _rows(report) == [("shop/views.py", 1, _W), ("shop/views.py", 2, _W)]
        assert report.debts == (Debt("shop", _W, "shop/views.py", 2, 0),)

    def test_strict_without_a_file(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT, framework=_STRICT):
            report = boundaries_manager.report()

        assert report.mode == "strict"
        assert _rows(report)[-1] == ("boundaries.toml", 0, UNRECORDED)
        assert report.findings[-1].message == (
            "Findings missing from boundaries.toml: 2, and "
            "NEXT_FRAMEWORK['BOUNDARIES'] is 'strict'."
        )

    def test_strict_counts_only_the_warnings(self, tmp_path) -> None:
        with boundary_project(tmp_path, _CYCLE, framework=_STRICT):
            report = boundaries_manager.report()

        assert _rows(report) == [
            ("alpha/services.py", 1, CYCLE),
            ("beta/services.py", 1, CYCLE),
        ]

    def test_a_mode_that_is_no_string_is_not_strict(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"BOUNDARIES": True}):
            assert (boundaries_manager.mode, boundaries_manager.strict) == (
                "warn",
                False,
            )

    def test_a_check_writes_no_file(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT, framework=_STRICT):
            boundaries_manager.report()

        assert not (tmp_path / "boundaries.toml").exists()


class TestRefreeze:
    """A refreeze records every finding once, then only shrinks what it recorded."""

    def test_it_creates_the_file_and_covers_the_findings(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT, framework=_STRICT):
            refrozen = boundaries_manager.refreeze()
            report = boundaries_manager.report()

        assert refrozen == Refrozen(
            path=tmp_path / "boundaries.toml",
            created=True,
            entries=1,
            recorded=2,
            unrecorded=0,
        )
        assert (tmp_path / "boundaries.toml").read_text() == (
            'version = 1\n\n[[violation]]\nfile = "shop/views.py"\n'
            'code = "next.W136"\ncount = 2\n'
        )
        assert report.findings == ()
        assert report.debts == (Debt("shop", _W, "shop/views.py", 2, 2),)

    def test_it_sends_the_signal(self, tmp_path) -> None:
        with (
            boundary_project(tmp_path, _PROJECT),
            capture_signals(baseline_refrozen) as recorder,
        ):
            refrozen = boundaries_manager.refreeze()

        (event,) = recorder.events
        assert event.sender is BoundariesManager
        assert event.kwargs == {"refrozen": refrozen}

    def test_it_shrinks_and_never_records_a_new_finding(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT):
            boundaries_manager.refreeze()
            _rescan_with(tmp_path, "from crm.utils import first\n")
            shrunk = boundaries_manager.refreeze()
            _rescan_with(tmp_path, _TWO_IMPORTS + "from crm.utils import third\n")
            kept = boundaries_manager.refreeze()
            report = boundaries_manager.report()

        assert (shrunk.created, shrunk.recorded, shrunk.unrecorded) == (False, 1, 0)
        assert (kept.created, kept.recorded, kept.unrecorded) == (False, 1, 2)
        assert _rows(report) == [("shop/views.py", 0, GROWN)]

    def test_it_needs_a_base_dir(self, tmp_path) -> None:
        with (
            boundary_project(tmp_path, _PROJECT, BASE_DIR=None),
            pytest.raises(BaselineError, match="BASE_DIR is not set"),
        ):
            boundaries_manager.refreeze()

    def test_it_refuses_a_broken_import_scan(self, tmp_path) -> None:
        broken = {"shop": {"views.py": "def broken(:\n"}}

        with boundary_project(tmp_path, broken), pytest.raises(ImportScanError):
            boundaries_manager.refreeze()

        assert not (tmp_path / "boundaries.toml").exists()


class TestRatchet:
    """A recorded count must match its findings exactly, more or fewer is an error."""

    def test_more_findings_than_recorded(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT):
            boundaries_manager.refreeze()
            _rescan_with(tmp_path, _TWO_IMPORTS + "from crm.utils import third\n")
            report = boundaries_manager.report()

        assert _rows(report) == [("shop/views.py", 0, GROWN)]
        assert report.findings[0].message.endswith("Their lines: 1, 2, 3.")

    @pytest.mark.parametrize(
        ("text", "remain"),
        [("from crm.utils import first\n", 1), ("", 0)],
        ids=["fewer", "none"],
    )
    def test_a_stale_record(self, tmp_path, text: str, remain: int) -> None:
        with boundary_project(tmp_path, _PROJECT):
            boundaries_manager.refreeze()
            _rescan_with(tmp_path, text)
            report = boundaries_manager.report()

        assert _rows(report) == [("shop/views.py", 0, STALE)]
        assert report.findings[0].app == "shop"
        assert report.findings[0].message == (
            "Findings of next.W136 in shop/views.py recorded in boundaries.toml: 2, "
            f"remaining: {remain}."
        )

    @pytest.mark.parametrize(
        "text",
        ["version = [\n", "version = 1\nowner = 'me'\n"],
        ids=["broken_toml", "wrong_schema"],
    )
    def test_an_unreadable_file(self, tmp_path, text: str) -> None:
        (tmp_path / "boundaries.toml").write_text(text)

        with boundary_project(tmp_path, _PROJECT, framework=_STRICT):
            report = boundaries_manager.report()

        assert _rows(report) == [
            ("shop/views.py", 1, _W),
            ("shop/views.py", 2, _W),
            ("boundaries.toml", 0, BASELINE),
            ("boundaries.toml", 0, UNRECORDED),
        ]

    def test_a_record_of_a_file_no_app_holds(self, tmp_path) -> None:
        (tmp_path / "boundaries.toml").write_text(
            'version = 1\n[[violation]]\nfile = "elsewhere.py"\ncode = "next.W136"\n'
            "count = 1\n"
        )

        with boundary_project(tmp_path, _PROJECT):
            report = boundaries_manager.report()

        assert [(f.file, f.code, f.app) for f in report.findings][-1] == (
            "elsewhere.py",
            STALE,
            "",
        )

    def test_a_record_in_a_project_without_apps(self, tmp_path) -> None:
        (tmp_path / "boundaries.toml").write_text(
            'version = 1\n[[violation]]\nfile = "shop/views.py"\ncode = "next.W136"\n'
            "count = 1\n"
        )

        with boundary_project(tmp_path, {}):
            report = boundaries_manager.report()

        assert [(f.file, f.code, f.app) for f in report.findings] == [
            ("shop/views.py", STALE, "")
        ]

    def test_without_a_base_dir_there_is_no_record(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT, BASE_DIR=None):
            report = boundaries_manager.report()

        assert report.findings == ()
        assert report.debts == ()

    def test_a_broken_import_scan_stands_alone(self, tmp_path) -> None:
        (tmp_path / "boundaries.toml").write_text("version = [\n")
        broken = {"shop": {"views.py": "def broken(:\n"}}

        with boundary_project(tmp_path, broken, framework=_STRICT):
            report = boundaries_manager.report()

        assert _rows(report) == [("", 0, SCAN)]
        assert report.findings[0].message.startswith("The import graph of the project")
        assert report.debts == ()


class TestScan:
    """The scan is built once per check run and needs grimp."""

    def test_it_is_kept_until_reset(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT):
            first = boundaries_manager.scan()
            kept = boundaries_manager.scan()
            boundaries_manager.reset()
            rebuilt = boundaries_manager.scan()

        assert kept is first
        assert rebuilt is not first
        assert rebuilt.findings == first.findings

    def test_the_graph_and_the_findings(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT):
            graph = boundaries_manager.graph()
            findings = boundaries_manager.findings()

        assert [app.label for app in graph.apps] == ["crm", "shop"]
        assert [finding.code for finding in findings] == [_W, _W]

    def test_without_grimp_the_ast_reader_scans(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(imports_module, "AVAILABLE", False)

        with boundary_project(tmp_path, _PROJECT):
            findings = boundaries_manager.findings()

        assert [finding.code for finding in findings] == [_W, _W]

    @pytest.mark.parametrize(
        ("mode", "enabled"), [(None, False), ("warn", True), ("strict", True)]
    )
    def test_enabled_by_the_mode(self, mode, enabled) -> None:
        with override_settings(NEXT_FRAMEWORK={"BOUNDARIES": mode}):
            assert boundaries_manager.enabled is enabled


class TestDisabled:
    """Unchecked boundaries say why, as a warning when the mode is strict."""

    def test_checked_boundaries_answer_none(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT, framework=_STRICT):
            assert boundaries_manager.disabled() is None

    def test_without_base_dir_the_warn_mode_stays_quiet(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT, BASE_DIR=None):
            assert boundaries_manager.disabled() is None

    def test_the_strict_mode_warns_without_base_dir(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT, framework=_STRICT, BASE_DIR=None):
            off = boundaries_manager.disabled()

        assert off is not None
        assert (off.code, off.message) == (
            STRICT_OFF,
            (
                "NEXT_FRAMEWORK['BOUNDARIES'] is 'strict', but the boundaries go "
                "unchecked, because BASE_DIR is not set."
            ),
        )


class TestTreeSyntax:
    """A broken file grimp never reads stops the scan as grimp would."""

    def test_a_broken_page_file_is_the_scan_error(self, tmp_path) -> None:
        broken = {"shop": {"pages/page.py": "x = 1\n\ndef broken(:\n"}}

        with boundary_project(tmp_path, broken):
            report = boundaries_manager.report()

        assert _rows(report) == [("shop/pages/page.py", 3, SCAN)]
        assert report.findings[0].message == (
            "The import graph of the project cannot be built. shop/pages/page.py, "
            "line 3: invalid syntax"
        )


class TestEscapingImport:
    """A relative import above its package is named before grimp reads it."""

    def test_it_is_the_scan_error_with_its_file_and_line(self, tmp_path) -> None:
        escaping = {
            "shop": {"orders/__init__.py": "", "orders/x.py": "\nfrom ... import far\n"}
        }

        with boundary_project(tmp_path, escaping):
            report = boundaries_manager.report()

        assert _rows(report) == [("shop/orders/x.py", 2, SCAN)]
        assert report.findings[0].message == (
            "The import graph of the project cannot be built. shop/orders/x.py, "
            "line 2: The relative import reaches above its package."
        )

    def test_a_page_tree_file_is_no_concern_of_grimp(self, tmp_path) -> None:
        tree = {"shop": {"pages/page.py": "from ...... import far\n"}}

        with boundary_project(tmp_path, tree):
            report = boundaries_manager.report()

        assert report.findings == ()


class TestDeclarations:
    """A scan keeps what each app declares, read once with the scan."""

    def test_the_scan_holds_them(self, tmp_path) -> None:
        apps = {
            "shop": {"apps.py": app_config("shop", depends_on=("crm",), isolated=True)},
            "crm": {},
        }

        with boundary_project(tmp_path, apps):
            scan = boundaries_manager.scan()

        assert scan.declarations.of("shop") == Declaration(
            "shop", "shop", ("crm",), True
        )
        assert scan.declarations.isolated == frozenset({"shop"})

    def test_an_empty_scan_declares_nothing(self, tmp_path) -> None:
        with boundary_project(tmp_path, {"shop": {}}, BASE_DIR=None):
            assert boundaries_manager.scan().declarations == NO_DECLARATIONS


class TestReducedDeploy:
    """Settings that leave an app out judge only growth and set its records aside."""

    def test_no_stale_record_and_no_debt_of_the_dropped_app(self, tmp_path) -> None:
        with boundary_project(tmp_path, _SPLIT, framework=_STRICT):
            boundaries_manager.refreeze()
            with _without_crm():
                report = boundaries_manager.report()

        assert report.findings == ()
        assert report.debts == (Debt("shop", _W, "shop/views.py", 0, 1),)

    def test_a_refreeze_keeps_what_it_cannot_judge(self, tmp_path) -> None:
        with boundary_project(tmp_path, _SPLIT):
            boundaries_manager.refreeze()
            before = (tmp_path / "boundaries.toml").read_text()
            with _without_crm():
                refrozen = boundaries_manager.refreeze()

        assert (refrozen.entries, refrozen.recorded) == (2, 2)
        assert (tmp_path / "boundaries.toml").read_text() == before

    def test_a_deleted_file_is_still_stale(self, tmp_path) -> None:
        with boundary_project(tmp_path, _DECLARED):
            boundaries_manager.refreeze()
            (tmp_path / "crm" / "views.py").unlink()
            with _without_crm():
                report = boundaries_manager.report()

        assert _rows(report) == [("crm/views.py", 0, STALE)]

    @pytest.mark.parametrize(
        ("apps", "rows"),
        [
            (_QUIET_CRM, [("shop/views.py", 0, STALE)]),
            (
                {
                    **_QUIET_CRM,
                    "shop": {**_QUIET_CRM["shop"], "apps.py": _SHOP_NEEDS_CRM},
                },
                [],
            ),
        ],
        ids=["undeclared", "declared"],
    )
    def test_a_dropped_app_without_records_shows_by_depends_on(
        self, tmp_path, apps, rows
    ) -> None:
        with boundary_project(tmp_path, apps):
            boundaries_manager.refreeze()
            with _without_crm():
                report = boundaries_manager.report()

        assert _rows(report) == rows

    def test_a_stray_app_package_leaves_the_whole_project_judged(
        self, tmp_path
    ) -> None:
        with boundary_project(tmp_path, _SPLIT):
            boundaries_manager.refreeze()
            copy = tmp_path / "build" / "lib" / "crm"
            copy.mkdir(parents=True)
            for name in ("__init__.py", "apps.py"):
                (copy / name).write_text("")
            _rescan_with(tmp_path, "")
            report = boundaries_manager.report()

        assert _rows(report) == [("shop/views.py", 0, STALE)]

    def test_growth_is_still_judged(self, tmp_path) -> None:
        with boundary_project(tmp_path, _SPLIT):
            boundaries_manager.refreeze()
            (tmp_path / "shop" / "views.py").write_text(
                "from blog.helpers import x\nfrom blog import helpers\n"
            )
            with _without_crm():
                report = boundaries_manager.report()

        assert _rows(report) == [("shop/views.py", 0, GROWN)]
