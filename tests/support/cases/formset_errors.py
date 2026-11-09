from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django import forms


if TYPE_CHECKING:
    from collections.abc import Callable


_REQUIRED = "This field is required."
_TOO_LONG = "Ensure this value has at most 20 characters (it has 30)."
_CROSS_ROW = "Cross-row failure."
_ROW_CLEAN = "Row failure."


class ErrorRow(forms.Form):
    """A formset row with one length-capped title."""

    title = forms.CharField(max_length=20)


class _CheckedRow(ErrorRow):
    def clean(self) -> dict[str, object]:
        cleaned = super().clean() or {}
        if cleaned.get("title") == "bad":
            raise forms.ValidationError(_ROW_CLEAN)
        return cleaned


class CrossRowFormSet(forms.BaseFormSet):
    """A formset whose own `clean` always rejects the submit."""

    def clean(self) -> None:
        """Reject the submit across rows."""
        raise forms.ValidationError(_CROSS_ROW)


def _rows(*titles: str, deleted: tuple[int, ...] = ()) -> dict[str, str]:
    """Return the POST data of a formset with one row per title."""
    data = {"form-TOTAL_FORMS": str(len(titles)), "form-INITIAL_FORMS": "0"}
    for index, title in enumerate(titles):
        data[f"form-{index}-title"] = title
    data.update({f"form-{index}-DELETE": "on" for index in deleted})
    return data


def _plain_form() -> forms.BaseForm:
    return ErrorRow({"title": ""})


def _clean_formset() -> forms.BaseFormSet:
    formset_class = forms.formset_factory(ErrorRow, extra=2)
    return formset_class(_rows("ok", ""))


def _unbound_formset() -> forms.BaseFormSet:
    return forms.formset_factory(ErrorRow, can_delete=True)()


def _max_num_formset() -> forms.BaseFormSet:
    formset_class = forms.formset_factory(ErrorRow, max_num=1, validate_max=True)
    return formset_class(_rows("a", "b"))


def _cross_row_formset() -> forms.BaseFormSet:
    formset_class = forms.formset_factory(ErrorRow, formset=CrossRowFormSet)
    return formset_class(_rows("a", "b"))


def _row_clean_formset() -> forms.BaseFormSet:
    formset_class = forms.formset_factory(_CheckedRow)
    return formset_class(_rows("ok", "bad"))


def _deleted_row_formset() -> forms.BaseFormSet:
    formset_class = forms.formset_factory(ErrorRow, can_delete=True)
    return formset_class(_rows("ok", "x" * 30, deleted=(1,)))


def _mixed_formset() -> forms.BaseFormSet:
    formset_class = forms.formset_factory(
        ErrorRow, formset=CrossRowFormSet, can_delete=True
    )
    return formset_class(_rows("x" * 30, "ok", "x" * 30, deleted=(2,)))


@dataclass(frozen=True, slots=True)
class WireErrorsCase:
    """One row for the wire-error walk over a bound form or formset."""

    id: str
    build: Callable[[], forms.BaseForm | forms.BaseFormSet]
    expected: dict[str, list[str]]


WIRE_ERRORS_CASES: tuple[WireErrorsCase, ...] = (
    WireErrorsCase("plain_form", _plain_form, {"title": [_REQUIRED]}),
    WireErrorsCase("clean_formset_blank_extra", _clean_formset, {}),
    WireErrorsCase("unbound_formset", _unbound_formset, {}),
    WireErrorsCase(
        "non_form_max_num",
        _max_num_formset,
        {"__all__": ["Please submit at most 1 form."]},
    ),
    WireErrorsCase("non_form_cross_row", _cross_row_formset, {"__all__": [_CROSS_ROW]}),
    WireErrorsCase("row_clean", _row_clean_formset, {"form-1-__all__": [_ROW_CLEAN]}),
    WireErrorsCase("deleted_invalid_row_ignored", _deleted_row_formset, {}),
    WireErrorsCase(
        "mixed", _mixed_formset, {"__all__": [_CROSS_ROW], "form-0-title": [_TOO_LONG]}
    ),
)
