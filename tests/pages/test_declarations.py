from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.boundaries.options import BoundaryOptions, DeferMode
from next.introspect import DeclaringSite
from next.pages import declarations as declarations_module
from next.pages.compression import compress_declaration
from next.pages.declarations import (
    Inherit,
    PageDeclaration,
    page_declarations,
    register_declaration,
)
from next.pages.defer import defer_declaration
from next.pages.loaders import (
    load_page_module,
    load_python_module_memo,
    reset_module_memo,
)
from next.pages.manager import reset_page_registrations
from next.pages.transactions import transaction_declaration
from next.permissions.errors import DeclarationOutsideModuleError
from next.router.declarations import DEFAULT_OPTIONS, RouterOptions, router_declaration
from next.transactions import AtomicOverride
from tests.support.pages import write_page_chain


_PAGE = Path("/srv/shop/page.py")


class _Counted:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, value: str = "declared") -> str:
        self.calls += 1
        if not value:
            msg = "value must not be empty"
            raise ValueError(msg)
        return value


def _site(
    path: Path = _PAGE, namespace: dict[str, object] | None = None
) -> DeclaringSite:
    return DeclaringSite(path, True, {} if namespace is None else namespace)


@pytest.fixture()
def registered() -> Iterator[Callable[..., PageDeclaration]]:
    """Return a maker of declarations registered for the test alone."""
    made: list[PageDeclaration] = []

    def make(name: str = "audit", **options: object) -> PageDeclaration:
        declaration = PageDeclaration(name, _Counted(), None, **options)
        register_declaration(declaration)
        made.append(declaration)
        return declaration

    yield make
    for declaration in made:
        page_declarations.unregister(declaration)


@pytest.fixture()
def loaded(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = ModuleType("page_module")
    monkeypatch.setattr(
        declarations_module, "load_python_module_memo", lambda _path: module
    )
    return module


class TestRegistry:
    """The registry holds each declaration once, by name, in registration order."""

    def test_ready_registers_the_shipped_declarations(self) -> None:
        assert [declaration.name for declaration in page_declarations] == [
            "transaction",
            "compress",
            "defer",
            "router",
        ]

    def test_registering_twice_keeps_one(
        self, registered: Callable[..., PageDeclaration]
    ) -> None:
        declaration = registered()
        start = page_declarations.version
        register_declaration(declaration)
        assert page_declarations.version == start
        assert page_declarations.named("audit") is declaration
        assert declaration in page_declarations

    def test_a_second_declaration_under_a_taken_name_raises(
        self, registered: Callable[..., PageDeclaration]
    ) -> None:
        registered()
        with pytest.raises(ImproperlyConfigured, match=r"page.audit\(\) is registered"):
            register_declaration(PageDeclaration("audit", _Counted(), None))

    def test_an_unknown_name_raises(self) -> None:
        with pytest.raises(ImproperlyConfigured, match=r"page.audit\(\) is not"):
            page_declarations.named("audit")
        assert "audit" not in page_declarations

    def test_a_call_to_an_unregistered_declaration_raises(self) -> None:
        declaration = PageDeclaration("audit", _Counted(), None)
        with pytest.raises(ImproperlyConfigured, match="ready"):
            declaration.declare(_site())

    def test_unregistering_drops_the_calls(
        self, registered: Callable[..., PageDeclaration]
    ) -> None:
        declaration = registered()
        declaration.declare(_site(Path("/srv/helpers.py")))
        page_declarations.unregister(declaration)
        assert declaration.outside() == ()
        assert declaration not in page_declarations

    @pytest.mark.parametrize(
        ("inherit", "merge"),
        [(Inherit.MERGE, None), (Inherit.OWN, max), (Inherit.NEAREST, max)],
    )
    def test_merge_goes_with_the_merge_rule_alone(
        self, inherit: Inherit, merge: Callable[[str, str], str] | None
    ) -> None:
        with pytest.raises(ValueError, match="merge function"):
            PageDeclaration("audit", _Counted(), None, inherit=inherit, merge=merge)

    def test_a_declaration_names_its_call(self) -> None:
        assert repr(router_declaration) == "<PageDeclaration page.router()>"


class TestDeclare:
    """A call binds its own `page.py`, a call from any other module is only noted."""

    def test_a_page_module_declares(
        self, registered: Callable[..., PageDeclaration], loaded: ModuleType
    ) -> None:
        declaration = registered()
        start = page_declarations.version
        declaration.declare(_site(namespace=vars(loaded)), "kept")
        assert declaration.of(_PAGE) == "kept"
        assert declaration.outside() == ()
        assert page_declarations.version == start + 1

    def test_a_call_off_the_top_level_raises(
        self, registered: Callable[..., PageDeclaration]
    ) -> None:
        declaration = registered()
        with pytest.raises(DeclarationOutsideModuleError, match=str(_PAGE)):
            declaration.declare(DeclaringSite(_PAGE, False, {}))
        assert declaration.of(_PAGE) is None

    def test_a_misfit_raises_naming_the_call(
        self, registered: Callable[..., PageDeclaration]
    ) -> None:
        declaration = registered()
        with pytest.raises(ImproperlyConfigured) as caught:
            declaration.declare(_site(), "")
        assert str(caught.value) == (
            f"page.audit() in {_PAGE} declares nothing, since value must not be empty."
        )

    def test_another_module_is_noted_outside(
        self, registered: Callable[..., PageDeclaration]
    ) -> None:
        declaration = registered()
        helper = Path("/srv/shop/helpers.py")
        declaration.declare(_site(helper))
        assert declaration.outside() == (helper,)
        page_declarations.reset()
        assert declaration.outside() == ()

    def test_the_declaration_itself_is_the_call_of_a_third_party(
        self, registered: Callable[..., PageDeclaration], tmp_path: Path
    ) -> None:
        declaration = registered()
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages.declarations import page_declarations\n\n"
            "audit = page_declarations.named('audit')\n"
            "audit('strict')\n"
        )
        load_python_module_memo(page_file)
        assert declaration.of(page_file) == "strict"


class TestOf:
    """`of` reads a page once per module load and answers at once without calls."""

    def test_a_declaration_without_a_call_reads_nothing(
        self,
        registered: Callable[..., PageDeclaration],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def refuse(_path: Path) -> None:
            raise AssertionError

        monkeypatch.setattr(declarations_module, "load_python_module_memo", refuse)
        assert registered().of(_PAGE) is None

    def test_the_answer_is_held_until_a_module_loads(
        self,
        registered: Callable[..., PageDeclaration],
        loaded: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        declaration = registered()
        declaration.declare(_site(namespace=vars(loaded)))
        reads: list[Path] = []
        monkeypatch.setattr(
            declarations_module,
            "load_python_module_memo",
            lambda path: reads.append(path) or loaded,
        )
        assert declaration.of(_PAGE) == "declared"
        assert declaration.of(_PAGE) == "declared"
        assert reads == [_PAGE]
        reset_module_memo()
        declaration.of(_PAGE)
        assert reads == [_PAGE, _PAGE]

    @pytest.mark.usefixtures("watched_template_edits")
    def test_watched_edits_read_the_page_every_time(
        self,
        registered: Callable[..., PageDeclaration],
        loaded: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        declaration = registered()
        declaration.declare(_site(namespace=vars(loaded)))
        reads: list[Path] = []
        monkeypatch.setattr(
            declarations_module,
            "load_python_module_memo",
            lambda path: reads.append(path) or loaded,
        )
        declaration.of(_PAGE)
        declaration.of(_PAGE)
        assert reads == [_PAGE, _PAGE]

    def test_a_stale_call_takes_the_default(
        self, registered: Callable[..., PageDeclaration], loaded: ModuleType
    ) -> None:
        declaration = registered()
        declaration.declare(_site(namespace={}))
        assert declaration.of(_PAGE) is None

    def test_a_page_that_no_longer_loads_takes_the_default(
        self,
        registered: Callable[..., PageDeclaration],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        declaration = registered()
        declaration.declare(_site())
        monkeypatch.setattr(
            declarations_module, "load_python_module_memo", lambda _path: None
        )
        assert declaration.of(_PAGE) is None


def _chain(tmp_path: Path, top: str, middle: str, leaf: str) -> list[Path]:
    head = "from next.pages.declarations import page_declarations\n\n"
    head += "audit = page_declarations.named('audit')\n"
    return write_page_chain(
        tmp_path, [("shop", head + top), ("books", head + middle), ("new", head + leaf)]
    )


class TestInherit:
    """The rule of a declaration decides what the pages below its `page.py` read."""

    def test_own_binds_only_the_declaring_page(
        self, registered: Callable[..., PageDeclaration], tmp_path: Path
    ) -> None:
        declaration = registered()
        top, _middle, leaf = _chain(tmp_path, "audit('top')\n", "", "")
        load_python_module_memo(top)
        assert declaration.of(top) == "top"
        assert declaration.of(leaf) is None

    def test_nearest_takes_the_closest_declaring_page(
        self, registered: Callable[..., PageDeclaration], tmp_path: Path
    ) -> None:
        declaration = registered(inherit=Inherit.NEAREST)
        top, middle, leaf = _chain(tmp_path, "audit('top')\n", "audit('mid')\n", "")
        assert declaration.of(leaf) == "mid"
        assert declaration.of(top) == "top"
        assert declaration.of(middle) == "mid"

    def test_nearest_without_a_declaring_page_takes_the_default(
        self, registered: Callable[..., PageDeclaration], tmp_path: Path
    ) -> None:
        declaration = registered(inherit=Inherit.NEAREST)
        helper = tmp_path / "helpers.py"
        declaration.declare(_site(helper))
        _top, _middle, leaf = _chain(tmp_path, "", "", "")
        assert declaration.of(leaf) is None

    def test_merge_folds_every_declaring_page_root_first(
        self, registered: Callable[..., PageDeclaration], tmp_path: Path
    ) -> None:
        declaration = registered(
            inherit=Inherit.MERGE, merge=lambda above, below: f"{above}+{below}"
        )
        top, middle, leaf = _chain(tmp_path, "audit('top')\n", "", "audit('leaf')\n")
        for page_file in (top, middle, leaf):
            load_python_module_memo(page_file)
        assert declaration.of(leaf) == "top+leaf"
        assert declaration.of(middle) == "top"

    def test_merge_without_a_declaring_page_takes_the_default(
        self, registered: Callable[..., PageDeclaration], tmp_path: Path
    ) -> None:
        declaration = registered(inherit=Inherit.MERGE, merge=max)
        declaration.declare(_site(tmp_path / "helpers.py"))
        _top, _middle, leaf = _chain(tmp_path, "", "", "")
        assert declaration.of(leaf) is None


@dataclass(frozen=True, slots=True)
class DeclarationCase:
    """One shipped `page` declaration, the calls that declare it and what it reads."""

    declaration: PageDeclaration
    default: object
    call: str
    declared: object
    later_call: str
    later_declared: object


CASES = [
    pytest.param(
        DeclarationCase(
            compress_declaration,
            None,
            "page.compress(enabled=False)",
            False,
            "page.compress(enabled=True)",
            True,
        ),
        id="compress",
    ),
    pytest.param(
        DeclarationCase(
            defer_declaration,
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
            router_declaration,
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
            transaction_declaration,
            None,
            "page.transaction(atomic=True)",
            AtomicOverride(atomic=True),
            "page.transaction(atomic=False)",
            AtomicOverride(atomic=False),
        ),
        id="transaction",
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
        assert case.declaration.of(plain) is case.default

    def test_the_call_binds_its_own_page(
        self,
        case: DeclarationCase,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
    ) -> None:
        page_file = page_declaring(tmp_path, case.call)
        load_python_module_memo(page_file)
        assert case.declaration.of(page_file) == case.declared

    def test_another_page_declares_nothing(
        self,
        case: DeclarationCase,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
    ) -> None:
        load_python_module_memo(page_declaring(tmp_path / "a", case.call))
        other = page_declaring(tmp_path / "b", "")
        load_python_module_memo(other)
        assert case.declaration.of(other) is case.default

    def test_the_last_call_of_a_module_wins(
        self,
        case: DeclarationCase,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
    ) -> None:
        page_file = page_declaring(tmp_path, f"{case.call}\n{case.later_call}")
        load_python_module_memo(page_file)
        assert case.declaration.of(page_file) == case.later_declared

    def test_a_removed_call_stops_applying_after_a_reload(
        self,
        case: DeclarationCase,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
    ) -> None:
        page_file = page_declaring(tmp_path, case.call)
        load_python_module_memo(page_file)
        assert case.declaration.of(page_file) == case.declared
        page_declaring(tmp_path, "")
        reset_module_memo()
        assert case.declaration.of(page_file) is case.default

    def test_a_call_inside_a_function_raises_on_load(
        self,
        case: DeclarationCase,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
    ) -> None:
        page_file = page_declaring(
            tmp_path, f"def declare():\n    {case.call}\n\n\ndeclare()"
        )
        module, error = load_page_module(page_file)
        assert module is None
        assert isinstance(error.__cause__, DeclarationOutsideModuleError)
        assert case.declaration.of(page_file) is case.default

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
        assert case.declaration.outside() == (helper,)
        reset_page_registrations()
        assert case.declaration.outside() == ()

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
        assert case.declaration.of(source) is case.default
        assert case.declaration.of(page_file) is case.default
        assert case.declaration.outside() == (source,)


def _team(name: str) -> str:
    if not name.isidentifier():
        msg = f"the team must be an identifier, got {name!r}"
        raise ValueError(msg)
    return name


owner = PageDeclaration("owner", _team, "platform", inherit=Inherit.NEAREST)


class TestOwnerRecipe:
    """The declaration of the how-to guide names the team of a section once."""

    @pytest.fixture(autouse=True)
    def _registered(self) -> Iterator[None]:
        register_declaration(owner)
        yield
        page_declarations.unregister(owner)

    def test_a_section_names_the_owner_of_its_pages(self, tmp_path: Path) -> None:
        pay = tmp_path / "checkout" / "pay"
        pay.mkdir(parents=True)
        (tmp_path / "checkout" / "page.py").write_text(
            "from next.pages.declarations import page_declarations\n\n"
            "owner = page_declarations.named('owner')\n"
            "owner('checkout')\n"
        )
        (pay / "page.py").write_text("template = '<p>pay</p>'\n")
        (tmp_path / "page.py").write_text("template = '<p>home</p>'\n")
        assert owner.of(pay / "page.py") == "checkout"
        assert owner.of(tmp_path / "page.py") == "platform"

    def test_a_name_that_is_no_identifier_raises_on_load(self, tmp_path: Path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages.declarations import page_declarations\n\n"
            "page_declarations.named('owner')('check out')\n"
        )
        _module, error = load_page_module(page_file)
        assert str(error.__cause__) == (
            f"page.owner() in {page_file} declares nothing, since the team must be "
            "an identifier, got 'check out'."
        )
