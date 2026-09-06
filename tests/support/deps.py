import inspect

from django import forms

from next.deps import ResolutionContext


class AForm(forms.Form):
    """A form class the `DForm[...]` and annotation matches are pinned against."""


class OtherForm(forms.Form):
    """A second form class, so a mismatch has something to mismatch with."""


class DeferringProvider:
    """Protocol provider that defers every static verdict and claims one name.

    It records the parameters and the contexts it is asked about, so a test can
    prove the resolver puts a candidate on the replay path. A name of None
    claims nothing and leaves the recording as the only thing it does.
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
