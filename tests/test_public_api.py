import importlib
import pkgutil
import subprocess
import sys

import pytest

import next as next_dj
from next import _LAZY_ATTRIBUTES
from next.components import component
from next.deps import Depends
from next.forms import action
from next.pages import context, page


_CURATED = frozenset({"Depends", "action", "component", "context", "page"})


class TestCuratedSurface:
    """`next.__all__` is the curated top-level facade plus the version constant."""

    def test_all_matches_curated_set(self) -> None:
        assert frozenset(next_dj.__all__) == _CURATED | {"VERSION"}

    def test_all_has_no_duplicates(self) -> None:
        assert len(next_dj.__all__) == len(set(next_dj.__all__))

    def test_lazy_map_covers_every_curated_name(self) -> None:
        assert set(_LAZY_ATTRIBUTES) == _CURATED

    @pytest.mark.parametrize("name", sorted(_CURATED))
    def test_every_curated_name_resolves(self, name: str) -> None:
        assert getattr(next_dj, name) is not None

    def test_curated_names_are_the_subpackage_objects(self) -> None:
        assert next_dj.page is page
        assert next_dj.context is context
        assert next_dj.component is component
        assert next_dj.action is action
        assert next_dj.Depends is Depends

    def test_unknown_name_raises_attribute_error(self) -> None:
        with pytest.raises(AttributeError, match="no attribute 'nonexistent'"):
            next_dj.__getattr__("nonexistent")

    def test_hasattr_contract_stays_honest(self) -> None:
        assert hasattr(next_dj, "page")
        assert not hasattr(next_dj, "nonexistent")

    def test_lazy_names_do_not_shadow_subpackages(self) -> None:
        subpackages = {info.name for info in pkgutil.iter_modules(next_dj.__path__)}
        assert _CURATED & subpackages == frozenset()


class TestDirContract:
    """Module __dir__ lists the curated names on top of the live namespace."""

    def test_dir_covers_all(self) -> None:
        assert set(next_dj.__all__) <= set(next_dj.__dir__())

    def test_dir_is_sorted(self) -> None:
        listed = next_dj.__dir__()
        assert listed == sorted(listed)

    def test_dir_has_no_duplicates(self) -> None:
        listed = next_dj.__dir__()
        assert len(listed) == len(set(listed))

    def test_dir_lists_the_metadata_globals(self) -> None:
        assert {"__title__", "__version__", "__author__"} <= set(next_dj.__dir__())

    def test_dir_lists_an_imported_subpackage(self) -> None:
        importlib.import_module("next.deps")
        assert "deps" in next_dj.__dir__()


class TestImportStaysDjangoFree:
    """Importing next must not drag Django into a fresh interpreter."""

    def test_no_django_modules_after_import(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys\n"
                    "import next\n"
                    "print(len([m for m in sys.modules if m.startswith('django')]))"
                ),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "0"
