import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from operator import attrgetter

import pytest
from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpRequest
from django.test import RequestFactory

from next.deps import (
    DependencyCycleError,
    DependencyResolver,
    Depends,
    UnknownDependencyError,
)
from next.deps.linear import LinearDependencyResolver
from next.deps.resolver import _introspect_key
from next.forms import DForm
from next.pages.context import Context
from next.testing import make_resolution_context
from next.urls import DQuery, DUrl
from tests.support import AForm, DeferringProvider, OtherForm, inspect_parameter


_REQUEST = RequestFactory().get("/?page=3&tags=a,b")
_FORM = AForm()


def _factory(request: HttpRequest | None = None, page_value: int = Context()) -> str:
    return f"{request is not None}:{page_value}"


def _with_theme(resolver_class: type[DependencyResolver]) -> DependencyResolver:
    """Build one resolver of the given class with the `theme` dependency bound."""
    built = resolver_class()
    built.dependency("theme")(lambda: "dark")
    return built


def _linear() -> LinearDependencyResolver:
    return LinearDependencyResolver()


def _plain(value) -> None:
    return None


def _defaulted(value: int = 7) -> None:
    return None


def _string_hint(user_id: "int") -> None:
    return None


def _unresolvable(value: HttpRequest | None = None) -> None:
    return None


_unresolvable.__annotations__["value"] = "NoSuchTypeAtAll"


def _deferred(value: HttpRequest | None = None) -> None:
    return None


# Its own callable, because a hint memo is process-wide and the test below makes
# this one resolve for good.
_deferred.__annotations__["value"] = "_LateHttpRequest"


def _skipping(self, *args, value: int = 1, **kwargs) -> None:
    return None


def _named(value: str = Depends("missing")) -> None:
    return None


def _outer(inner: str = Depends("inner"), value=None) -> None:
    return None


def _inner_dep(value=None) -> str:
    return "inner"


class TestFillTargets:
    """The walk derives the same parameters a compile would."""

    def test_a_signature_nothing_can_read_fills_nothing(self) -> None:
        assert _linear()._fill_targets(int) == ()
        assert _linear().resolve_dependencies(int) == {}

    def test_hints_that_do_not_resolve_keep_the_raw_annotation(self) -> None:
        (target,) = _linear()._fill_targets(_unresolvable)
        assert target[1].annotation == "NoSuchTypeAtAll"
        assert _linear().resolve_dependencies(_unresolvable, request=_REQUEST) == {
            "value": None
        }

    def test_a_resolved_hint_replaces_the_string_annotation(self) -> None:
        (target,) = _linear()._fill_targets(_string_hint)
        assert target[1].annotation is int
        assert _linear().resolve_dependencies(_string_hint, user_id="42") == {
            "user_id": 42
        }

    def test_self_and_the_variadics_are_skipped(self) -> None:
        assert [name for name, _p, _f in _linear()._fill_targets(_skipping)] == [
            "value"
        ]

    def test_the_fallback_is_the_default_or_none(self) -> None:
        assert _linear()._fill_targets(_plain)[0][2] is None
        assert _linear()._fill_targets(_defaulted)[0][2] == 7


class TestLinearResolve:
    """The walk asks every provider and stops at the first claim."""

    def test_the_first_claiming_provider_wins(self) -> None:
        built = _linear()
        first = DeferringProvider("value", "FIRST")
        second = DeferringProvider("value", "SECOND")
        built.prepend_provider(second)
        built.prepend_provider(first)
        assert built.resolve_dependencies(_plain) == {"value": "FIRST"}

    def test_an_unclaimed_parameter_takes_its_fallback(self) -> None:
        assert _linear().resolve_dependencies(_defaulted) == {"value": 7}

    def test_every_provider_is_asked_about_an_unclaimed_parameter(self) -> None:
        built = _linear()
        stub = DeferringProvider(None)
        built.prepend_provider(stub)
        assert built.resolve_dependencies(_plain) == {"value": None}
        assert stub.seen == ["value"]

    def test_a_marker_parameter_still_walks_can_handle(self) -> None:
        """The compiled short-list is what the linear path gives up."""
        built = _with_theme(LinearDependencyResolver)
        stub = DeferringProvider(None)
        built.prepend_provider(stub)

        def by_marker(theme: str = Depends("theme")) -> None:
            return None

        assert built.resolve_dependencies(by_marker) == {"theme": "dark"}
        assert stub.seen == ["theme"]

    def test_an_unknown_dependency_names_the_owning_callable(self) -> None:
        built = _linear()
        with pytest.raises(UnknownDependencyError) as exc_info:
            built.resolve_dependencies(_named)
        assert exc_info.value.func is _named

    def test_an_unknown_dependency_names_the_innermost_callable(self) -> None:
        built = _linear()
        built.dependency("inner")(_named)
        with pytest.raises(UnknownDependencyError) as exc_info:
            built.resolve_dependencies(_outer)
        assert exc_info.value.func is _named

    def test_a_cycle_is_refused(self) -> None:
        built = _linear()

        def loop(value: str = Depends("loop")) -> str:
            return value

        built.dependency("loop")(loop)
        with pytest.raises(DependencyCycleError):
            built.resolve_dependencies(loop)

    def test_no_plan_is_ever_cached(self) -> None:
        built = _linear()
        built.resolve_dependencies(_defaulted)
        built.resolve_dependencies(_defaulted)
        assert dict(built._plan_cache) == {}


class TestLinearProvides:
    """`provides` answers from the same walk the resolve makes."""

    def test_a_claimed_parameter_is_provided(self) -> None:
        built = _linear()
        param = inspect_parameter("value")
        context = make_resolution_context(url_kwargs={"value": "u"})
        assert built.provides(_plain, param, context) is True

    def test_an_unclaimed_parameter_is_not_provided(self) -> None:
        built = _linear()
        param = inspect_parameter("value")
        assert built.provides(_plain, param, make_resolution_context()) is False

    def test_a_parameter_outside_the_signature_is_not_provided(self) -> None:
        built = _linear()
        param = inspect_parameter("absent")
        context = make_resolution_context(url_kwargs={"absent": "u"})
        assert built.provides(_plain, param, context) is False

    def test_a_skipped_parameter_is_not_provided(self) -> None:
        built = _linear()
        param = inspect_parameter("kwargs")
        context = make_resolution_context(url_kwargs={"kwargs": "u"})
        assert built.provides(_skipping, param, context) is False


@dataclass(frozen=True, slots=True)
class _ParityCase:
    """One callable and one loose kwargs mapping both resolvers have to agree on.

    `build` installs whatever providers or dependencies the case needs, so the
    two resolvers under comparison are set up identically and independently.
    """

    id: str
    func: Callable[..., object]
    kwargs: dict[str, object] = field(default_factory=dict)
    build: Callable[[DependencyResolver], None] = lambda _r: None


def _bind_theme(built: DependencyResolver) -> None:
    built.dependency("theme")(lambda: "dark")


def _bind_nested(built: DependencyResolver) -> None:
    built.dependency("inner")(_inner_dep)


def _prepend_stub(built: DependencyResolver) -> None:
    built.prepend_provider(DeferringProvider("value", "STUB"))


def _markers(
    theme: str = Depends("theme"),
    built: str = Depends(_factory),
    fixed: int = Depends(42),
    value: int = Context("page_value"),
    named: str = Context(),
    shadowed: str = Context("request"),
    konst: str = Context(source="k"),
    made: str = Context(source=_factory),
    slug: DUrl[str] = "",
    ident: DUrl[int] = 0,
    keyed: DUrl["id", int] = 0,
    page: DQuery[int] = 1,
    tags: DQuery[list[str]] = (),
    request: HttpRequest | None = None,
    req: WSGIRequest | None = None,
) -> None:
    return None


def _forms(
    form,
    other: AForm | None = None,
    wrong: DForm[OtherForm] | None = None,
    cleaned_data=None,
) -> None:
    return None


_CONTEXT_DATA: dict[str, object] = {"page_value": 42, "named": "n", "value": "ctx"}

PARITY_CASES: tuple[_ParityCase, ...] = (
    _ParityCase("markers_bare", _markers, {}, _bind_theme),
    _ParityCase(
        "markers_full",
        _markers,
        {
            "request": _REQUEST,
            "slug": "hello",
            "ident": "5",
            "id": "7",
            "req": "raw",
            "_context_data": _CONTEXT_DATA,
        },
        _bind_theme,
    ),
    _ParityCase(
        "markers_form",
        _markers,
        {"request": _REQUEST, "form": _FORM, "cleaned_data": {"a": 1}},
        _bind_theme,
    ),
    _ParityCase("forms_bare", _forms),
    _ParityCase("forms_filled", _forms, {"form": _FORM, "cleaned_data": {"a": 1}}),
    _ParityCase(
        "forms_context", _forms, {"_context_data": {"form": _FORM, "other": "o"}}
    ),
    _ParityCase("nested_dependency", _outer, {"value": "u"}, _bind_nested),
    _ParityCase("custom_provider", _plain, {}, _prepend_stub),
    _ParityCase("custom_provider_outranked", _plain, {"value": "u"}, _prepend_stub),
    _ParityCase("unresolvable_hint", _unresolvable, {"request": _REQUEST}),
    _ParityCase("builtin", int, {"request": _REQUEST}),
)

_PARAMS: tuple[inspect.Parameter, ...] = (
    inspect_parameter("theme", default=Depends("theme")),
    inspect_parameter("built", default=Depends(_factory)),
    inspect_parameter("fixed", default=Depends(42)),
    inspect_parameter("value", int, default=Context("page_value")),
    inspect_parameter("named", str, default=Context()),
    inspect_parameter("shadowed", str, default=Context("request")),
    inspect_parameter("konst", str, default=Context(source="k")),
    inspect_parameter("made", str, default=Context(source=_factory)),
    inspect_parameter("slug", DUrl[str], default=""),
    inspect_parameter("ident", DUrl[int], default=0),
    inspect_parameter("keyed", DUrl["id", int], default=0),
    inspect_parameter("page", DQuery[int], default=1),
    inspect_parameter("tags", DQuery[list[str]], default=()),
    inspect_parameter("request", HttpRequest | None, default=None),
    inspect_parameter("req", WSGIRequest | None, default=None),
    inspect_parameter("form", default=None),
    inspect_parameter("other", AForm, default=None),
    inspect_parameter("wrong", DForm[OtherForm], default=None),
    inspect_parameter("cleaned_data", default=None),
    inspect_parameter("plain"),
    inspect_parameter("with_default", int, default=7),
)

# The parameters `provides` is asked about: every one the parity callables declare,
# one the signature skips, and one no signature carries.
_PROVIDES_NAMES: tuple[str, ...] = (*(p.name for p in _PARAMS), "kwargs", "absent")

_CONTEXTS: dict[str, dict[str, object]] = {
    "bare": {},
    "request_and_url": {
        "request": _REQUEST,
        "url_kwargs": {"slug": "hello", "id": "7", "plain": "p"},
    },
    "context_data": {"context_data": _CONTEXT_DATA},
    "form": {"request": _REQUEST, "form": _FORM, "cleaned_data": {"a": 1}},
}


def _agree(first: object, second: object) -> bool:
    """Compare two fills, taking identity for the values that define no equality."""
    return first is second or first == second


class TestPathParity:
    """The compiled plan and the linear walk answer the same thing.

    The golden matrix pins both paths against a literal. These rows compare the
    two against each other, where the shapes are messy enough that no literal
    would stay readable.
    """

    @pytest.mark.parametrize("case", PARITY_CASES, ids=attrgetter("id"))
    def test_both_paths_fill_the_same_mapping(self, case: _ParityCase) -> None:
        planned = DependencyResolver()
        linear = LinearDependencyResolver()
        case.build(planned)
        case.build(linear)
        compiled = planned.resolve_dependencies(case.func, **case.kwargs)
        replayed = planned.resolve_dependencies(case.func, **case.kwargs)
        walked = linear.resolve_dependencies(case.func, **case.kwargs)
        assert compiled == replayed
        assert set(compiled) == set(walked)
        for name, value in compiled.items():
            assert _agree(value, walked[name])

    @pytest.mark.parametrize("case", PARITY_CASES, ids=attrgetter("id"))
    def test_both_paths_agree_on_the_component_entry_point(
        self, case: _ParityCase
    ) -> None:
        planned = DependencyResolver()
        linear = LinearDependencyResolver()
        case.build(planned)
        case.build(linear)
        template_context = {"page_value": 42, "named": "n", "form": _FORM}
        compiled = planned.resolve_with_template_context(
            case.func, request=_REQUEST, template_context=template_context
        )
        walked = linear.resolve_with_template_context(
            case.func, request=_REQUEST, template_context=template_context
        )
        assert set(compiled) == set(walked)
        for name, value in compiled.items():
            assert _agree(value, walked[name])

    @pytest.mark.parametrize("name", _PROVIDES_NAMES)
    @pytest.mark.parametrize("context_id", sorted(_CONTEXTS), ids=sorted(_CONTEXTS))
    def test_both_paths_agree_on_provides(self, name, context_id) -> None:
        planned = _with_theme(DependencyResolver)
        linear = _with_theme(LinearDependencyResolver)
        # Both implementations match the entry by name alone, so the parameter
        # handed in carries nothing else.
        param = inspect_parameter(name)
        context = make_resolution_context(**_CONTEXTS[context_id])
        for func in (_markers, _forms, _skipping):
            assert planned.provides(func, param, context) == linear.provides(
                func, param, context
            )


class TestProviderContract:
    """Every provider owes the compiler the verdicts its `can_handle` makes.

    Parity between the two paths rests on this, so it is pinned per provider
    rather than inferred from the callables the matrices happen to cover.
    """

    @pytest.fixture()
    def providers(self) -> list[object]:
        built = _with_theme(DependencyResolver)
        built._sync_providers()
        return list(built._providers)

    @pytest.mark.parametrize("param", _PARAMS, ids=attrgetter("name"))
    def test_a_static_verdict_holds_in_every_context(self, param, providers) -> None:
        for provider in providers:
            verdict = provider.static_can_handle(param)
            if verdict is None:
                continue
            for shape in _CONTEXTS.values():
                context = make_resolution_context(**shape)
                assert provider.can_handle(param, context) is verdict

    @pytest.mark.parametrize("param", _PARAMS, ids=attrgetter("name"))
    def test_a_compiled_filler_answers_what_resolve_answers(
        self, param, providers
    ) -> None:
        for provider in providers:
            if provider.static_can_handle(param) is not True:
                continue
            filler = provider.compile_resolve(param)
            if filler is None:
                continue
            for shape in _CONTEXTS.values():
                context = make_resolution_context(**shape)
                assert _agree(filler(context), provider.resolve(param, context))


class TestKnownDivergence:
    """The one difference between the paths is what each of them memoises."""

    def test_the_plan_is_cached_and_the_walk_keeps_nothing(self) -> None:
        planned = DependencyResolver()
        linear = LinearDependencyResolver()
        assert planned.resolve_dependencies(_defaulted) == {"value": 7}
        assert linear.resolve_dependencies(_defaulted) == {"value": 7}
        assert _introspect_key(_defaulted) in planned._plan_cache
        assert dict(linear._plan_cache) == {}

    def test_an_unresolvable_hint_is_left_out_of_the_plan_cache(self) -> None:
        planned = DependencyResolver()
        linear = LinearDependencyResolver()
        assert planned.resolve_dependencies(
            _unresolvable
        ) == linear.resolve_dependencies(_unresolvable)
        assert _introspect_key(_unresolvable) not in planned._plan_cache

    def test_a_late_name_is_picked_up_by_both(self) -> None:
        planned = DependencyResolver()
        linear = LinearDependencyResolver()
        assert planned.resolve_dependencies(_deferred, request=_REQUEST) == {
            "value": None
        }
        assert linear.resolve_dependencies(_deferred, request=_REQUEST) == {
            "value": None
        }
        globals()["_LateHttpRequest"] = HttpRequest
        try:
            assert planned.resolve_dependencies(_deferred, request=_REQUEST) == {
                "value": _REQUEST
            }
            assert linear.resolve_dependencies(_deferred, request=_REQUEST) == {
                "value": _REQUEST
            }
        finally:
            del globals()["_LateHttpRequest"]
