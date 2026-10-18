import ast

import pytest

from next.boundaries import edges
from next.boundaries.edges import class_line
from tests.support import file_router_config_entry, write_page
from tests.support.boundaries import edge_rows, scanned


_SHOP_MODELS = """from django.conf import settings
from django.db import models

from crm.models import Tag


class Order(models.Model):
    buyer = models.ForeignKey("crm.Client", on_delete=models.CASCADE)
    tags = models.ManyToManyField(Tag)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    parent = models.ForeignKey("shop.Order", null=True, on_delete=models.CASCADE)
    title: str = models.CharField(max_length=10)
"""

_SHOP_SERVICES = """from django.apps import apps

from crm.services import notify


label = "crm"
client = apps.get_model("crm", "Client")
tag = apps.get_model("crm.Tag")
mine = apps.get_model("shop", "Order")
dynamic = apps.get_model(label, "Client")
bare = apps.get_model("crm")
"""

_SHOP_SIGNALS = """from django.db.models.signals import post_save
from django.dispatch import receiver

from crm import signals as crm_signals
from crm.models import Client


@receiver(crm_signals.changed)
def on_changed(sender, **kwargs):
    pass


@receiver([post_save, crm_signals.changed], sender=Client)
def on_client(sender, **kwargs):
    pass


@receiver(post_save, sender="crm.Tag")
async def on_tag(sender, **kwargs):
    pass
"""

_SHOP_VIEWS = """import functools

from django.db.models.signals import post_save
from django.dispatch import receiver

import crm.utils.helper
from crm.pages import helpers
from crm.utils import helper
from next.deps import Depends
from shop.models import Order


@receiver(post_save, sender=Order)
@receiver(post_save, dispatch_uid="own")
@receiver()
@receiver(post_save, sender=make())
@functools.cache
@register(post_save)
def on_own(sender, **kwargs):
    pass


def render(
    request,
    profile=Depends("crm_profile"),
    cart=Depends("shop_cart"),
    unknown=Depends("missing"),
    dynamic=Depends(name),
    empty=Depends(),
):
    Patches(request).refresh(zone="shared")
    Patches(request).refresh(zone="nobody")
    Patches(request).refresh(zone=name)
    Patches(request).refresh("feed")
    Patches(request).refresh(target="feed")
    refresh(zone="feed")
"""

_SHOP_DEPS = """from next.deps import resolver


@resolver.dependency("shop_cart")
def cart():
    return None
"""

_SHOP_PAGE = """from next.deps import Depends


def render(request, client=Depends("crm_client")):
    Patches(request).refresh(zone="feed")


template = "{% zone 'shared' %}x{% endzone %}"
"""

_CRM_DEPS = """import functools

from next.deps import resolver


def client():
    return None


class Profile:
    def __call__(self):
        return None


resolver.register_dependency("crm_client", functools.wraps(client)(lambda: None))
resolver.register_dependency("crm_profile", Profile())
resolver.register_dependency("crm_partial", functools.partial(client))
"""

_CRM_PAGE = """template = (
    "{% zone 'feed' %}a{% endzone %}"
    "{% zone 'shared' %}b{% endzone %}"
)
"""

_EDGE_PROJECT = {
    "shop": {
        "models.py": _SHOP_MODELS,
        "services.py": _SHOP_SERVICES,
        "signals.py": _SHOP_SIGNALS,
        "deps.py": _SHOP_DEPS,
        "views.py": _SHOP_VIEWS,
        "pages/page.py": _SHOP_PAGE,
        "pages/helpers.py": "from ..... import far\nfrom crm import services\n",
        "tests/test_orders.py": "from crm.utils import helper\n",
        "migrations/__init__.py": "",
        "migrations/0001_initial.py": "from crm.utils import helper\n",
    },
    "crm": {
        "models.py": (
            "from django.db import models\n\n\n"
            "class Client(models.Model):\n    pass\n\n\n"
            "class Tag(models.Model):\n    pass\n"
        ),
        "utils.py": "helper = 1\n",
        "services.py": "def notify():\n    pass\n",
        "signals.py": "from django.dispatch import Signal\n\nchanged = Signal()\n",
        "deps.py": _CRM_DEPS,
        "pages/page.py": _CRM_PAGE,
        "pages/helpers.py": "thing = 1\n",
    },
}


_OUTSIDE_PROJECT = {
    "proj.shop": {
        "services.py": "import vendor.utils\nfrom proj.crm import utils\nimport django.db\nimport next.deps\n"
    },
    "proj.crm": {"utils.py": "helper = 1\n"},
    "vendor": {"utils.py": "helper = 1\n"},
}


@pytest.fixture()
def edge_scan(tmp_path):
    return scanned(tmp_path, _EDGE_PROJECT, load=("shop.deps", "crm.deps"))


class TestCollectEdges:
    """Every kind of edge between two project apps, once each, in file order."""

    def test_every_edge_kind(self, edge_scan) -> None:
        assert edge_rows(edge_scan) == [
            ("import", "shop", "crm", "shop/models.py", 4, "crm.models"),
            ("fk", "shop", "crm", "shop/models.py", 8, "Order.buyer -> crm.Client"),
            ("fk", "shop", "crm", "shop/models.py", 9, "Order.tags -> crm.Tag"),
            ("import", "shop", "crm", "shop/pages/helpers.py", 2, "crm.services"),
            ("di", "shop", "crm", "shop/pages/page.py", 4, "crm_client"),
            ("zone", "shop", "crm", "shop/pages/page.py", 5, "feed"),
            ("import", "shop", "crm", "shop/services.py", 3, "crm.services"),
            ("get_model", "shop", "crm", "shop/services.py", 7, "crm.Client"),
            ("get_model", "shop", "crm", "shop/services.py", 8, "crm.Tag"),
            ("import", "shop", "crm", "shop/signals.py", 4, "crm.signals"),
            ("import", "shop", "crm", "shop/signals.py", 5, "crm.models"),
            ("signal", "shop", "crm", "shop/signals.py", 8, "crm.signals.changed"),
            ("signal", "shop", "crm", "shop/signals.py", 13, "crm.signals.changed"),
            (
                "signal",
                "shop",
                "crm",
                "shop/signals.py",
                13,
                "sender=crm.models.Client",
            ),
            ("signal", "shop", "crm", "shop/signals.py", 18, "sender=crm.Tag"),
            ("import", "shop", "crm", "shop/views.py", 6, "crm.utils"),
            ("import", "shop", "crm", "shop/views.py", 7, "crm.pages.helpers"),
            ("import", "shop", "crm", "shop/views.py", 8, "crm.utils"),
            ("di", "shop", "crm", "shop/views.py", 25, "crm_profile"),
        ]

    def test_tests_and_migrations_add_no_edge(self, edge_scan) -> None:
        files = {edge.file.name for edge in edge_scan.graph.edges}

        assert "test_orders.py" not in files
        assert "0001_initial.py" not in files

    def test_only_apps_under_the_base_dir_are_targets(self, tmp_path) -> None:
        scan = scanned(tmp_path, _OUTSIDE_PROJECT, BASE_DIR=tmp_path / "proj")

        assert [app.label for app in scan.graph.apps] == ["crm", "shop"]
        assert edge_rows(scan) == [
            ("import", "shop", "crm", "shop/services.py", 2, "proj.crm.utils")
        ]

    def test_a_zone_of_a_page_outside_every_app_is_no_edge(self, tmp_path) -> None:
        site = tmp_path / "site"
        write_page(site, "", "template = \"{% zone 'feed' %}x{% endzone %}\"\n")
        entry = file_router_config_entry(app_dirs=True, dirs=[site])
        refreshing = {
            "shop": {
                "views.py": "def go(request):\n    Patches(request).refresh(zone='feed')\n"
            }
        }

        scan = scanned(tmp_path, refreshing, framework={"PAGE_BACKENDS": [entry]})

        assert scan.graph.edges == ()


class TestRegistrations:
    """A DI key belongs to the app whose file holds the code of its callable."""

    def test_the_provider_app_of_each_key(self, edge_scan) -> None:
        assert edge_scan.project is not None
        rows = [
            (item.key, item.app.label, edge_scan.project.relative(item.file), item.line)
            for item in edge_scan.registrations
        ]

        assert rows == [
            ("crm_client", "crm", "crm/deps.py", 6),
            ("crm_profile", "crm", "crm", 0),
            ("shop_cart", "shop", "shop/deps.py", 4),
        ]


class TestClassLine:
    """The line of a field is where the class body assigns it."""

    _SYNTAX = ast.parse(
        "class Order:\n    pass\n    title: str = ''\n    buyer = None\n\n\n"
        "class Shadow:\n    class Order:\n        buyer = 1\n"
    )

    @pytest.mark.parametrize(
        ("name", "attribute", "line"),
        [
            ("Order", "buyer", 4),
            ("Order", "title", 3),
            ("Order", "missing", 1),
            ("Order", "", 1),
            ("Missing", "buyer", 0),
        ],
    )
    def test_the_line_of(self, name: str, attribute: str, line: int) -> None:
        assert class_line(self._SYNTAX, name, attribute) == line

    def test_no_syntax_is_line_zero(self) -> None:
        assert class_line(None, "Order", "buyer") == 0


_REACH = """from django.apps import apps

from crm.lib.util import tool
from next.deps import Depends
from next.permissions import add_perm, has_perm


def bare(
    crm_client=Depends(),
    *,
    crm_profile=Depends(),
    own=Depends(dependency="shop_cart"),
    named=Depends(dependency="crm_client"),
    dynamic=Depends(dependency=name),
    plain=None,
    called=make(),
    odd=Depends(cache=True),
):
    pass


def positional(crm_client=Depends(), /, other=1):
    pass


kw = apps.get_model(app_label="crm", model_name="Client")
mixed = apps.get_model("crm", model_name="Tag")
unnamed = apps.get_model("crm", name)
nolabel = apps.get_model(model_name="Client")

has_perm(user, "crm.view_client")
page.permission_required(["crm.change_client", "shop.own", "nodot", "vendor.x", name])
add_perm(name="crm.edit", predicate=None)


@action("close", permission_required="crm.close_client")
@action("open", permission_required=["crm.open_client"], login_required=True)
def close():
    pass


class OrderForm:
    class Meta:
        permission_required = ("crm.delete_client",)
        other = "crm.not_a_perm"
        size: int = 1
"""

_REACH_TEMPLATE = """{% #component "badge" %}{% /component %}
{% has_perm "crm.view_client" %}{% endhas_perm %}
{% component 'own_card' %}{% component "ghost" %}
{% has_perm "shop.own" %}{% has_perm "nodot" %}{% has_perm "vendor.x" %}
"""

_REACH_PROJECT = {
    "shop": {
        "reach.py": _REACH,
        "deps.py": _SHOP_DEPS,
        "pages/template.djx": _REACH_TEMPLATE,
        "pages/bad.djx": b"\xff\xfe{% component 'badge' %}",
        "pages/inline/page.py": (
            "title = 'Inline'\nlabel: str = 'x'\nnamed = template = 1\n"
            "template = (\n    \"{% has_perm 'crm.view_client' %}\"\n"
            "    \"{% component 'badge' %}\"\n)\n"
        ),
        "pages/_components/own_card/component.djx": "own",
    },
    "crm": {
        "lib/util.py": "tool = 1\n",
        "deps.py": _CRM_DEPS,
        "pages/page.py": "",
        "pages/_components/badge/component.djx": "badge",
    },
}


@pytest.fixture()
def reach_scan(tmp_path):
    return scanned(tmp_path, _REACH_PROJECT, load=("shop.deps", "crm.deps"))


class TestReachingEdges:
    """Bare `Depends()`, keyword `get_model`, permissions and template tags are edges."""

    def test_the_edges(self, reach_scan) -> None:
        assert edge_rows(reach_scan) == [
            ("component", "shop", "crm", "shop/pages/inline/page.py", 5, "badge"),
            ("perm", "shop", "crm", "shop/pages/inline/page.py", 5, "crm.view_client"),
            ("component", "shop", "crm", "shop/pages/template.djx", 1, "badge"),
            ("perm", "shop", "crm", "shop/pages/template.djx", 2, "crm.view_client"),
            ("import", "shop", "crm", "shop/reach.py", 3, "crm.lib.util"),
            ("di", "shop", "crm", "shop/reach.py", 9, "crm_client"),
            ("di", "shop", "crm", "shop/reach.py", 11, "crm_profile"),
            ("di", "shop", "crm", "shop/reach.py", 13, "crm_client"),
            ("di", "shop", "crm", "shop/reach.py", 22, "crm_client"),
            ("get_model", "shop", "crm", "shop/reach.py", 26, "crm.Client"),
            ("get_model", "shop", "crm", "shop/reach.py", 27, "crm.Tag"),
            ("get_model", "shop", "crm", "shop/reach.py", 28, "crm"),
            ("perm", "shop", "crm", "shop/reach.py", 31, "crm.view_client"),
            ("perm", "shop", "crm", "shop/reach.py", 32, "crm.change_client"),
            ("perm", "shop", "crm", "shop/reach.py", 33, "crm.edit"),
            ("perm", "shop", "crm", "shop/reach.py", 36, "crm.close_client"),
            ("perm", "shop", "crm", "shop/reach.py", 37, "crm.open_client"),
            ("perm", "shop", "crm", "shop/reach.py", 44, "crm.delete_client"),
        ]

    def test_components_are_looked_up_only_when_a_template_names_one(
        self, tmp_path, monkeypatch
    ) -> None:
        def refuse() -> None:
            raise AssertionError

        monkeypatch.setattr(edges, "get_components_manager", refuse)
        quiet = {"shop": {"pages/template.djx": "{% has_perm 'crm.x' %}"}, "crm": {}}

        scan = scanned(tmp_path, quiet)

        assert edge_rows(scan) == [
            ("perm", "shop", "crm", "shop/pages/template.djx", 1, "crm.x")
        ]

    def test_a_component_outside_every_app_has_no_owner(self, tmp_path) -> None:
        shared = tmp_path / "shared"
        (shared / "card").mkdir(parents=True)
        (shared / "card" / "component.djx").write_text("card")
        backend = {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [str(shared)],
            "COMPONENTS_DIR": "_components",
        }
        using = {"shop": {"pages/template.djx": "{% component 'card' %}"}}

        scan = scanned(tmp_path, using, framework={"COMPONENT_BACKENDS": [backend]})

        assert scan.graph.edges == ()
