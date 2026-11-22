from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

from next.defer import BoundaryOptions, DeferMode
from next.pages import declarations as page_declarations
from next.pages.compression import compress_store, page_compress
from next.pages.declarations import NESTED, DeclarationStore
from next.pages.defer import page_defer, page_defer_store
from next.pages.loaders import load_python_module_memo, reset_module_memo
from next.pages.manager import reset_page_registrations
from next.pages.router import DEFAULT_OPTIONS, RouterOptions, page_router, router_store
from next.pages.transactions import atomic_store, page_atomic
from next.transactions import AtomicOverride


@dataclass(frozen=True, slots=True)
class _Call:
    namespace: dict[str, object]
    top_level: bool = True
    value: str | None = "declared"


_PAGE = Path("/srv/shop/page.py")


def _module() -> ModuleType:
    return ModuleType("page_module")


class _Counted:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, call: _Call) -> str | None:
        self.calls += 1
        return call.value


@pytest.fixture()
def loaded(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _module()
    monkeypatch.setattr(page_declarations, "load_python_module_memo", lambda _p: module)
    return module


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
        store: DeclarationStore[_Call, str | None] = DeclarationStore(_Counted(), None)
        held = _Call(vars(kept))
        store.declare(Path("/srv/kept/page.py"), held)
        store.declare(Path("/srv/reloaded/page.py"), _Call(vars(_module())))
        store.declare(Path("/srv/broken/page.py"), _Call({}))

        assert list(store.loaded()) == [(Path("/srv/kept/page.py"), held)]


class TestRecord:
    """A call binds its own `page.py`, a call from any other module is only noted."""

    def test_a_page_module_declares(self) -> None:
        store: DeclarationStore[_Call, str | None] = DeclarationStore(_Counted(), None)
        module = _module()
        store.record((_PAGE, False), vars(module), _Call)
        assert store.current(_PAGE, module) == _Call(vars(module), top_level=False)
        assert store.outside() == ()

    def test_another_module_is_noted_outside(self) -> None:
        store: DeclarationStore[_Call, str | None] = DeclarationStore(_Counted(), None)
        helper = Path("/srv/shop/helpers.py")
        store.record((helper, True), {}, _Call)
        assert not store
        assert store.outside() == (helper,)
        store.reset()
        assert store.outside() == ()

    def test_every_change_moves_the_version(self) -> None:
        store: DeclarationStore[_Call, str | None] = DeclarationStore(_Counted(), None)
        start = store.version
        store.declare(_PAGE, _Call({}))
        store.reset()
        assert store.version == start + 2
        assert not store

    def test_the_nested_reason_names_the_function(self) -> None:
        assert "inside a function" in NESTED


class TestResolved:
    """`resolved` reads a page once per module load and answers at once without calls."""

    def test_a_store_without_a_call_reads_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(_path: Path) -> None:
            raise AssertionError

        monkeypatch.setattr(page_declarations, "load_python_module_memo", refuse)
        read = _Counted()
        store: DeclarationStore[_Call, str] = DeclarationStore(read, "default")
        assert store.resolved(_PAGE) == "default"
        assert read.calls == 0

    def test_the_answer_is_held_until_a_module_loads(self, loaded: ModuleType) -> None:
        read = _Counted()
        store: DeclarationStore[_Call, str] = DeclarationStore(read, "default")
        store.declare(_PAGE, _Call(vars(loaded)))
        assert store.resolved(_PAGE) == "declared"
        assert store.resolved(_PAGE) == "declared"
        assert read.calls == 1
        reset_module_memo()
        store.resolved(_PAGE)
        assert read.calls == 2

    @pytest.mark.usefixtures("watched_template_edits")
    def test_watched_edits_read_the_page_every_time(self, loaded: ModuleType) -> None:
        read = _Counted()
        store: DeclarationStore[_Call, str] = DeclarationStore(read, "default")
        store.declare(_PAGE, _Call(vars(loaded)))
        store.resolved(_PAGE)
        store.resolved(_PAGE)
        assert read.calls == 2

    def test_a_stale_call_takes_the_default(self, loaded: ModuleType) -> None:
        store: DeclarationStore[_Call, str] = DeclarationStore(_Counted(), "default")
        store.declare(_PAGE, _Call({}))
        assert store.resolved(_PAGE) == "default"

    def test_a_call_declaring_nothing_takes_the_default(
        self, loaded: ModuleType
    ) -> None:
        store: DeclarationStore[_Call, str] = DeclarationStore(_Counted(), "default")
        store.declare(_PAGE, _Call(vars(loaded), value=None))
        assert store.resolved(_PAGE) == "default"


@dataclass(frozen=True, slots=True)
class DeclarationCase:
    """One `page` declaration, the calls that declare it and what its reader answers."""

    name: str
    store: DeclarationStore
    read: Callable[[Path], object]
    default: object
    call: str
    declared: object
    later_call: str
    later_declared: object


CASES = [
    pytest.param(
        DeclarationCase(
            "compress",
            compress_store,
            page_compress,
            None,
            "page.compress(False)",
            False,
            "page.compress(True)",
            True,
        ),
        id="compress",
    ),
    pytest.param(
        DeclarationCase(
            "defer",
            page_defer_store,
            page_defer,
            None,
            "page.defer(timeout=1)",
            BoundaryOptions(mode=DeferMode.STREAM, timeout=1.0),
            "page.defer(timeout=2)",
            BoundaryOptions(mode=DeferMode.STREAM, timeout=2.0),
        ),
        id="defer",
    ),
    pytest.param(
        DeclarationCase(
            "router",
            router_store,
            page_router,
            DEFAULT_OPTIONS,
            "page.router(navigate=False)",
            RouterOptions(navigate=False),
            "page.router(prefetch=False)",
            RouterOptions(prefetch=False),
        ),
        id="router",
    ),
    pytest.param(
        DeclarationCase(
            "atomic",
            atomic_store,
            page_atomic,
            None,
            "page.atomic(True)",
            AtomicOverride(atomic=True),
            "page.atomic(False)",
            AtomicOverride(atomic=False),
        ),
        id="atomic",
    ),
]


@pytest.mark.usefixtures("fresh_declarations")
@pytest.mark.parametrize("case", CASES)
class TestDeclarationContract:
    """Every `page` declaration binds one page, the last call winning, per module load."""

    def test_a_project_without_a_declaration_reads_the_default(
        self,
        case: DeclarationCase,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
    ) -> None:
        plain = page_declaring(tmp_path, "")
        load_python_module_memo(plain)
        assert not case.store
        assert case.read(plain) is case.default

    def test_the_call_binds_its_own_page(
        self,
        case: DeclarationCase,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
    ) -> None:
        page_file = page_declaring(tmp_path, case.call)
        load_python_module_memo(page_file)
        assert case.read(page_file) == case.declared

    def test_another_page_declares_nothing(
        self,
        case: DeclarationCase,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
    ) -> None:
        load_python_module_memo(page_declaring(tmp_path / "a", case.call))
        other = page_declaring(tmp_path / "b", "")
        load_python_module_memo(other)
        assert case.read(other) is case.default

    def test_the_last_call_of_a_module_wins(
        self,
        case: DeclarationCase,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
    ) -> None:
        page_file = page_declaring(tmp_path, f"{case.call}\n{case.later_call}")
        load_python_module_memo(page_file)
        assert case.read(page_file) == case.later_declared

    def test_a_removed_call_stops_applying_after_a_reload(
        self,
        case: DeclarationCase,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
    ) -> None:
        page_file = page_declaring(tmp_path, case.call)
        load_python_module_memo(page_file)
        assert case.read(page_file) == case.declared
        page_declaring(tmp_path, "")
        reset_module_memo()
        assert case.read(page_file) is case.default

    def test_a_call_inside_a_function_declares_nothing(
        self,
        case: DeclarationCase,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
    ) -> None:
        indented = case.call.replace("\n", "\n    ")
        page_file = page_declaring(
            tmp_path, f"def declare():\n    {indented}\n\n\ndeclare()"
        )
        load_python_module_memo(page_file)
        assert case.read(page_file) is case.default
        [(file_path, held)] = list(case.store.loaded())
        assert file_path == page_file
        assert held.problem() == NESTED

    def test_a_page_reset_drops_every_declaration(
        self,
        case: DeclarationCase,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
    ) -> None:
        load_python_module_memo(page_declaring(tmp_path, case.call))
        helper = tmp_path / "helpers.py"
        helper.write_text(f"from next.pages import page\n\n{case.call}\n")
        load_python_module_memo(helper)
        assert case.store
        assert case.store.outside() == (helper,)
        reset_page_registrations()
        assert not case.store
        assert case.store.outside() == ()

    @pytest.mark.parametrize("name", ["component.py", "helpers.py"])
    def test_a_call_outside_a_page_module_declares_nothing(
        self,
        case: DeclarationCase,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
        name: str,
    ) -> None:
        source = tmp_path / name
        source.write_text(f"from next.pages import page\n\n{case.call}\n")
        page_file = page_declaring(tmp_path / "shop", "")
        load_python_module_memo(source)
        load_python_module_memo(page_file)
        assert not case.store
        assert case.read(source) is case.default
        assert case.read(page_file) is case.default
        assert case.store.outside() == (source,)
