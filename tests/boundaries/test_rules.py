from pathlib import Path

import pytest

from next.boundaries.graph import App
from next.boundaries.rules import (
    CYCLE,
    DOCTRINE,
    FOREIGN_KEY_NAME,
    FOREIGN_MODEL,
    FOREIGN_ZONE,
    GROWN,
    ISOLATED_FOREIGN_KEY,
    PRIVATE_IMPORT,
    RECORDABLE,
    RULES,
    STRAY_RECEIVER,
    STRICT_OFF,
    TREE_IMPORT,
    UNDECLARED,
    Rule,
    public,
)
from next.boundaries.sources import Project
from tests.support.boundaries import app_config, finding_rows, scanned


_RECEIVER = """from django.dispatch import receiver

from crm.signals import changed


@receiver(changed)
def on_changed(sender, **kwargs):
    pass
"""

_SHOP_DEPS = """from next.deps import resolver


def provide():
    return None


resolver.register_dependency("crm_thing", provide)
resolver.register_dependency("crm.other", provide)
resolver.register_dependency("shop_cart", provide)
resolver.register_dependency("scope", provide)
resolver.register_dependency("billing_total", provide)
"""

_RULE_PROJECT = {
    "shop": {
        "views.py": (
            "from crm.utils import helper\n"
            "from crm.models import Client\n"
            "from crm.services.orders import place\n"
            "from crm import signals\n"
            "from crm.pages import helpers\n"
            "from crm._components.card import component\n"
            "from shop.pages import own\n"
        ),
        "services.py": (
            "from django.apps import apps\n\n"
            "client = apps.get_model('crm', 'Client')\n"
            "own = apps.get_model('shop.Order')\n"
        ),
        "tasks.py": _RECEIVER,
        "signals.py": _RECEIVER,
        "deps.py": _SHOP_DEPS,
        "pages/page.py": (
            "def render(request):\n"
            "    Patches(request).refresh(zone='feed')\n"
            "    Patches(request).refresh(zone='mine')\n\n\n"
            "template = \"{% zone 'mine' %}x{% endzone %}\"\n"
        ),
        "pages/own.py": "",
    },
    "crm": {
        "models.py": "from django.db import models\n\n\nclass Client(models.Model):\n    pass\n",
        "utils.py": "helper = 1\n",
        "services/__init__.py": "",
        "services/orders.py": "def place():\n    pass\n",
        "signals.py": "from django.dispatch import Signal\n\nchanged = Signal()\n",
        "pages/page.py": "template = \"{% zone 'feed' %}x{% endzone %}\"\n",
        "pages/helpers.py": "",
        "_components/card/component.py": "",
    },
    "scope": {"signals/__init__.py": _RECEIVER},
}

_CYCLE_PROJECT = {
    "alpha": {"services.py": "from beta.models import thing\n"},
    "beta": {"models.py": "thing = 1\n", "services.py": "\n\nimport alpha.services\n"},
}


@pytest.fixture()
def rule_scan(tmp_path):
    return scanned(tmp_path, _RULE_PROJECT)


def _messages(scan, code: str) -> list[str]:
    return [finding.message for finding in scan.findings if finding.code == code]


class TestImportRules:
    """Another app may import only the models, services and signals of an app."""

    def test_a_private_module(self, rule_scan) -> None:
        assert finding_rows(rule_scan, PRIVATE_IMPORT) == [
            ("shop/views.py", 1, PRIVATE_IMPORT)
        ]
        assert _messages(rule_scan, PRIVATE_IMPORT) == [
            (
                "shop imports crm.utils, which crm does not publish. Only its models, "
                "services and signals modules are public."
            )
        ]

    def test_a_page_tree_or_component_of_another_app(self, rule_scan) -> None:
        assert finding_rows(rule_scan, TREE_IMPORT) == [
            ("shop/views.py", 5, TREE_IMPORT),
            ("shop/views.py", 6, TREE_IMPORT),
        ]
        assert _messages(rule_scan, TREE_IMPORT)[0] == (
            "shop imports crm.pages.helpers from a page tree of crm."
        )

    def test_a_module_of_no_app_is_public(self, rule_scan) -> None:
        assert public(rule_scan.project, "django.db.models") is True
        assert public(rule_scan.project, "crm.services.orders") is True
        assert public(rule_scan.project, "crm.utils") is False


class TestEdgeRules:
    """A model loaded by name, a stray receiver and a foreign zone each warn."""

    def test_get_model_of_another_app(self, rule_scan) -> None:
        assert finding_rows(rule_scan, FOREIGN_MODEL) == [
            ("shop/services.py", 3, FOREIGN_MODEL)
        ]
        assert _messages(rule_scan, FOREIGN_MODEL) == [
            "shop loads the model crm.Client of crm with apps.get_model."
        ]

    def test_a_receiver_outside_the_signals_module(self, rule_scan) -> None:
        assert finding_rows(rule_scan, STRAY_RECEIVER) == [
            ("shop/tasks.py", 6, STRAY_RECEIVER)
        ]
        assert _messages(rule_scan, STRAY_RECEIVER) == [
            "shop receives crm.signals.changed of crm outside signals.py."
        ]

    def test_a_zone_of_another_app(self, rule_scan) -> None:
        assert finding_rows(rule_scan, FOREIGN_ZONE) == [
            ("shop/pages/page.py", 2, FOREIGN_ZONE)
        ]
        assert _messages(rule_scan, FOREIGN_ZONE) == [
            "shop refreshes the zone 'feed' of crm."
        ]


class TestKeyNames:
    """A DI key is named after the app that provides it, or after the core."""

    def test_keys_named_after_another_app(self, rule_scan) -> None:
        assert finding_rows(rule_scan, FOREIGN_KEY_NAME) == [
            ("shop/deps.py", 4, FOREIGN_KEY_NAME),
            ("shop/deps.py", 4, FOREIGN_KEY_NAME),
        ]
        assert _messages(rule_scan, FOREIGN_KEY_NAME) == [
            "shop provides the DI key 'crm.other', named after the app crm.",
            "shop provides the DI key 'crm_thing', named after the app crm.",
        ]


_WITNESS = (
    "the apps alpha, beta depend on each other in a cycle, as in alpha -> beta -> "
    "alpha (alpha -> beta by import beta.models, beta -> alpha by import "
    "alpha.services)."
)

_RING_PROJECT = {
    "alpha": {"services.py": "from beta.services import f\n"},
    "beta": {"models.py": "", "services.py": "f = 1\nfrom gamma.services import g\n"},
    "gamma": {
        "services.py": "g = 1\nfrom alpha.services import h\n",
        "views.py": "from beta.services import f\n\nimport beta.models\n",
    },
}


class TestCycles:
    """Each pair of apps inside a cycle is one error, at the first edge of the pair."""

    def test_a_cycle_between_two_apps(self, tmp_path) -> None:
        scan = scanned(tmp_path, _CYCLE_PROJECT)

        assert finding_rows(scan) == [
            ("alpha/services.py", 1, CYCLE),
            ("beta/services.py", 3, CYCLE),
        ]
        assert _messages(scan, CYCLE) == [
            f"alpha depends on beta by import beta.models, and {_WITNESS}",
            f"beta depends on alpha by import alpha.services, and {_WITNESS}",
        ]

    def test_a_new_pair_inside_a_cycle_is_a_new_finding(self, tmp_path) -> None:
        scan = scanned(tmp_path, _RING_PROJECT)

        assert finding_rows(scan) == [
            ("alpha/services.py", 1, CYCLE),
            ("beta/services.py", 2, CYCLE),
            ("gamma/services.py", 2, CYCLE),
            ("gamma/views.py", 1, CYCLE),
        ]
        assert _messages(scan, CYCLE)[3].startswith(
            "gamma depends on beta by import beta.services, and the apps alpha, beta, "
            "gamma depend on each other in a cycle, as in alpha -> beta -> gamma -> "
            "alpha ("
        )

    def test_no_cycle_without_a_way_back(self, rule_scan) -> None:
        assert finding_rows(rule_scan, CYCLE) == []


class TestRuleTable:
    """Each code has one rule, its level and the section of the doctrine."""

    def test_the_codes(self) -> None:
        assert sorted(RULES) == sorted(
            [
                *(
                    f"next.E{number}"
                    for number in (167, 168, 169, 170, 171, 173, 174, 175, 176)
                ),
                *(f"next.W{number}" for number in range(136, 154)),
            ]
        )

    def test_only_warnings_and_the_cycle_are_recorded(self) -> None:
        assert sorted(RECORDABLE) == sorted(
            [
                CYCLE,
                *(f"next.W{number}" for number in range(136, 148)),
                *(f"next.W{number}" for number in range(149, 154)),
            ]
        )
        assert GROWN not in RECORDABLE
        assert STRICT_OFF not in RECORDABLE

    def test_the_hint_points_to_the_doctrine(self) -> None:
        rule = Rule("next.W999", "warning", "Do better.", "anchor")

        assert rule.doc == f"{DOCTRINE}#anchor"
        assert rule.full_hint == f"Do better. See {DOCTRINE}#anchor."

    def test_a_project_without_apps_publishes_everything(self) -> None:
        project = Project(Path("/p"), (), (), (), {})

        assert public(project, "crm.utils") is True
        assert App("crm", "crm", Path("/p/crm")).owns_module("crm.utils")


_ISO_PROJECT = {
    "iso": {
        "apps.py": app_config("iso", isolated=True),
        "models.py": (
            "from django.db import models\n\n\n"
            "class Record(models.Model):\n"
            '    client = models.ForeignKey("crm.Client", on_delete=models.CASCADE)\n'
        ),
        "services.py": "def fetch() -> int:\n    pass\n",
        "signals.py": "from django.dispatch import Signal\n\nchanged = Signal()\n",
        "utils.py": "helper = 1\n",
    },
    "shop": {
        "views.py": (
            "from iso.models import Record\n"
            "from iso.services import fetch\n"
            "from iso.signals import changed\n"
            "from iso.utils import helper\n"
        ),
        "models.py": (
            "from django.db import models\n\n\n"
            "class Order(models.Model):\n"
            '    record = models.ForeignKey("iso.Record", on_delete=models.CASCADE)\n'
            '    main = models.OneToOneField("iso.Record", on_delete=models.CASCADE, '
            'related_name="+")\n'
            '    records = models.ManyToManyField("iso.Record", related_name="+")\n'
        ),
    },
    "crm": {
        "models.py": (
            "from django.db import models\n\n\nclass Client(models.Model):\n    pass\n"
        )
    },
}

_ISO_REASON = "iso is isolated, it publishes only its services and signals modules."


@pytest.fixture()
def iso_scan(tmp_path):
    return scanned(tmp_path, _ISO_PROJECT)


class TestIsolated:
    """An isolated app publishes only services and signals and takes no foreign key."""

    def test_its_models_are_private(self, iso_scan) -> None:
        assert finding_rows(iso_scan, PRIVATE_IMPORT) == [
            ("shop/views.py", 1, PRIVATE_IMPORT),
            ("shop/views.py", 4, PRIVATE_IMPORT),
        ]
        assert _messages(iso_scan, PRIVATE_IMPORT) == [
            f"shop imports iso.models, which iso does not publish. {_ISO_REASON}",
            f"shop imports iso.utils, which iso does not publish. {_ISO_REASON}",
        ]

    def test_public_takes_the_isolated_apps(self, iso_scan) -> None:
        isolated = frozenset({"iso"})

        assert public(iso_scan.project, "iso.models") is True
        assert public(iso_scan.project, "iso.models", isolated) is False
        assert public(iso_scan.project, "iso.services", isolated) is True
        assert public(iso_scan.project, "iso.signals.changed", isolated) is True

    def test_every_relation_into_it(self, iso_scan) -> None:
        assert finding_rows(iso_scan, ISOLATED_FOREIGN_KEY) == [
            ("shop/models.py", 5, ISOLATED_FOREIGN_KEY),
            ("shop/models.py", 6, ISOLATED_FOREIGN_KEY),
            ("shop/models.py", 7, ISOLATED_FOREIGN_KEY),
        ]
        assert _messages(iso_scan, ISOLATED_FOREIGN_KEY) == [
            (
                "shop relates Order.record -> iso.Record to a model of the isolated app "
                "iso."
            ),
            "shop relates Order.main -> iso.Record to a model of the isolated app iso.",
            (
                "shop relates Order.records -> iso.Record to a model of the isolated app "
                "iso."
            ),
        ]

    def test_its_own_relations_out_are_free(self, iso_scan) -> None:
        assert [
            finding.app
            for finding in iso_scan.findings
            if finding.code == ISOLATED_FOREIGN_KEY
        ] == ["shop", "shop", "shop"]
        assert ("fk", "iso", "crm") in {
            (edge.kind, edge.source, edge.target) for edge in iso_scan.graph.edges
        }

    def test_an_app_that_is_not_isolated(self, tmp_path) -> None:
        open_iso = {**_ISO_PROJECT, "iso": {**_ISO_PROJECT["iso"]}}
        open_iso["iso"]["apps.py"] = app_config("iso")
        open_iso["iso"]["services.py"] = "def fetch():\n    pass\n"

        scan = scanned(tmp_path, open_iso)

        assert finding_rows(scan) == [("shop/views.py", 4, PRIVATE_IMPORT)]


_COMPONENT_PROJECT = {
    "shop": {"pages/template.djx": "{% component 'badge' %}"},
    "iso": {
        "apps.py": app_config("iso", isolated=True),
        "pages/page.py": "",
        "pages/_components/badge/component.djx": "badge",
    },
}


class TestIsolatedComponent:
    """A component of an isolated app is no finding, only an undeclared edge."""

    def test_without_depends_on(self, tmp_path) -> None:
        scan = scanned(tmp_path, _COMPONENT_PROJECT)

        assert [edge.kind for edge in scan.graph.edges] == ["component"]
        assert scan.findings == ()

    def test_with_depends_on(self, tmp_path) -> None:
        shop = {
            **_COMPONENT_PROJECT["shop"],
            "apps.py": app_config("shop", depends_on=()),
        }

        scan = scanned(tmp_path, {**_COMPONENT_PROJECT, "shop": shop})

        assert finding_rows(scan) == [("shop/pages/template.djx", 1, UNDECLARED)]
