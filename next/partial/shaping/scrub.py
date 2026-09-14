"""Cleaning of validation errors down to the fields a validate pass asked for."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast, override

from django.core.exceptions import NON_FIELD_ERRORS
from django.forms import BaseForm, BaseFormSet, FileField

from next.partial.envelope import FormMeta


if TYPE_CHECKING:
    from collections.abc import Iterable


class _NonFormErrors(Protocol):
    """The writable non-form-errors attribute Django keeps no setter for."""

    _non_form_errors: object


@dataclass(frozen=True, slots=True)
class _Member:
    """One bound form of a pass, with the prefix its field names travel under."""

    form: BaseForm
    prefix: str = ""

    def wire_name(self, name: str) -> str:
        """Return the name the field travels under on the wire."""
        return f"{self.prefix}{name}"

    def file_fields(self) -> "Iterable[str]":
        """Yield the wire name of each file field the member declares."""
        for name, field in self.form.fields.items():
            if isinstance(field, FileField):
                yield self.wire_name(name)

    def scrub(self, requested: frozenset[str]) -> None:
        """Keep only the errors of the requested fields.

        The non-field errors go whatever was requested, because a `clean()`
        belongs to the submit and not to a per-field blur.
        """
        errors = self.form.errors
        survivors = {
            name
            for name in errors
            if name != NON_FIELD_ERRORS and self.wire_name(name) in requested
        }
        for name in list(errors):
            if name not in survivors:
                del errors[name]


class _Pass(ABC):
    """Uniform view of the members a form or a formset offers to one pass.

    A formset differs from a plain form only in the members it holds and in the
    prefixed names their fields travel under, so every pass shares one body.
    """

    @abstractmethod
    def members(self) -> "Iterable[_Member]":
        """Return the bound forms the pass walks."""

    @abstractmethod
    def clear_cross_errors(self) -> None:
        """Drop the errors a clean across members produced."""


@dataclass(frozen=True, slots=True)
class _FormPass(_Pass):
    """The single-member view of a plain form."""

    form: BaseForm

    @override
    def members(self) -> "Iterable[_Member]":
        """Return the form itself as its only member."""
        return (_Member(self.form),)

    @override
    def clear_cross_errors(self) -> None:
        """Clear nothing, a plain form holds no errors outside its own fields."""


@dataclass(frozen=True, slots=True)
class _FormSetPass(_Pass):
    """The many-member view of a formset, whose fields travel prefixed."""

    formset: BaseFormSet

    @override
    def members(self) -> "Iterable[_Member]":
        """Return each member form with the prefix of its row."""
        return tuple(
            _Member(form, prefix=f"{form.prefix}-") for form in self.formset.forms
        )

    @override
    def clear_cross_errors(self) -> None:
        """Clear the non-form errors a cross-form clean produced."""
        formset = self.formset
        cast("_NonFormErrors", formset)._non_form_errors = formset.error_class()


def _pass_over(form: "BaseForm | BaseFormSet") -> _Pass:
    """Return the member view of a form or a formset."""
    if isinstance(form, BaseFormSet):
        return _FormSetPass(form)
    return _FormPass(form)


def _validate_targets(
    form: "BaseForm | BaseFormSet", validate_fields: tuple[str, ...]
) -> frozenset[str]:
    """Return the requested field names with file fields removed.

    A multipart file is never re-uploaded on a blur, so even if a client
    names a file field the server drops it from the validate target set.
    """
    files = _file_field_names(form)
    return frozenset(name for name in validate_fields if name not in files)


def _file_field_names(form: "BaseForm | BaseFormSet") -> frozenset[str]:
    """Return the wire names of every file field a form or a formset declares."""
    return frozenset(
        name for member in _pass_over(form).members() for name in member.file_fields()
    )


def _scrub_errors(form: "BaseForm | BaseFormSet", requested: frozenset[str]) -> None:
    """Drop every error the validate request did not ask to surface.

    Only the errors of the requested fields survive, and a formset loses its non-form
    errors too, so no cross-member clean surfaces on a per-field blur.
    """
    walk = _pass_over(form)
    for member in walk.members():
        member.scrub(requested)
    walk.clear_cross_errors()


def _error_count(form: "BaseForm | BaseFormSet") -> int:
    """Return the number of surviving error messages after scrubbing."""
    return sum(
        len(messages)
        for member in _pass_over(form).members()
        for messages in member.form.errors.values()
    )


def _form_meta(uid: str, form: "BaseForm | BaseFormSet") -> FormMeta:
    """Build the machine-readable form meta from a bound form's errors."""
    errors = _meta_errors(form)
    return FormMeta(uid=uid, valid=not errors, errors=errors)


def _meta_errors(form: "BaseForm | BaseFormSet") -> dict[str, list[str]]:
    """Return the errors of a form or a formset under their wire names."""
    return {
        member.wire_name(name): [str(message) for message in messages]
        for member in _pass_over(form).members()
        for name, messages in member.form.errors.items()
    }
