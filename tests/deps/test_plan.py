import inspect
from operator import attrgetter
from typing import Annotated

import pytest
from django import forms
from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpRequest
from django.test import RequestFactory

from next.deps import (
    DependencyResolver,
    Depends,
    ResolutionContext,
    UnknownDependencyError,
)
from next.deps.markers import DependsProvider
from next.deps.plan import EMPTY_PLAN, compile_plan
from next.deps.resolver import _introspect_key
from next.forms import DForm
from next.forms.markers import CleanedDataProvider, FormProvider
from next.pages.context import Context, ContextByDefaultProvider, ContextByNameProvider
from next.testing import make_resolution_context
from next.urls import (
    DQuery,
    DUrl,
    HttpRequestProvider,
    QueryParamProvider,
    UrlByAnnotationProvider,
    UrlKwargsProvider,
)
from tests.support import (
    AForm,
    DeferringProvider,
    OtherForm,
    PlanCase,
    inspect_parameter,
    typing_optional,
)


_BOOM = RuntimeError("boom")


def _factory(request: HttpRequest | None = None, page_value: int = Context()) -> str:
    return f"{request is not None}:{page_value}"


def _markers(
    theme: str = Depends("theme"),
    value: int = Context("page_value"),
    named: str = Context(),
    slug: DUrl[str] = "",
    ident: DUrl[int] = 0,
    page: DQuery[int] = 1,
    request: HttpRequest | None = None,
    req: HttpRequest | None = None,
    built: str = Depends(_factory),
) -> None:
    return None


def _uncovered(plain, with_default: int = 7, ident: int = 0) -> None:
    return None


def _form_params(
    form,
    other: AForm | None = None,
    wrong: DForm[OtherForm] | None = None,
    bare: DForm | None = None,
    named: DForm["OtherForm"] | None = None,
    cleaned_data=None,
) -> None:
    return None


def _theme_request(theme: HttpRequest | None = None) -> None:
    return None


def _string_request(request: "HttpRequest | None" = None) -> None:
    return None


def _broken_hint(request: HttpRequest | None = None) -> None:
    return None


_broken_hint.__annotations__["request"] = "NoSuchTypeAnywhere"


class _Holder:
    def method(self, value: int = Context("page_value")) -> None:
        return None


def _two(theme: str = Depends("theme"), value: int = Context("page_value")) -> None:
    return None


def _later(value: str = Depends("later")) -> None:
    return None


def _plain(plain) -> None:
    return None


def _inner(plain) -> str:
    return "inner"


def _outer(dep: str = Depends("dep"), plain=None) -> None:
    return None


def _string_url(user_id: "int") -> None:
    return None


class _Raising:
    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        raise _BOOM

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        return None

    def static_can_handle(self, param: inspect.Parameter) -> bool | None:
        return None


class _BadVerdict:
    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        return False

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        return None

    def static_can_handle(self, param: inspect.Parameter) -> object:
        return 1


def _with_theme() -> DependencyResolver:
    planned = DependencyResolver()
    planned.dependency("theme")(lambda: "dark")
    return planned


# One request and one form shared by the kwargs and the expected mapping of a
# case, because neither type defines equality and the plan must hand back the
# very object it was given.
_REQUEST = RequestFactory().get("/?page=3&ident=9")
_FORM = AForm()

_FULL: dict[str, object] = {
    "request": _REQUEST,
    "slug": "hello",
    "ident": "5",
    "plain": "p",
    "req": "raw",
    "_context_data": {"page_value": 42, "named": "n"},
}
_NO_REQUEST: dict[str, object] = {
    "slug": "hello",
    "ident": "5",
    "plain": "p",
    "req": "raw",
    "_context_data": {"page_value": 42, "named": "n"},
}
_WITH_FORM: dict[str, object] = {
    "request": _REQUEST,
    "form": _FORM,
    "cleaned_data": {"a": 1},
    "_context_data": {"page_value": 1},
}
_THEME_IN_CONTEXT: dict[str, object] = {
    "request": _REQUEST,
    "_context_data": {"theme": "ctx"},
}
_BARE: dict[str, object] = {}

_FORM_PARAMS_UNFILLED: dict[str, object] = {
    "form": None,
    "other": None,
    "wrong": None,
    "bare": None,
    "named": None,
    "cleaned_data": None,
}

# Every callable against every context, so a context that must leave a parameter
# alone is pinned as firmly as one that decides it. The two callables with
# nothing to read take one row, because they have no pairing to vary.
PLAN_CASES: tuple[PlanCase, ...] = (
    PlanCase(
        "markers_full",
        _markers,
        _FULL,
        {
            "theme": "dark",
            "value": 42,
            "named": "n",
            "slug": "hello",
            "ident": 5,
            "page": 3,
            "request": _REQUEST,
            "req": _REQUEST,
            "built": "True:42",
        },
    ),
    PlanCase(
        "markers_no_request",
        _markers,
        _NO_REQUEST,
        {
            "theme": "dark",
            "value": 42,
            "named": "n",
            "slug": "hello",
            "ident": 5,
            "page": 1,
            "request": None,
            "req": "raw",
            "built": "False:42",
        },
    ),
    PlanCase(
        "markers_form",
        _markers,
        _WITH_FORM,
        {
            "theme": "dark",
            "value": 1,
            "named": None,
            "slug": None,
            "ident": None,
            "page": 3,
            "request": _REQUEST,
            "req": _REQUEST,
            "built": "True:1",
        },
    ),
    PlanCase(
        "markers_theme_in_context",
        _markers,
        _THEME_IN_CONTEXT,
        {
            "theme": "dark",
            "value": None,
            "named": None,
            "slug": None,
            "ident": None,
            "page": 3,
            "request": _REQUEST,
            "req": _REQUEST,
            "built": "True:None",
        },
    ),
    PlanCase(
        "markers_bare",
        _markers,
        _BARE,
        {
            "theme": "dark",
            "value": None,
            "named": None,
            "slug": None,
            "ident": None,
            "page": 1,
            "request": None,
            "req": None,
            "built": "False:None",
        },
    ),
    PlanCase(
        "uncovered_full",
        _uncovered,
        _FULL,
        {"plain": "p", "with_default": 7, "ident": 5},
    ),
    PlanCase(
        "uncovered_no_request",
        _uncovered,
        _NO_REQUEST,
        {"plain": "p", "with_default": 7, "ident": 5},
    ),
    PlanCase(
        "uncovered_form",
        _uncovered,
        _WITH_FORM,
        {"plain": None, "with_default": 7, "ident": 0},
    ),
    PlanCase(
        "uncovered_theme_in_context",
        _uncovered,
        _THEME_IN_CONTEXT,
        {"plain": None, "with_default": 7, "ident": 0},
    ),
    PlanCase(
        "uncovered_bare",
        _uncovered,
        _BARE,
        {"plain": None, "with_default": 7, "ident": 0},
    ),
    PlanCase(
        "form_params_full", _form_params, _FULL, {**_FORM_PARAMS_UNFILLED, "named": "n"}
    ),
    PlanCase(
        "form_params_no_request",
        _form_params,
        _NO_REQUEST,
        {**_FORM_PARAMS_UNFILLED, "named": "n"},
    ),
    PlanCase(
        "form_params_form",
        _form_params,
        _WITH_FORM,
        {**_FORM_PARAMS_UNFILLED, "form": _FORM, "cleaned_data": {"a": 1}},
    ),
    PlanCase(
        "form_params_theme_in_context",
        _form_params,
        _THEME_IN_CONTEXT,
        _FORM_PARAMS_UNFILLED,
    ),
    PlanCase("form_params_bare", _form_params, _BARE, _FORM_PARAMS_UNFILLED),
    PlanCase("string_request_full", _string_request, _FULL, {"request": _REQUEST}),
    PlanCase(
        "string_request_no_request", _string_request, _NO_REQUEST, {"request": None}
    ),
    PlanCase("string_request_form", _string_request, _WITH_FORM, {"request": _REQUEST}),
    PlanCase(
        "string_request_theme_in_context",
        _string_request,
        _THEME_IN_CONTEXT,
        {"request": _REQUEST},
    ),
    PlanCase("string_request_bare", _string_request, _BARE, {"request": None}),
    PlanCase("broken_hint", _broken_hint, _FULL, {"request": None}),
    PlanCase("method_full", _Holder().method, _FULL, {"value": 42}),
    PlanCase("method_no_request", _Holder().method, _NO_REQUEST, {"value": 42}),
    PlanCase("method_form", _Holder().method, _WITH_FORM, {"value": 1}),
    PlanCase(
        "method_theme_in_context", _Holder().method, _THEME_IN_CONTEXT, {"value": None}
    ),
    PlanCase("method_bare", _Holder().method, _BARE, {"value": None}),
    PlanCase("builtin", int, _FULL, {}),
)


class TestGoldenMatrix:
    """A compile and a replay both yield the literal pinned for the pair."""

    @pytest.mark.parametrize("case", PLAN_CASES, ids=attrgetter("id"))
    def test_plan_matches_the_pinned_literal(self, case: PlanCase) -> None:
        planned = _with_theme()
        # The first call compiles the plan and the second replays the cached one.
        compiled = planned.resolve_dependencies(case.func, **case.kwargs)
        replayed = planned.resolve_dependencies(case.func, **case.kwargs)
        assert compiled == case.expected
        assert replayed == case.expected
        # Equality on a request or a form is identity, so the pinned object is
        # the one the plan handed back and not a lookalike.
        for name, value in case.expected.items():
            if isinstance(value, HttpRequest | forms.Form):
                assert compiled[name] is value

    def test_request_param_without_request_falls_back_to_url_kwargs(self) -> None:
        planned = _with_theme()
        assert planned.resolve_dependencies(_markers, req="raw")["req"] == "raw"

    def test_non_introspectable_callable_yields_empty(self) -> None:
        planned = DependencyResolver()
        assert planned.resolve_dependencies(int) == {}
        assert planned._plan_cache[_introspect_key(int)][1] is EMPTY_PLAN


class TestPlanShape:
    """Compiled entries hold the short-list the signature leaves open."""

    def _plan(self, planned: DependencyResolver, func) -> dict[str, tuple]:
        planned.resolve_dependencies(func)
        return {
            name: (candidates, terminal)
            for name, candidates, terminal, _fallback, _param in planned._plan_cache[
                _introspect_key(func)
            ][1]
        }

    def test_marker_parameters_end_in_a_terminal(self) -> None:
        plan = self._plan(_with_theme(), _two)
        assert plan["theme"][0] == ()
        assert isinstance(plan["theme"][1], DependsProvider)
        assert plan["value"][0] == ()
        assert isinstance(plan["value"][1], ContextByDefaultProvider)

    def test_marker_parameters_call_no_can_handle(self, monkeypatch) -> None:
        planned = _with_theme()
        planned._sync_providers()
        calls: list[str] = []
        for provider in planned._providers:
            name = type(provider).__name__
            original = provider.can_handle

            def counting(param, context, name=name, original=original):
                calls.append(name)
                return original(param, context)

            monkeypatch.setattr(provider, "can_handle", counting)
        result = planned.resolve_dependencies(_two, _context_data={"page_value": 42})
        assert result == {"theme": "dark", "value": 42}
        assert calls == []

    def test_request_annotation_keeps_the_name_provider_ahead(self) -> None:
        plan = self._plan(DependencyResolver(), _theme_request)
        candidates, terminal = plan["theme"]
        assert [type(p) for p in candidates] == [
            ContextByNameProvider,
            HttpRequestProvider,
            UrlKwargsProvider,
        ]
        assert terminal is None

    def test_fallback_is_the_default_object_itself(self) -> None:
        planned = DependencyResolver()
        planned.resolve_dependencies(_uncovered)
        entries = {
            name: fallback
            for name, _c, _t, fallback, _p in planned._plan_cache[
                _introspect_key(_uncovered)
            ][1]
        }
        assert entries["plain"] is None
        assert entries["with_default"] == 7
        default = inspect.signature(_uncovered).parameters["with_default"].default
        assert entries["with_default"] is default

    def test_skipped_parameters_are_absent(self) -> None:
        def fn(self, *args, value: int = 1, **kwargs) -> None:
            return None

        plan = self._plan(DependencyResolver(), fn)
        assert list(plan) == ["value"]

    def test_custom_provider_deferring_its_verdict_is_a_candidate_everywhere(
        self,
    ) -> None:
        planned = DependencyResolver()
        planned.dependency("flag")(lambda: "dep")
        first = DeferringProvider()
        last = DeferringProvider()
        planned.prepend_provider(first)
        planned.add_provider(last)
        plan = self._plan(planned, _plain)
        assert [type(p) for p in plan["plain"][0]] == [
            DeferringProvider,
            ContextByNameProvider,
            UrlKwargsProvider,
            DeferringProvider,
        ]
        assert plan["plain"][0][0] is first
        assert plan["plain"][0][-1] is last

        def by_marker(flag: str = Depends("flag")) -> None:
            return None

        marker_plan = self._plan(planned, by_marker)
        candidates, terminal = marker_plan["flag"]
        assert candidates == (first,)
        assert isinstance(terminal, DependsProvider)
        assert planned.resolve_dependencies(by_marker) == {"flag": "STUB"}

    def test_compile_plan_stops_at_the_terminal(self) -> None:
        depends = DependsProvider(resolver=DependencyResolver())
        tail = DeferringProvider()
        sig = inspect.signature(_two)
        plan = compile_plan(sig, {}, [depends, tail], lambda _p: False)
        assert type(plan[0]) is tuple
        assert plan[0][0] == "theme"
        assert plan[0][1] == ()
        assert plan[0][2] is depends
        assert plan[1][1] == (tail,)
        assert plan[1][2] is None

    def test_entry_carries_the_resolved_annotation(self) -> None:
        planned = DependencyResolver()
        planned.resolve_dependencies(_string_url)
        (entry,) = planned._plan_cache[_introspect_key(_string_url)][1]
        assert entry[4].annotation is int
        assert entry[4].name == "user_id"

    def test_string_annotation_coerces_through_the_plan(self) -> None:
        planned = DependencyResolver()
        assert planned.resolve_dependencies(_string_url, user_id="42") == {
            "user_id": 42
        }

    def test_raw_parameter_is_kept_when_hints_add_nothing(self) -> None:
        sig = inspect.signature(_uncovered)
        plan = compile_plan(sig, {}, [], lambda _p: False)
        assert plan[0][4] is sig.parameters["plain"]

    def test_verdict_outside_the_contract_raises_at_compile_time(self) -> None:
        planned = DependencyResolver()
        planned.add_provider(_BadVerdict())
        with pytest.raises(
            TypeError, match=r"_BadVerdict.static_can_handle returned 1 for parameter"
        ):
            planned.resolve_dependencies(_plain)


class TestInvalidation:
    """Every provider-list mutation bumps the version and recompiles the plan."""

    @pytest.fixture()
    def compiles(self, monkeypatch) -> list[int]:
        calls: list[int] = []
        original = DependencyResolver._compile_plan

        def counting(self, func):
            calls.append(1)
            return original(self, func)

        monkeypatch.setattr(DependencyResolver, "_compile_plan", counting)
        return calls

    def test_fresh_resolver_compiles_once_across_two_resolves(self, compiles) -> None:
        planned = DependencyResolver()
        assert planned.resolve_dependencies(_plain) == {"plain": None}
        assert planned.resolve_dependencies(_plain) == {"plain": None}
        assert len(compiles) == 1
        entry = planned._plan_cache[_introspect_key(_plain)]
        assert entry[0] == planned._providers_version == 1

    def test_mutations_bump_and_recompile(self, compiles) -> None:
        planned = DependencyResolver()
        assert planned._providers_version == 0
        planned.resolve_dependencies(_plain)
        assert planned._providers_version == 1
        assert len(compiles) == 1
        planned.resolve_dependencies(_plain)
        assert len(compiles) == 1

        stub = DeferringProvider("plain")
        planned.add_provider(stub)
        assert planned._providers_version == 2
        assert planned.resolve_dependencies(_plain) == {"plain": "STUB"}
        assert len(compiles) == 2

        planned.remove_provider(stub)
        assert planned._providers_version == 3
        assert planned.resolve_dependencies(_plain) == {"plain": None}
        assert len(compiles) == 3

        planned.prepend_provider(stub)
        assert planned._providers_version == 4
        assert planned.resolve_dependencies(_plain) == {"plain": "STUB"}
        assert len(compiles) == 4

    def test_removing_an_absent_provider_keeps_the_version(self, compiles) -> None:
        planned = DependencyResolver()
        planned.resolve_dependencies(_plain)
        version = planned._providers_version
        planned.remove_provider(DeferringProvider())
        assert planned._providers_version == version
        planned.resolve_dependencies(_plain)
        assert len(compiles) == 1

    def test_explicit_providers_load_without_a_bump(self) -> None:
        planned = DependencyResolver(DeferringProvider("plain"))
        planned.resolve_dependencies(_plain)
        assert planned._providers_version == 0

    def test_register_dependency_reuses_the_plan(self) -> None:
        planned = DependencyResolver()
        with pytest.raises(UnknownDependencyError):
            planned.resolve_dependencies(_later)
        version = planned._providers_version
        entry = planned._plan_cache[_introspect_key(_later)]
        planned.register_dependency("later", lambda: "bound")
        assert planned.resolve_dependencies(_later) == {"value": "bound"}
        assert planned._providers_version == version
        assert planned._plan_cache[_introspect_key(_later)] is entry


class TestReplay:
    """Every replay walks the candidates of the plan and owns what it raises."""

    def test_candidates_are_asked_about_each_planned_parameter(self) -> None:
        planned = DependencyResolver()
        stub = DeferringProvider("plain")
        planned.prepend_provider(stub)
        planned.resolve(_plain, make_resolution_context())
        assert stub.seen == ["plain"]

    def test_a_nested_resolve_walks_the_inner_plan_too(self) -> None:
        planned = DependencyResolver()
        planned.dependency("dep")(_inner)
        stub = DeferringProvider("nothing")
        planned.prepend_provider(stub)
        context = make_resolution_context()
        assert planned.resolve(_outer, context) == {"dep": "inner", "plain": None}
        # `plain` repeats because the nested `_inner` is asked before `_outer`.
        assert stub.seen == ["dep", "plain", "plain"]

    def test_a_raising_provider_propagates(self) -> None:
        planned = DependencyResolver()
        planned.prepend_provider(_Raising())
        with pytest.raises(RuntimeError, match="boom"):
            planned.resolve(_plain, make_resolution_context())

    def test_an_empty_plan_returns_without_a_replay(self) -> None:
        planned = DependencyResolver()
        assert planned.resolve(lambda: None, make_resolution_context()) == {}

    def test_unknown_dependency_names_the_owning_callable(self) -> None:
        planned = DependencyResolver()
        with pytest.raises(UnknownDependencyError, match=_later.__name__) as exc_info:
            planned.resolve(_later, make_resolution_context())
        assert exc_info.value.func is _later

    def test_unknown_dependency_names_the_innermost_callable(self) -> None:
        planned = DependencyResolver()
        planned.dependency("dep")(_later)
        with pytest.raises(UnknownDependencyError) as exc_info:
            planned.resolve(_outer, make_resolution_context())
        assert exc_info.value.func is _later

    def test_resolve_accepts_a_prebuilt_context(self) -> None:
        planned = DependencyResolver()
        context = make_resolution_context(url_kwargs={"plain": "u"})
        assert planned.resolve(_plain, context) == {"plain": "u"}


class TestStaticCanHandle:
    """Each hook mirrors the context-free branches of its `can_handle`."""

    def test_default_hook_any_parameter_defers(self) -> None:
        param = inspect_parameter("anything")
        assert ContextByNameProvider().static_can_handle(param) is None
        assert UrlKwargsProvider().static_can_handle(param) is None

    @pytest.mark.parametrize(
        ("param", "expected"),
        [
            (inspect_parameter("form"), None),
            (inspect_parameter("other"), False),
            (inspect_parameter("other", AForm), None),
            (inspect_parameter("other", DForm[AForm]), None),
            (inspect_parameter("other", DForm["AForm"]), False),
            (inspect_parameter("other", DForm), False),
            (inspect_parameter("other", AForm | None), False),
        ],
        ids=[
            "form_name",
            "bare_other",
            "form_class",
            "dform_of_class",
            "dform_of_string",
            "bare_dform",
            "optional_form_class",
        ],
    )
    def test_form_provider_shape_defers_or_rules_out(self, param, expected) -> None:
        assert FormProvider().static_can_handle(param) is expected

    def test_cleaned_data_provider_name_defers_and_other_rules_out(self) -> None:
        provider = CleanedDataProvider()
        assert provider.static_can_handle(inspect_parameter("cleaned_data")) is None
        assert provider.static_can_handle(inspect_parameter("other")) is False

    @pytest.mark.parametrize(
        ("annotation", "expected"),
        [
            (HttpRequest, None),
            (HttpRequest | None, None),
            (typing_optional(HttpRequest), None),
            (WSGIRequest, None),
            (WSGIRequest | None, None),
            (HttpRequest | int, False),
            ("HttpRequest", False),
            (int, False),
            (list[int], False),
            (inspect.Parameter.empty, False),
        ],
        ids=[
            "bare",
            "pep604_optional",
            "typing_optional",
            "subclass",
            "subclass_optional",
            "union_with_other_type",
            "string",
            "int",
            "generic_alias",
            "empty",
        ],
    )
    def test_http_request_provider_annotation_defers_or_rules_out(
        self, annotation, expected
    ) -> None:
        param = inspect_parameter("request", annotation)
        assert HttpRequestProvider().static_can_handle(param) is expected

    def test_url_by_annotation_provider_durl_claims_and_plain_rules_out(self) -> None:
        provider = UrlByAnnotationProvider()
        assert provider.static_can_handle(inspect_parameter("s", DUrl[str])) is True
        assert provider.static_can_handle(inspect_parameter("s", str)) is False

    def test_query_param_provider_dquery_defers_and_plain_rules_out(self) -> None:
        provider = QueryParamProvider()
        assert provider.static_can_handle(inspect_parameter("q", DQuery[int])) is None
        assert provider.static_can_handle(inspect_parameter("q", int)) is False

    def test_marker_default_providers_marker_claims_and_plain_rules_out(self) -> None:
        inner = DependencyResolver()
        depends = DependsProvider(resolver=inner)
        context = ContextByDefaultProvider(resolver=inner)
        assert depends.static_can_handle(inspect_parameter("d", default=Depends()))
        assert depends.static_can_handle(inspect_parameter("d")) is False
        assert context.static_can_handle(inspect_parameter("c", default=Context()))
        assert context.static_can_handle(inspect_parameter("c")) is False

    def test_marker_default_providers_answer_the_same_with_a_context(self) -> None:
        inner = DependencyResolver()
        depends = DependsProvider(resolver=inner)
        context = ContextByDefaultProvider(resolver=inner)
        resolution = make_resolution_context(context_data={"c": "ctx"})
        assert depends.can_handle(inspect_parameter("d", default=Depends()), resolution)
        assert depends.can_handle(inspect_parameter("d"), resolution) is False
        assert context.can_handle(inspect_parameter("c", default=Context()), resolution)
        assert context.can_handle(inspect_parameter("c"), resolution) is False


_TENANT = "tenant"


def _annotated(
    request: Annotated[HttpRequest, _TENANT] = None,
    slug: Annotated[DUrl[int], _TENANT] = 0,
    page: Annotated[DQuery[int], _TENANT] = 1,
    form: Annotated[AForm, _TENANT] = None,
    ident: Annotated[int, _TENANT] = 0,
) -> None:
    return None


def _tagged(value: Annotated[str, _TENANT] = "unset") -> str:
    return value


class _MetadataProvider:
    """Provider that claims a parameter by the metadata of its `Annotated` hint."""

    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        return self.static_can_handle(param)

    def static_can_handle(self, param: inspect.Parameter) -> bool:
        return _TENANT in getattr(param.annotation, "__metadata__", ())

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        return "acme"


class TestAnnotatedHints:
    """The plan keeps the extras of a hint, and the built-ins look past them."""

    def test_metadata_reaches_a_custom_provider(self) -> None:
        planned = DependencyResolver()
        planned.prepend_provider(_MetadataProvider())
        assert planned.resolve_dependencies(_tagged) == {"value": "acme"}

    def test_the_builtin_providers_see_through_the_wrapper(self) -> None:
        planned = DependencyResolver()
        request = RequestFactory().get("/?page=7")
        form = AForm()
        assert planned.resolve_dependencies(
            _annotated, request=request, form=form, slug="12", ident="34"
        ) == {"request": request, "slug": 12, "page": 7, "form": form, "ident": 34}
