import sys
from types import ModuleType

import pytest
from django.apps import AppConfig
from django.conf import ENVIRONMENT_VARIABLE

from next.boundaries import declared
from next.boundaries.declared import (
    NO_DECLARATIONS,
    Declaration,
    Unused,
    read_declarations,
    unused_declarations,
)
from next.boundaries.manager import boundaries_manager
from next.boundaries.rules import CLOSURE, DECLARATIONS, UNDECLARED
from tests.support.boundaries import app_config, boundary_project, scanned
from tests.support.cases.boundaries import (
    EDGE_KIND_CASES,
    REFUSED_DECLARATIONS,
    EdgeKindCase,
    RefusedDeclaration,
)


_CRM = {
    "models.py": (
        "from django.db import models\n\n\nclass Client(models.Model):\n    pass\n"
    ),
    "services.py": "def notify():\n    pass\n",
}


def _shop(*lines: str, **attributes: object) -> dict[str, dict[str, str]]:
    return {"shop": {"apps.py": app_config("shop", *lines, **attributes)}}


def _findings(code: str) -> list[tuple[str, int, str, str]]:
    return [
        (finding.file, finding.line, finding.app, finding.message)
        for finding in read_declarations().findings
        if finding.code == code
    ]


class TestReadDeclarations:
    """Each app declares `depends_on` and `isolated`, both optional."""

    def test_an_app_with_none_declares_nothing(self, tmp_path) -> None:
        with boundary_project(tmp_path, _shop()):
            found = read_declarations()

        assert found.findings == ()
        assert found.of("shop") == Declaration("shop", "shop", None, False)
        assert found.of("nope") is None
        assert found.isolated == frozenset()

    def test_every_attribute_read(self, tmp_path) -> None:
        apps = {
            **_shop(depends_on=("crm",), isolated=True),
            "crm": {"apps.py": app_config("crm", depends_on=())},
        }

        with boundary_project(tmp_path, apps):
            found = read_declarations()

        assert found.findings == ()
        assert found.of("shop") == Declaration("shop", "shop", ("crm",), True)
        assert found.of("crm") == Declaration("crm", "crm", (), False)
        assert found.isolated == frozenset({"shop"})

    def test_an_installed_app_outside_the_project_is_left_alone(self, tmp_path) -> None:
        apps = {
            **_shop(depends_on=("django.contrib.auth",)),
            "vendor": {"apps.py": app_config("vendor", depends_on=["crm"], isolated=1)},
        }

        with boundary_project(tmp_path, apps, BASE_DIR=tmp_path / "shop"):
            found = read_declarations()

        assert found.findings == ()
        assert found.apps == (
            Declaration("shop", "shop", ("django.contrib.auth",), False),
        )
        assert found.of("vendor") is None

    def test_without_base_dir_nothing_is_read(self, tmp_path) -> None:
        with boundary_project(tmp_path, _shop(depends_on=["crm"]), BASE_DIR=None):
            assert read_declarations() == NO_DECLARATIONS


class TestRefused:
    """A broken attribute is one E175 at its line, and counts as never declared."""

    @pytest.mark.parametrize("case", REFUSED_DECLARATIONS, ids=lambda case: case.id)
    def test_the_finding(self, tmp_path, case: RefusedDeclaration) -> None:
        with boundary_project(tmp_path, _shop(**case.attributes)):
            found = read_declarations()

        assert [
            (item.file, item.line, item.code, item.app, item.message)
            for item in found.findings
        ] == [("shop/apps.py", 6, DECLARATIONS, "shop", case.message)]
        assert found.of("shop") == Declaration("shop", "shop", None, False)

    def test_two_broken_attributes_are_two_findings(self, tmp_path) -> None:
        apps = _shop(depends_on="crm", isolated=None)

        with boundary_project(tmp_path, apps):
            assert [
                line for _file, line, _app, _message in _findings(DECLARATIONS)
            ] == [6, 7]

    def test_a_broken_depends_on_causes_no_undeclared_finding(self, tmp_path) -> None:
        apps = {**_shop(depends_on=["billing"]), "crm": _CRM}
        apps["shop"]["views.py"] = "from crm.services import notify\n"

        scan = scanned(tmp_path, apps)

        assert scan.findings == ()
        assert [item.code for item in scan.declarations.findings] == [DECLARATIONS]

    def test_an_inherited_attribute_points_at_its_class(self, tmp_path) -> None:
        apps = {
            "shop": {
                "base.py": (
                    "from django.apps import AppConfig\n\n\n"
                    "class Base(AppConfig):\n"
                    "    default = False\n"
                    "    depends_on = ['crm']\n"
                ),
                "apps.py": (
                    "from shop.base import Base\n\n\n"
                    "class Config(Base):\n"
                    "    default = True\n"
                    "    name = 'shop'\n"
                ),
            }
        }

        with boundary_project(tmp_path, apps):
            (finding,) = _findings(DECLARATIONS)

        assert finding[:2] == ("shop/base.py", 6)

    def test_an_attribute_set_in_ready_points_at_the_class(self, tmp_path) -> None:
        apps = _shop("def ready(self):", "    self.isolated = 'no'")

        with boundary_project(tmp_path, apps):
            (finding,) = _findings(DECLARATIONS)

        assert finding == (
            "shop/apps.py",
            4,
            "shop",
            "shop declares isolated as str instead of a bool.",
        )

    def test_a_class_without_a_file_has_no_place(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "virtual_config", ModuleType("virtual_config"))
        attributes = {"__module__": "virtual_config", "depends_on": "crm"}
        virtual = type("Virtual", (AppConfig,), attributes)

        with boundary_project(tmp_path, _shop()):
            config = virtual("shop", sys.modules["shop"])

            assert declared._where(config, "depends_on") == ("", 0)

    def test_a_file_outside_base_dir_is_shown_whole(
        self, tmp_path, monkeypatch
    ) -> None:
        source = tmp_path / "vendor_config.py"
        source.write_text(
            "from django.apps import AppConfig\n\n\n"
            "class Vendor(AppConfig):\n"
            "    depends_on = 'crm'\n"
        )
        module = ModuleType("vendor_config")
        module.__file__ = str(source)
        monkeypatch.setitem(sys.modules, "vendor_config", module)
        attributes = {"__module__": "vendor_config", "depends_on": "crm"}
        vendor = type("Vendor", (AppConfig,), attributes)

        with boundary_project(tmp_path / "project", _shop()):
            config = vendor("shop", sys.modules["shop"])
            where = declared._where(config, "depends_on")

        assert where == (source.resolve().as_posix(), 5)


class TestClosure:
    """A name of `depends_on` the settings do not install is one E174 per name."""

    def test_a_missing_app_named_by_the_environment(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv(ENVIRONMENT_VARIABLE, "config.cut")

        with boundary_project(tmp_path, _shop(depends_on=("crm", "billing"))):
            assert _findings(CLOSURE) == [
                (
                    "shop/apps.py",
                    6,
                    "shop",
                    "shop depends on billing, which config.cut does not install.",
                ),
                (
                    "shop/apps.py",
                    6,
                    "shop",
                    "shop depends on crm, which config.cut does not install.",
                ),
            ]

    def test_a_missing_app_named_by_the_settings_module(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.delenv(ENVIRONMENT_VARIABLE, raising=False)
        apps = _shop(depends_on=("crm",))

        with boundary_project(tmp_path, apps, SETTINGS_MODULE="config.deploy"):
            ((_file, _line, _app, message),) = _findings(CLOSURE)

        assert message == "shop depends on crm, which config.deploy does not install."

    def test_configured_settings_have_no_module(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv(ENVIRONMENT_VARIABLE, raising=False)

        with boundary_project(tmp_path, _shop(depends_on=("crm",))):
            ((_file, _line, _app, message),) = _findings(CLOSURE)

        assert message == "shop depends on crm, which these settings do not install."

    def test_a_label_in_place_of_a_name(self, tmp_path) -> None:
        with boundary_project(tmp_path, _shop(depends_on=("auth",))):
            ((_file, _line, _app, message),) = _findings(CLOSURE)

        assert message == (
            "shop depends on auth, the label of django.contrib.auth, and depends_on "
            "takes app names."
        )

    def test_an_installed_app_is_no_finding(self, tmp_path) -> None:
        apps = {**_shop(depends_on=("crm", "django.contrib.auth")), "crm": _CRM}

        with boundary_project(tmp_path, apps):
            assert read_declarations().findings == ()


class TestUndeclared:
    """An edge to an app `depends_on` leaves out is one W150 per pair of apps."""

    @pytest.mark.parametrize("case", EDGE_KIND_CASES, ids=lambda case: case.id)
    def test_every_kind_of_edge(self, tmp_path, case: EdgeKindCase) -> None:
        apps = {
            "shop": {"apps.py": app_config("shop", depends_on=()), **case.shop},
            "crm": {**_CRM, **case.crm},
        }

        scan = scanned(tmp_path, apps, load=case.load)

        assert [
            (item.file, item.line, item.app, item.message)
            for item in scan.findings
            if item.code == UNDECLARED
        ] == [
            (
                f"shop/{case.file}",
                case.line,
                "shop",
                (
                    f"shop depends on crm by {case.id} x1, and its depends_on does not "
                    "declare crm."
                ),
            )
        ]

    @pytest.mark.parametrize("case", EDGE_KIND_CASES, ids=lambda case: case.id)
    def test_a_declared_app_is_quiet(self, tmp_path, case: EdgeKindCase) -> None:
        apps = {
            "shop": {"apps.py": app_config("shop", depends_on=("crm",)), **case.shop},
            "crm": {**_CRM, **case.crm},
        }

        scan = scanned(tmp_path, apps, load=case.load)

        assert [item for item in scan.findings if item.code == UNDECLARED] == []
        assert {edge.kind for edge in scan.graph.edges} == {case.id}

    def test_one_finding_counts_every_kind_at_the_first_edge(self, tmp_path) -> None:
        apps = {
            "shop": {
                "apps.py": app_config("shop", depends_on=("django.contrib.auth",)),
                "views.py": (
                    "from crm.services import notify\nfrom crm.models import Client\n"
                ),
                "models.py": (
                    "from django.db import models\n\n\n"
                    "class Order(models.Model):\n"
                    '    buyer = models.ForeignKey("crm.Client", '
                    "on_delete=models.CASCADE)\n"
                ),
            },
            "crm": _CRM,
            "billing": {"services.py": "def pay():\n    pass\n"},
        }
        apps["shop"]["views.py"] += "from billing.services import pay\n"

        scan = scanned(tmp_path, apps)

        assert [
            (item.file, item.line, item.message)
            for item in scan.findings
            if item.code == UNDECLARED
        ] == [
            (
                "shop/models.py",
                5,
                (
                    "shop depends on crm by fk x1, import x2, and its depends_on does not "
                    "declare crm."
                ),
            ),
            (
                "shop/views.py",
                3,
                (
                    "shop depends on billing by import x1, and its depends_on does not "
                    "declare billing."
                ),
            ),
        ]

    def test_without_depends_on_no_finding(self, tmp_path) -> None:
        apps = {"shop": {"views.py": "from crm.services import notify\n"}, "crm": _CRM}

        scan = scanned(tmp_path, apps)

        assert [item for item in scan.findings if item.code == UNDECLARED] == []


class TestUnused:
    """`--exact` lists each `depends_on` entry of a project app no edge leads to."""

    def test_the_entries(self, tmp_path) -> None:
        apps = {
            "shop": {
                "apps.py": app_config(
                    "shop", depends_on=("crm", "billing", "django.contrib.auth")
                ),
                "views.py": "from crm.services import notify\n",
            },
            "crm": {**_CRM, "apps.py": app_config("crm", depends_on=())},
            "billing": {},
            "audit": {},
        }

        with boundary_project(tmp_path, apps):
            scan = boundaries_manager.scan()
            unused = unused_declarations(scan.graph, scan.declarations)

        assert unused == Unused(("crm", "shop"), (("shop", "billing"),))

    def test_without_declarations(self, tmp_path) -> None:
        with boundary_project(tmp_path, {"shop": {}}):
            scan = boundaries_manager.scan()

            assert unused_declarations(scan.graph, scan.declarations) == Unused((), ())
