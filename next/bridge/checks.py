"""The check of the installed asgiref, whose private surface the async bridge uses."""

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning

from next.bridge.hops import TESTED, verdict
from next.checks import NEXT, register


_PIN = f"asgiref>={TESTED[0][0]}.{TESTED[0][1]},<{TESTED[1][0]}.{TESTED[1][1]}"


@register(NEXT)
def check_asgiref_release(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report an asgiref the hops cannot use, or one the contract tests never ran on.

    A served request refuses the first case with the same text, where no check runs.
    """
    found = verdict()
    if found.error is not None:
        return [
            Error(found.error, hint=f"Install {_PIN}.", obj=settings, id="next.E244")
        ]
    if found.warning is not None:
        return [
            DjangoWarning(
                found.warning,
                hint=f"Pin {_PIN} until next.dj lists the release as tested.",
                obj=settings,
                id="next.W188",
            )
        ]
    return []


__all__ = ["check_asgiref_release"]
