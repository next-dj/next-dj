import pytest

from next.boundaries.lint import FOREIGN_FILES, project_signals
from next.boundaries.rules import (
    AGGREGATOR,
    FOREIGN_FILE,
    FOREIGN_PERMISSION,
    PACKAGE_MODEL,
    STRAY_SEND,
    TREE_WRITE,
    UNSCOPED_QUERY,
)
from tests.support.boundaries import finding_rows, scanned


_TREE_PAGE = """from shop.models import Order

rows = Order.objects.all()
mine = Order.objects.visible_to(user).filter(open=True)
Order().save()


def save(request, form):
    order = Order()
    order.total = 1
    order.count += 1
    order.note: str = ""
    order.save()
    form.save()
    self.form.save()
    super().save()
    other.flag = True
    get_order().save()
    items[0].save()

    def inner():
        order.save()

    return inner


class Handler:
    def run(self):
        self.order.save()
"""

_PLAIN = """from shop.models import Order

rows = Order.objects.all()


def save():
    order = Order()
    order.total = 1
    order.save()
"""

_CRM_SIGNALS = """from django.dispatch import Signal


def make():
    return None


changed = Signal()
typed: Signal = Signal()
declared: Signal
other = make()
number = 1
first = second = Signal()


class Holder:
    pass


Holder.attr = Signal()


def build():
    inner = Signal()
    return inner
"""

_SENDS = """from django.db.models.signals import post_save

from crm import signals
from crm.signals import changed


def go():
    changed.send(sender=None)
    signals.typed.send_robust(sender=None)
    post_save.send(sender=None)
    changed.connect(go)
    send(changed)
"""

_SHOP_INIT = """class Model:
    pass


class Holder:
    Model = Model


class Thing(Model):
    pass


class Other(Holder.Model):
    pass


class Plain(object):
    pass
"""

_API = """from crm.models import *
from crm.services import place
from shop.models import *

local = 1
__all__ = ["place", "local", 3]
__all__ += ["place"]
__all__: list = ["place"]
__all__ = tuple(__all__)
others = ["place"]
"""

_LINT_PROJECT = {
    "shop": {
        "__init__.py": _SHOP_INIT,
        "models.py": "",
        "services.py": _PLAIN
        + "\n\n"
        + "from crm.signals import changed\n\nchanged.send(sender=None)\n",
        "views.py": _SENDS,
        "api.py": _API,
        "orders/__init__.py": "class Model:\n    pass\n\n\nclass Order(Model):\n    pass\n",
        "orders/providers.py": "",
        "pages/page.py": _TREE_PAGE,
        "pages/broken.py": b"name = '\xff'\n",
        "pages/beyond.py": "from ...... import *\n",
        "signals/broken.py": b"name = '\xff'\n",
        **dict.fromkeys(FOREIGN_FILES, ""),
    },
    "crm": {
        "models.py": "",
        "signals.py": _CRM_SIGNALS,
        "services/__init__.py": "from crm.signals import changed\n\nchanged.send(sender=None)\n",
    },
}


@pytest.fixture()
def lint_scan(tmp_path):
    return scanned(tmp_path, _LINT_PROJECT)


class TestTreeQueries:
    """A page tree narrows every queryset with `.visible_to(...)`."""

    def test_unscoped_queries_of_a_tree_file_only(self, lint_scan) -> None:
        assert finding_rows(lint_scan, UNSCOPED_QUERY) == [
            ("shop/pages/page.py", 3, UNSCOPED_QUERY)
        ]
        assert [f.message for f in lint_scan.findings if f.code == UNSCOPED_QUERY] == [
            "A page tree reads Order.objects without .visible_to(...)."
        ]


class TestTreeWrites:
    """A page tree saves through a form, never by `.save()` of its own."""

    def test_the_writes_of_a_tree_file_only(self, lint_scan) -> None:
        assert finding_rows(lint_scan, TREE_WRITE) == [
            ("shop/pages/page.py", 5, TREE_WRITE),
            ("shop/pages/page.py", 10, TREE_WRITE),
            ("shop/pages/page.py", 11, TREE_WRITE),
            ("shop/pages/page.py", 12, TREE_WRITE),
            ("shop/pages/page.py", 13, TREE_WRITE),
            ("shop/pages/page.py", 18, TREE_WRITE),
            ("shop/pages/page.py", 19, TREE_WRITE),
            ("shop/pages/page.py", 22, TREE_WRITE),
            ("shop/pages/page.py", 29, TREE_WRITE),
        ]

    def test_the_messages(self, lint_scan) -> None:
        messages = [f.message for f in lint_scan.findings if f.code == TREE_WRITE]

        assert messages[:3] == [
            "A page tree calls Order().save() past a form.",
            "A page tree sets order.total on an object it saves in the same function.",
            "A page tree sets order.count on an object it saves in the same function.",
        ]


_FORM_METHOD = """from shop.base import Base


class OrderForm(Base):
    def on_valid(self):
        self.instance.total = 1
        self.save()
        super().save()
"""

_FORM_BASE = """from django.forms import ModelForm


class Checkout(ModelForm):
    def done(self):
        self.total = 1
        self.save()
"""

_PLAIN_METHOD = """class Handler:
    def run(self):
        self.total = 1
        self.save()
"""


class TestFormNames:
    """A save past a form is told by the words of the name, never by its letters."""

    @pytest.mark.parametrize(
        ("subject", "found"),
        [
            ("form", False),
            ("Form", False),
            ("order_form", False),
            ("form_set", False),
            ("OrderForm", False),
            ("platform", True),
            ("transform", True),
            ("formula", True),
            ("information", True),
        ],
    )
    def test_the_name_of_the_saved_object(self, tmp_path, subject, found) -> None:
        page = f"def save({subject}):\n    {subject}.save()\n"
        scan = scanned(tmp_path, {"shop": {"models.py": "", "pages/page.py": page}})

        expected = [("shop/pages/page.py", 2, TREE_WRITE)] if found else []
        assert finding_rows(scan, TREE_WRITE) == expected


class TestFormMethodWrites:
    """A method of a form saves the form itself, so `self.save()` passes there."""

    @pytest.mark.parametrize(
        ("page", "rows"),
        [
            (_FORM_METHOD, []),
            (_FORM_BASE, []),
            (
                "class Handler(make()):\n    def run(self):\n        self.save()\n",
                [("shop/pages/page.py", 3, TREE_WRITE)],
            ),
            (
                _PLAIN_METHOD,
                [
                    ("shop/pages/page.py", 3, TREE_WRITE),
                    ("shop/pages/page.py", 4, TREE_WRITE),
                ],
            ),
        ],
        ids=["form-name", "form-base", "call-base", "plain-class"],
    )
    def test_self_save(self, tmp_path, page, rows) -> None:
        scan = scanned(tmp_path, {"shop": {"models.py": "", "pages/page.py": page}})

        assert finding_rows(scan, TREE_WRITE) == rows


_PERMISSIONS = """from next.permissions import add_perm

add_perm("crm.edit", None)
add_perm(name="crm.view", predicate=None)
add_perm("shop.own", None)
add_perm("vendor.x", None)
add_perm("nodot", None)
add_perm(name, None)
add_perm(predicate=None)
has_perm(user, "crm.edit")
"""


class TestForeignPermissions:
    """Only the app a permission label names registers that permission."""

    def test_add_perm_of_another_app(self, tmp_path) -> None:
        apps = {"shop": {"grants.py": _PERMISSIONS}, "crm": {}}
        scan = scanned(tmp_path, apps)

        assert finding_rows(scan, FOREIGN_PERMISSION) == [
            ("shop/grants.py", 3, FOREIGN_PERMISSION),
            ("shop/grants.py", 4, FOREIGN_PERMISSION),
        ]
        assert [f.message for f in scan.findings if f.code == FOREIGN_PERMISSION] == [
            "shop registers the permission 'crm.edit' of the app crm with add_perm.",
            "shop registers the permission 'crm.view' of the app crm with add_perm.",
        ]


class TestSignals:
    """Only the services module sends a signal a `signals.py` of the project binds."""

    def test_the_project_signals(self, lint_scan) -> None:
        assert sorted(project_signals(lint_scan.project)) == [
            "crm.signals.changed",
            "crm.signals.first",
            "crm.signals.second",
            "crm.signals.typed",
        ]

    def test_a_send_outside_services(self, lint_scan) -> None:
        assert finding_rows(lint_scan, STRAY_SEND) == [
            ("shop/views.py", 8, STRAY_SEND),
            ("shop/views.py", 9, STRAY_SEND),
        ]
        assert next(f.message for f in lint_scan.findings if f.code == STRAY_SEND) == (
            "shop sends the project signal crm.signals.changed outside services.py."
        )


class TestPackageModels:
    """A model class in the root `__init__.py` of an app belongs in models.py."""

    def test_models_of_the_root_package_only(self, lint_scan) -> None:
        assert finding_rows(lint_scan, PACKAGE_MODEL) == [
            ("shop/__init__.py", 9, PACKAGE_MODEL),
            ("shop/__init__.py", 13, PACKAGE_MODEL),
        ]
        assert [f.message for f in lint_scan.findings if f.code == PACKAGE_MODEL] == [
            "shop defines the model Thing in __init__.py.",
            "shop defines the model Other in __init__.py.",
        ]


class TestAggregators:
    """A module re-exports no name of another app, by `*` or by `__all__`."""

    def test_star_imports_and_listed_names(self, lint_scan) -> None:
        assert finding_rows(lint_scan, AGGREGATOR) == [
            ("shop/api.py", 1, AGGREGATOR),
            ("shop/api.py", 6, AGGREGATOR),
            ("shop/api.py", 7, AGGREGATOR),
            ("shop/api.py", 8, AGGREGATOR),
        ]
        assert [f.message for f in lint_scan.findings if f.code == AGGREGATOR][:2] == [
            "shop re-exports everything of crm.models with import *.",
            "shop lists place of crm in __all__.",
        ]


class TestForeignFiles:
    """A root module named for a role next.dj reads elsewhere names the right one."""

    def test_every_foreign_name_at_the_root_only(self, lint_scan) -> None:
        assert finding_rows(lint_scan, FOREIGN_FILE) == [
            (f"shop/{name}", 0, FOREIGN_FILE) for name in sorted(FOREIGN_FILES)
        ]

    def test_the_message_names_the_module_to_use(self, lint_scan) -> None:
        message = next(
            f.message
            for f in lint_scan.findings
            if f.file == "shop/providers.py" and f.code == FOREIGN_FILE
        )

        assert message == (
            "shop has providers.py at its root, a module next.dj does not read. Its "
            "code belongs in deps.py."
        )
