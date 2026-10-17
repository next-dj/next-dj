from __future__ import annotations

from typing import TYPE_CHECKING, Self

from next.components.info import ComponentInfo


if TYPE_CHECKING:
    from pathlib import Path

    from django.contrib.auth.models import Group


_DEFAULT_LEAF_BODY = "def render(request):\n    return 'x'\n"


def build_pages_tree(
    root: Path,
    depth: int,
    fanout: int,
    *,
    leaf: str = "page.py",
    leaf_body: str = _DEFAULT_LEAF_BODY,
) -> None:
    """Materialise a synthetic page tree of ``depth`` * ``fanout`` under ``root``."""
    if depth == 0:
        (root / leaf).write_text(leaf_body)
        return
    for i in range(fanout):
        child = root / f"n_{i}"
        child.mkdir()
        build_pages_tree(child, depth - 1, fanout, leaf=leaf, leaf_body=leaf_body)


def build_component_djx_dir(root: Path, count: int) -> None:
    """Write ``count`` simple ``.djx`` components into ``root`` (flat)."""
    for i in range(count):
        (root / f"comp_{i}.djx").write_text(f"<div>c{i}</div>")


def noop_form_handler(**kwargs) -> None:  # pragma: no cover - bench stub
    """Stub action handler for form-action benchmarks."""


def noop_signal_receiver(sender: object, **kwargs) -> None:  # pragma: no cover
    """No-op Django signal receiver for signal-overhead benchmarks."""
    del sender


def build_component_info(root: Path, name: str = "c") -> ComponentInfo:
    """Create one ``ComponentInfo`` without writing to the filesystem."""
    return ComponentInfo(
        name=name,
        scope_root=root,
        scope_relative="",
        template_path=root / f"{name}.djx",
        module_path=None,
        is_simple=True,
    )


def build_component_info_list(root: Path, count: int) -> list[ComponentInfo]:
    """Create ``count`` ``ComponentInfo`` objects without writing to the filesystem."""
    return [build_component_info(root, f"c_{i}") for i in range(count)]


def build_layout_page(
    root: Path, *, layouts: int, template: str | None = None, page_body: str = "x = 1\n"
) -> Path:
    """Materialise a page nested under ``layouts`` ancestor ``layout.djx`` wrappers.

    The layouts sit strictly above the page directory, the shape the walk pays for.
    """
    directory = root
    for i in range(layouts):
        (directory / "layout.djx").write_text(
            f'<div class="l{i}">{{% template %}}</div>'
        )
        directory = directory / f"seg_{i}"
        directory.mkdir()
    (directory / "page.py").write_text(page_body)
    if template is not None:
        (directory / "template.djx").write_text(template)
    return directory / "page.py"


class StandInRows:
    """Rows answering every lookup with one unsaved row, so no query is ever made."""

    def __init__(self, row: Group) -> None:
        """Answer every lookup with `row`."""
        self._row = row

    def visible_to(self, scope: object) -> Self:
        """Return the rows themselves, whatever the scope."""
        return self

    def filter(self, **lookups: object) -> Self:
        """Return the rows themselves, whatever the lookups."""
        return self

    def order_by(self) -> Self:
        """Return the rows themselves, unordered."""
        return self

    def __getitem__(self, window: slice) -> list[Group]:
        """Return the one row, whatever the window."""
        return [self._row]


def build_boundary_apps(count: int) -> dict[str, dict[str, str]]:
    """Return `count` apps in a ring, each joined to the next by all eight edge kinds."""
    apps: dict[str, dict[str, str]] = {}
    for index in range(count):
        label = f"ring{index:02d}"
        target = f"ring{(index + 1) % count:02d}"
        source = f"ring{(index - 1) % count:02d}"
        apps[label] = {
            "models.py": (
                "from django.db import models\n\n\n"
                "class Item(models.Model):\n"
                f'    peer = models.ForeignKey("{target}.Item", null=True, '
                "on_delete=models.CASCADE)\n"
            ),
            "utils.py": "helper = 1\n",
            "services.py": (
                "from django.apps import apps\n\n"
                f"from {target}.utils import helper\n\n"
                f'peer = apps.get_model("{target}", "Item")\n'
            ),
            "signals.py": (
                "from django.dispatch import Signal, receiver\n\n\n"
                "changed = Signal()\n\n"
                f"from {source}.signals import changed as upstream\n\n\n"
                "@receiver(upstream)\n"
                "def on_upstream(sender, **kwargs):\n    pass\n"
            ),
            "deps.py": (
                "from next.deps import resolver\n\n\n"
                f'@resolver.dependency("{label}_value")\n'
                "def value():\n    return 1\n"
            ),
            "pages/page.py": (
                "from next.deps import Depends\n\n\n"
                f'def render(request, peer=Depends("{target}_value")):\n'
                f'    Patches(request).refresh(zone="{target}")\n\n\n'
                f"template = \"{{% zone '{label}' %}}{{{{ peer }}}}{{% endzone %}}\"\n"
            ),
            f"pages/_components/{label}_badge/component.djx": (
                f"{{% component '{target}_badge' %}}"
                f"{{% has_perm '{target}.view_item' %}}x{{% endhas_perm %}}\n"
            ),
        }
    return apps
