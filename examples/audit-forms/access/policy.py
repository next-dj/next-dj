from django import forms as django_forms


POLICY_FIELD = "policy_acknowledged"


class AcknowledgedStep(django_forms.Form):
    """Base step that carries the retention acknowledgement on every wizard step.

    The acknowledgement is a real form field rather than raw markup so a
    re-render reflects what the visitor actually submitted. Raw markup with a
    hardcoded `checked` would let any partial morph put the tick back.
    """

    policy_acknowledged = django_forms.BooleanField(
        required=False,
        initial=True,
        widget=django_forms.CheckboxInput(attrs={"class": "mt-0.5"}),
    )
