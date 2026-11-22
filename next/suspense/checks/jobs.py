"""The check of what code a deferred job runs, `durable=True` under connection state."""

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from django.core.checks import CheckMessage, Warning as DjangoWarning, register

from next.checks import NEXT
from next.conf.defer import backend_paths
from next.pages.checks.contexts import load_routed_pages
from next.pages.loaders import load_page_module, page_boundaries, serves
from next.pages.manager import page

from .calls import deferred_components


_DURABLE: Final = "durable"


def _source_file(func: object) -> Path | None:
    """Return the file a function was defined in, `None` for one without code."""
    code = getattr(func, "__code__", None)
    filename = getattr(code, "co_filename", None)
    return Path(filename) if isinstance(filename, str) else None


def _deferred_page_sources() -> Iterator[Path | None]:
    """Yield each page with a page boundary and the files of its deferred callables."""
    for _url, page_path in load_routed_pages() or []:
        chain = page_boundaries(page_path)
        if not (chain.deferred and serves(page_path, *load_page_module(page_path))):
            continue
        yield page_path
        plan = page.gating_plan(page_path, chain)
        steps = plan.graph.steps
        for _source, gated in plan.split.jobs:
            for index in sorted(gated):
                yield _source_file(steps[index].func)


def _deferred_sources() -> list[Path]:
    """Return the modules deferred jobs run code from, each once, in a stable order."""
    found = [info.module_path for info in deferred_components()]
    found.extend(_deferred_page_sources())
    return list(dict.fromkeys(path.resolve() for path in found if path is not None))


def _durable(path: Path) -> bool:
    """Whether the source of `path` passes `durable=True`, an unreadable one not."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return False
    return any(
        isinstance(node, ast.keyword)
        and node.arg == _DURABLE
        and isinstance(node.value, ast.Constant)
        and node.value.value is True
        for node in ast.walk(tree)
    )


@register(NEXT)
def check_durable_jobs(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about `durable=True` in a deferred job with connection state (`next.W173`).

    The scan reads the literal keyword only, so a value it cannot see is not found.
    """
    if not backend_paths("CONNECTION_STATE"):
        return []
    return [
        DjangoWarning(
            f"{path} passes durable=True while NEXT_FRAMEWORK['DEFER']"
            "['CONNECTION_STATE'] is set. A ConnectionState replays its state with "
            "transaction-local statements, so a deferred job runs inside a "
            "transaction and a durable atomic block there raises.",
            hint=(
                "Drop durable=True from code a deferred boundary runs, or render "
                "that boundary inline."
            ),
            obj=str(path),
            id="next.W173",
        )
        for path in _deferred_sources()
        if _durable(path)
    ]


__all__ = ["check_durable_jobs"]
