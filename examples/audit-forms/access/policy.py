from django import forms as django_forms


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
