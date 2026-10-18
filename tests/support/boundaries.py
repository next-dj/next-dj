from __future__ import annotations

import importlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from django.apps import apps as django_apps
from django.core.checks.registry import CheckRegistry, registry

from next.boundaries.checks import boundary_checks
from next.boundaries.manager import boundaries_manager
from next.deps import resolver
from tests.support.mounts import installed_apps, write_app


if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Mapping
    from pathlib import Path

    from django.core.checks import CheckMessage

    from next.boundaries.manager import Scan


@dataclass(frozen=True, slots=True)
class Unreadable:
    """A file the scan lists but cannot read, written as a link that leads nowhere."""


UNREADABLE: Final = Unreadable()

type AppFiles = Mapping[str, str | bytes | Unreadable]
type EdgeRow = tuple[str, str, str, str, int, str]
type FindingRow = tuple[str, int, str]


def literal(value: object) -> str:
    """Return the source of a value as `ruff format` spells it."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, tuple):
        items = [literal(item) for item in value]
        return f"({items[0]},)" if len(items) == 1 else f"({', '.join(items)})"
    if isinstance(value, list):
        return f"[{', '.join(literal(item) for item in value)}]"
    return repr(value)


def app_config(name: str, *lines: str, **attributes: object) -> str:
    """Return an `apps.py` whose class `Config` sets `name`, `attributes`, `lines`."""
    body = "".join(
        f"    {key} = {literal(value)}\n" for key, value in attributes.items()
    )
    extra = "".join(f"    {line}\n" for line in lines)
    return (
        "from django.apps import AppConfig\n\n\n"
        f"class Config(AppConfig):\n    name = {literal(name)}\n{body}{extra}"
    )


def write_boundary_apps(root: Path, apps: Mapping[str, AppFiles]) -> None:
    """Write each app under `root` with no page tree and the files it maps to text."""
    for label, files in apps.items():
        app = write_app(root, label, {})
        for name, text in files.items():
            path = app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(text, Unreadable):
                path.symlink_to(path.with_name(f"{path.name}.missing"))
            elif isinstance(text, bytes):
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

    The boundaries are on in the warn mode unless `framework` says otherwise.
    """
    write_boundary_apps(root, apps)
    settings = {"BASE_DIR": root, **overrides}
    framework = {"BOUNDARIES": "warn", **(framework or {})}
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
        "services/broken.py": UNREADABLE,
        "views.py": "from shop.services import place_order\n",
    },
}


type Check = Callable[..., list[CheckMessage]]


@dataclass(slots=True)
class Probes:
    """Checks that record their name when a run calls them, and answer `messages`."""

    calls: list[str] = field(default_factory=list)

    def check(
        self, name: str, *tags: str, messages: Iterable[CheckMessage] = ()
    ) -> Check:
        """Return a check tagged `tags` that records `name` on each call."""
        answer = list(messages)

        def probe(*args: object, **kwargs: object) -> list[CheckMessage]:
            self.calls.append(name)
            return answer

        CheckRegistry().register(probe, *tags)
        return probe


@contextmanager
def held_checks(
    regular: Iterable[Check] = (),
    *,
    deployment: Iterable[Check] = (),
    area: Iterable[Check] | None = None,
) -> Generator[None, None, None]:
    """Hold the global check registry to the checks given, and the boundary one too.

    Django reads one registry object everywhere, so its sets swap, `area=None` stays.
    """
    saved = registry.registered_checks, registry.deployment_checks
    saved_area = boundary_checks.registered_checks
    registry.registered_checks = set(regular)
    registry.deployment_checks = set(deployment)
    if area is not None:
        boundary_checks.registered_checks = set(area)
    try:
        yield
    finally:
        registry.registered_checks, registry.deployment_checks = saved
        boundary_checks.registered_checks = saved_area
