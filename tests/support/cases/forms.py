from __future__ import annotations

from dataclasses import dataclass


# Sentinels the matrix reads specially: RAISE makes the hook raise
# PermissionDenied, BAD_TYPE makes it return an unsupported type.
PERMISSION_HOOK_RAISE = object()
PERMISSION_HOOK_BAD_TYPE = object()


@dataclass(frozen=True, slots=True)
class PermissionHookCase:
    """One row for the dynamic permission-hook return-contract matrix."""

    id: str
    hook_return: object
    expected_status: int | None
    expected_redirect: str | None = None
    raises_permission_denied: bool = False
    raises_type_error: bool = False


PERMISSION_OUTCOME_CASES: tuple[PermissionHookCase, ...] = (
    PermissionHookCase("none_allows", None, 302, expected_redirect="/"),
    PermissionHookCase("true_allows", True, 302, expected_redirect="/"),
    PermissionHookCase("false_denies", False, None, raises_permission_denied=True),
    PermissionHookCase(
        "redirect_short_circuits", "redirect", 302, expected_redirect="/paywall/"
    ),
    PermissionHookCase("response_403_verbatim", "response_403", 403),
    PermissionHookCase(
        "raised_propagates", PERMISSION_HOOK_RAISE, None, raises_permission_denied=True
    ),
    PermissionHookCase(
        "bad_type_raises_type_error",
        PERMISSION_HOOK_BAD_TYPE,
        None,
        raises_type_error=True,
    ),
)
