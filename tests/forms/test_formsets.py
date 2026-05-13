from django import forms as django_forms
from django.forms import formset_factory

from next.forms import cleanup_extra_initial


class _Row(django_forms.Form):
    title = django_forms.CharField(max_length=20)
    weight = django_forms.IntegerField(initial=0)


_RowFormset = formset_factory(_Row, extra=2)


class TestCleanupExtraInitial:
    def test_blank_extras_lose_initial(self) -> None:
        formset = _RowFormset(initial=[{"title": "first"}])
        cleanup_extra_initial(formset)
        # Two extras come after the seeded row.
        extras = formset.forms[1:]
        assert all(f.initial == {} for f in extras)
        for f in extras:
            for field in f.fields.values():
                assert field.initial is None

    def test_non_extra_row_initial_preserved(self) -> None:
        formset = _RowFormset(initial=[{"title": "first"}])
        cleanup_extra_initial(formset)
        seeded = formset.forms[0]
        assert seeded.initial == {"title": "first"}

    def test_idempotent(self) -> None:
        formset = _RowFormset()
        cleanup_extra_initial(formset)
        cleanup_extra_initial(formset)
        for f in formset.forms:
            assert f.initial == {}

    def test_form_without_instance_attribute(self) -> None:
        # Plain BaseFormSet rows have no `.instance`; helper still runs.
        formset = _RowFormset()
        cleanup_extra_initial(formset)
        assert all(f.initial == {} for f in formset.forms)
