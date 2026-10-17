from pathlib import Path

import pytest

from next.boundaries.graph import App
from next.boundaries.rules import (
    CYCLE,
    DISABLED,
    DOCTRINE,
    FOREIGN_KEY_NAME,
    FOREIGN_MODEL,
    FOREIGN_ZONE,
    GROWN,
    PRIVATE_IMPORT,
    RECORDABLE,
    RULES,
    STRAY_RECEIVER,
    STRICT_OFF,
    TREE_IMPORT,
    Rule,
    public,
)
from next.boundaries.sources import Project
from tests.support.boundaries import finding_rows, scanned


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
                *(f"next.E{number}" for number in (167, 168, 169, 170, 171, 173)),
                *(f"next.W{number}" for number in range(136, 150)),
                DISABLED,
            ]
        )

    def test_only_warnings_and_the_cycle_are_recorded(self) -> None:
        assert sorted(RECORDABLE) == sorted(
            [CYCLE, *(f"next.W{number}" for number in range(136, 148)), "next.W149"]
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
