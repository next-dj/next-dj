from __future__ import annotations

import inspect
from dataclasses import dataclass

from next.urls import DUrl


@dataclass(frozen=True, slots=True)
class CoerceUrlValueCase:
    """One row for ``TestCoerceUrlValue`` (raw string, type hint, expected value)."""

    id: str
    raw: str
    hint: type
    expected: object


COERCE_URL_VALUE_CASES: tuple[CoerceUrlValueCase, ...] = (
    CoerceUrlValueCase("int_ok", "42", int, 42),
    CoerceUrlValueCase("int_bad", "x", int, "x"),
    CoerceUrlValueCase("bool_true", "true", bool, True),
    CoerceUrlValueCase("bool_one", "1", bool, True),
    CoerceUrlValueCase("bool_yes", "yes", bool, True),
    CoerceUrlValueCase("bool_zero", "0", bool, False),
    CoerceUrlValueCase("bool_false", "false", bool, False),
    CoerceUrlValueCase("float_ok", "3.14", float, 3.14),
    CoerceUrlValueCase("float_bad", "x", float, "x"),
    CoerceUrlValueCase("str_pass", "hello", str, "hello"),
)


@dataclass(frozen=True, slots=True)
class UrlKwargsResolveCase:
    """One row for ``UrlKwargsProvider.resolve`` table tests."""

    id: str
    name: str
    annotation: object
    url_kwargs: dict[str, object]
    expected: object


URL_KWARGS_RESOLVE_CASES: tuple[UrlKwargsResolveCase, ...] = (
    UrlKwargsResolveCase("int_match", "id", int, {"id": 42}, 42),
    UrlKwargsResolveCase("str_to_int", "id", int, {"id": "99"}, 99),
    UrlKwargsResolveCase(
        "no_annotation",
        "slug",
        inspect.Parameter.empty,
        {"slug": "hello"},
        "hello",
    ),
    UrlKwargsResolveCase(
        "int_conv_fail", "id", int, {"id": "not-a-number"}, "not-a-number"
    ),
    UrlKwargsResolveCase(
        "str_annot", "slug", str, {"slug": "hello-world"}, "hello-world"
    ),
    UrlKwargsResolveCase("missing_key", "missing", str, {"other": "value"}, None),
)


@dataclass(frozen=True, slots=True)
class UrlByAnnotationResolveCase:
    """One row for ``UrlByAnnotationProvider.resolve`` table tests."""

    id: str
    name: str
    annotation: object
    url_kwargs: dict[str, object]
    expected: object | None


URL_BY_ANNOTATION_RESOLVE_CASES: tuple[UrlByAnnotationResolveCase, ...] = (
    UrlByAnnotationResolveCase("coerce_int", "pk", DUrl[int], {"pk": "123"}, 123),
    UrlByAnnotationResolveCase(
        "str_slug", "slug", DUrl[str], {"slug": "hello"}, "hello"
    ),
    UrlByAnnotationResolveCase("missing_key", "missing", DUrl[str], {}, None),
)


@dataclass(frozen=True, slots=True)
class ComponentTagCase:
    """One row for parametrized ``{% component %}`` template tag tests."""

    id: str
    template: str
    match: str


@dataclass(frozen=True, slots=True)
class FormDispatchCase:
    """One row for parametrized form dispatch status-code tests."""

    id: str
    form_data: dict[str, object]
    expected_status: int
