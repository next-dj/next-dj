from __future__ import annotations

import importlib
from contextlib import contextmanager
from typing import TYPE_CHECKING

from django.apps import apps as django_apps

from next.boundaries.manager import boundaries_manager
from next.deps import resolver
from tests.support.mounts import installed_apps, write_app


if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Mapping
    from pathlib import Path

    from next.boundaries.manager import Scan


type AppFiles = Mapping[str, str | bytes]
type EdgeRow = tuple[str, str, str, str, int, str]
type FindingRow = tuple[str, int, str]


def write_boundary_apps(root: Path, apps: Mapping[str, AppFiles]) -> None:
    """Write each app under `root` with no page tree and the files it maps to text."""
    for label, files in apps.items():
        app = write_app(root, label, {})
        for name, text in files.items():
            path = app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(text, bytes):
                path.write_bytes(text)
            else:
                path.write_text(text)


@contextmanager
def boundary_project(
    root: Path,
    apps: Mapping[str, AppFiles],
    *,
    framework: Mapping[str, object] | None = None,
    urlconf: str | None = None,
    load: Iterable[str] = (),
    **overrides: object,
) -> Generator[Path, None, None]:
    """Install the apps written under `root` with `BASE_DIR` at `root` for the block.

    The modules of `load` run first, so their DI keys are held until the block ends.
    """
    write_boundary_apps(root, apps)
    settings = {"BASE_DIR": root, **overrides}
    keys = set(resolver.dependencies())
    try:
        with installed_apps(
            root, *apps, urlconf=urlconf, framework=framework, **settings
        ):
            for module in load:
                importlib.import_module(module)
            yield root
    finally:
        for key in set(resolver.dependencies()) - keys:
            resolver.unregister_dependency(key)
        for name in apps:
            django_apps.all_models.pop(name.rpartition(".")[2], None)
        django_apps.clear_cache()


def scanned(root: Path, apps: Mapping[str, AppFiles], **overrides: object) -> Scan:
    """Return the boundary scan of the apps written and installed under `root`."""
    with boundary_project(root, apps, **overrides):
        return boundaries_manager.scan()


def edge_rows(scan: Scan) -> list[EdgeRow]:
    """Return each edge of a scan with its file relative to `BASE_DIR`."""
    assert scan.project is not None
    return [
        (
            edge.kind,
            edge.source,
            edge.target,
            scan.project.relative(edge.file),
            edge.line,
            edge.detail,
        )
        for edge in scan.graph.edges
    ]


def finding_rows(scan: Scan, *codes: str) -> list[FindingRow]:
    """Return the file, line and code of each finding, only of `codes` when named."""
    return [
        (finding.file, finding.line, finding.code)
        for finding in scan.findings
        if not codes or finding.code in codes
    ]


_CANVAS_PAGE = """from next.forms import action
from next.pages import page

page.public()
template = "home"


@action("cancel", login_required=True)
def cancel():
    return None
"""

_CANVAS_ORDERS = """from next.forms import action

template = "orders"


@action("place")
def place():
    return None
"""

CANVAS_APPS: dict[str, dict[str, str]] = {
    "shop": {
        "models.py": (
            "from django.db import models\n\n\n"
            "class Order(models.Model):\n"
            '    buyer = models.ForeignKey("crm.Client", on_delete=models.CASCADE)\n\n\n'
            "class Cart(models.Model):\n    pass\n"
        ),
        "signals.py": (
            "from django.dispatch import Signal, receiver\n\n"
            "from crm.signals import changed\n\n\n"
            "placed = Signal()\n\n\n"
            "@receiver(changed)\n"
            "def on_changed(sender, **kwargs):\n    pass\n"
        ),
        "services.py": (
            "def place_order():\n    pass\n\n\n"
            "def _helper():\n    pass\n\n\n"
            "async def refund():\n    pass\n"
        ),
        "deps.py": (
            "from next.deps import resolver\n\n\n"
            '@resolver.dependency("shop_cart")\n'
            "def cart():\n    return None\n"
        ),
        "pages/page.py": _CANVAS_PAGE,
        "pages/orders/page.py": _CANVAS_ORDERS,
    },
    "crm": {
        "models.py": (
            "from django.db import models\n\n\nclass Client(models.Model):\n    pass\n"
        ),
        "signals.py": "from django.dispatch import Signal\n\nchanged = Signal()\n",
        "services/billing.py": "def bill():\n    pass\n",
        "services/broken.py": b"name = '\xff'\n",
        "views.py": "from shop.services import place_order\n",
    },
}
