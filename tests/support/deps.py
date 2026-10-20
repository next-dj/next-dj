import inspect
from typing import NamedTuple

import pytest
from django import forms
from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.db.models import QuerySet

from next.deps import ResolutionContext
from next.deps.plan import InjectionPlan, ParameterFiller, ParameterPlan
from next.deps.providers import ParameterProvider


class PlanEntry(NamedTuple):
    """One compiled plan entry read by field name instead of by position.

    Building it from the raw tuple fails loudly when the plan grows a field.
    """

    name: str
    candidates: tuple[ParameterProvider, ...]
    fallback: object
    param: inspect.Parameter
    filler: ParameterFiller | None


def plan_entry(entry: ParameterPlan) -> PlanEntry:
    """Return the named view of one compiled plan entry."""
    return PlanEntry(*entry)


def plan_entries(plan: InjectionPlan) -> tuple[PlanEntry, ...]:
    """Return every entry of `plan` in compile order, read by field name."""
    return tuple(plan_entry(entry) for entry in plan)


def plan_by_name(plan: InjectionPlan) -> dict[str, PlanEntry]:
    """Index the entries of `plan` by the parameter name each one fills."""
    return {entry.name: entry for entry in plan_entries(plan)}


class AForm(forms.Form):
    """A form class the `DForm[...]` and annotation matches are pinned against."""


class OtherForm(forms.Form):
    """A second form class, so a mismatch has something to mismatch with."""


class DeferringProvider:
    """Protocol provider that defers every static verdict and claims one name.

    Records every parameter and context it is asked about, proving the replay ran.
    """

    def __init__(self, name: str | None = "flag", value: object = "STUB") -> None:
        """Claim `name` and answer `value` for it."""
        self.name = name
        self.value = value
        self.seen: list[str] = []
        self.contexts: list[ResolutionContext] = []

    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        self.seen.append(param.name)
        self.contexts.append(context)
        return param.name == self.name

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        return self.value

    def static_can_handle(self, param: inspect.Parameter) -> bool | None:
        return None


def _visible_content_types(scope: object) -> QuerySet[ContentType]:
    return ContentType.objects.filter(app_label=scope)


def _visible_permissions(scope: object) -> QuerySet[Permission]:
    return Permission.objects.filter(content_type__app_label=scope)


def _visible_groups(scope: object) -> QuerySet[Group]:
    if isinstance(scope, User):
        return scope.groups.all()
    return Group.objects.filter(name__startswith=f"{scope}:")


def install_visibility_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the auth and contenttypes default managers a `visible_to` hook.

    A group is visible to a member user or a name prefix, the rest to an app label.
    """
    for model, hook in (
        (ContentType, _visible_content_types),
        (Permission, _visible_permissions),
        (Group, _visible_groups),
    ):
        monkeypatch.setattr(model._default_manager, "visible_to", hook, raising=False)
