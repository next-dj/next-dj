from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from next.urls import DUrl


_UUID_TEXT = "12345678-1234-5678-1234-567812345678"
_UUID_VALUE = UUID(_UUID_TEXT)


@dataclass(frozen=True, slots=True)
class CoerceUrlValueCase:
    """One row for ``TestCoerceUrlValue`` (raw value, type hint, expected value)."""

    id: str
    raw: object
    hint: object
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
    CoerceUrlValueCase("uuid_ok", _UUID_TEXT, UUID, _UUID_VALUE),
    CoerceUrlValueCase("uuid_bad", "not-a-uuid", UUID, "not-a-uuid"),
    CoerceUrlValueCase("decimal_ok", "3.14", Decimal, Decimal("3.14")),
    CoerceUrlValueCase("decimal_bad", "x", Decimal, "x"),
    CoerceUrlValueCase("date_ok", "2026-01-15", date, date(2026, 1, 15)),
    CoerceUrlValueCase("date_bad", "x", date, "x"),
    CoerceUrlValueCase(
        "datetime_ok",
        "2026-01-15T10:30:00+00:00",
        datetime,
        datetime(2026, 1, 15, 10, 30, tzinfo=UTC),
    ),
    CoerceUrlValueCase("datetime_bad", "x", datetime, "x"),
    CoerceUrlValueCase("isinstance_uuid", _UUID_VALUE, UUID, _UUID_VALUE),
    CoerceUrlValueCase("isinstance_int", 42, int, 42),
    CoerceUrlValueCase("non_type_hint", "anything", "not-a-type", "anything"),
    CoerceUrlValueCase("str_from_int", 42, str, "42"),
    CoerceUrlValueCase("unsupported_type", "hello", bytes, "hello"),
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
    UrlKwargsResolveCase(
        "uuid_preserved", "id", UUID, {"id": _UUID_VALUE}, _UUID_VALUE
    ),
    UrlKwargsResolveCase("uuid_from_text", "id", UUID, {"id": _UUID_TEXT}, _UUID_VALUE),
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
    UrlByAnnotationResolveCase(
        "two_arg_coerce_int", "note_id", DUrl["id", int], {"id": "42"}, 42
    ),
    UrlByAnnotationResolveCase(
        "key_only_no_coercion", "note_id", DUrl["id"], {"id": "7"}, "7"
    ),
    UrlByAnnotationResolveCase(
        "two_arg_reads_named_key", "note_id", DUrl["id", int], {"note_id": "9"}, None
    ),
    UrlByAnnotationResolveCase(
        "coerce_uuid_preserved",
        "pk",
        DUrl[UUID],
        {"pk": _UUID_VALUE},
        _UUID_VALUE,
    ),
    UrlByAnnotationResolveCase(
        "coerce_uuid_from_text",
        "pk",
        DUrl[UUID],
        {"pk": _UUID_TEXT},
        _UUID_VALUE,
    ),
)


# Sentinels marking a hook return that the matrix interprets specially. RAISE
# means the hook body raises PermissionDenied, BAD_TYPE means it returns an
# unsupported type so the normaliser must raise TypeError.
PERMISSION_HOOK_RAISE = object()
PERMISSION_HOOK_BAD_TYPE = object()


@dataclass(frozen=True, slots=True)
class PermissionHookCase:
    """One row for the dynamic permission-hook return-contract matrix.

    ``hook_return`` is the value a hook returns, or one of the
    ``PERMISSION_HOOK_*`` sentinels for the raise and bad-type branches.
    ``expected_status`` is the HTTP status of a full dispatch, or None when
    the hook is expected to raise (PermissionDenied at the view boundary
    surfaces as 403 through the test client, TypeError propagates raw).
    """

    id: str
    hook_return: object
    expected_status: int | None
    expected_redirect: str | None = None
    raises_permission_denied: bool = False
    raises_type_error: bool = False


PERMISSION_OUTCOME_CASES: tuple[PermissionHookCase, ...] = (
    PermissionHookCase("none_allows", None, 302, expected_redirect="/"),
    PermissionHookCase("true_allows", True, 302, expected_redirect="/"),
    PermissionHookCase(
        "false_denies",
        False,
        None,
        raises_permission_denied=True,
    ),
    PermissionHookCase(
        "redirect_short_circuits",
        "redirect",
        302,
        expected_redirect="/paywall/",
    ),
    PermissionHookCase("response_403_verbatim", "response_403", 403),
    PermissionHookCase(
        "raised_propagates",
        PERMISSION_HOOK_RAISE,
        None,
        raises_permission_denied=True,
    ),
    PermissionHookCase(
        "bad_type_raises_type_error",
        PERMISSION_HOOK_BAD_TYPE,
        None,
        raises_type_error=True,
    ),
)
