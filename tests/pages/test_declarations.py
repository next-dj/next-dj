from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

from next.pages import declarations as page_declarations
from next.pages.declarations import ModuleDeclarations


@dataclass(frozen=True, slots=True)
class _Call:
    namespace: dict[str, object]


def _module() -> ModuleType:
    return ModuleType("page_module")


class TestLoaded:
    """`loaded` yields only the calls the latest load of each file made."""

    def test_a_stale_or_failed_load_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kept, reloaded = _module(), _module()
        modules = {
            Path("/srv/kept/page.py"): kept,
            Path("/srv/reloaded/page.py"): reloaded,
            Path("/srv/broken/page.py"): None,
        }
        monkeypatch.setattr(
            page_declarations, "load_page_module", lambda path: (modules[path], None)
        )
        store: ModuleDeclarations[_Call] = ModuleDeclarations()
        held = _Call(vars(kept))
        store.declare(Path("/srv/kept/page.py"), held)
        store.declare(Path("/srv/reloaded/page.py"), _Call(vars(_module())))
        store.declare(Path("/srv/broken/page.py"), _Call({}))

        assert list(store.loaded()) == [(Path("/srv/kept/page.py"), held)]
