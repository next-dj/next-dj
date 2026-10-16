from django import forms as django_forms
from django.http import HttpRequest


POLICY_FIELD = "policy_acknowledged"


class AcknowledgedStep(django_forms.Form):
    """Base step that carries the retention acknowledgement on every wizard step.

    The acknowledgement is a real field, so a bound blur morph replays the tick sent.
    """

    policy_acknowledged = django_forms.BooleanField(
        required=False,
        initial=True,
        widget=django_forms.CheckboxInput(attrs={"class": "mt-0.5"}),
    )

    @classmethod
    def is_acknowledged(cls, request: HttpRequest) -> bool:
        """Read the tick off a POST the way binding the field would read it.

        `CheckboxInput` accepts `on`, `true` and `1` alike, so asking the widget keeps
        the gate and the bound form from disagreeing over an unusual client.
        """
        widget = cls.base_fields[POLICY_FIELD].widget
        return bool(
            widget.value_from_datadict(request.POST, request.FILES, POLICY_FIELD)
        )
