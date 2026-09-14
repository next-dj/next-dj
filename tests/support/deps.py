import inspect
from typing import NamedTuple

from django import forms

from next.deps import ResolutionContext
from next.deps.plan import InjectionPlan, ParameterFiller, ParameterPlan
from next.deps.providers import ParameterProvider


class PlanEntry(NamedTuple):
    """One compiled plan entry read by field name instead of by position.

    Building it from the raw tuple fails loudly the day the plan grows a
    field, where indexing would quietly shift every assertion one slot over.
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

    Records every parameter and context asked about, so a test can prove a candidate
    reached the replay path.
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
