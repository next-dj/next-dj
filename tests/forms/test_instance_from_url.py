from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import pytest
from django import forms as django_forms
from django.contrib.auth.models import Group
from django.http import Http404, HttpRequest, QueryDict

from next.forms import (
    ActionRegistration,
    Form,
    FormActionDispatch,
    ModelForm,
    RegistryFormActionBackend,
)
from next.forms.base import (
    _instance_from_url_db_fields,
    _instance_from_url_on_non_model_form,
    _instance_from_url_unknown_field,
    _instance_lookup_from_spec,
)
from next.forms.checks import (
    check_instance_from_url_on_non_model_form,
    check_instance_from_url_unknown_field,
)
from next.forms.dispatch import (
    _accepts_var_keyword,
    _call_get_initial,
    _form_action_context_callable,
)


_FAKE_FILE = "/fake/myapp/forms.py"


class GroupByNameForm(ModelForm):
    """ModelForm loading a Group through its `name` field."""

    class Meta:
        """Bind to Group and load by name."""

        model = Group
        fields: ClassVar[list[str]] = ["name"]
        instance_from_url = "name"


class GroupByPkForm(ModelForm):
    """ModelForm loading a Group through its pk under a mismatched url kwarg."""

    class Meta:
        """Bind to Group and load by pk via the `id` url kwarg."""

        model = Group
        fields: ClassVar[list[str]] = ["name"]
        instance_from_url: ClassVar[dict[str, str]] = {"id": "pk"}


class GroupNoSpecForm(ModelForm):
    """ModelForm without instance_from_url, behaving as an unbound create form."""

    class Meta:
        """Bind to Group without a URL-driven instance lookup."""

        model = Group
        fields: ClassVar[list[str]] = ["name"]


class TestInstanceFromUrlDbFields:
    """_instance_from_url_db_fields enumerates the model lookup fields of a spec."""

    def test_string_spec_yields_single_field(self) -> None:
        """A string spec names exactly one db field."""
        assert _instance_from_url_db_fields("slug") == ["slug"]

    def test_dict_spec_yields_mapped_values(self) -> None:
        """A dict spec yields its values stringified as db fields."""
        assert _instance_from_url_db_fields({"id": "pk", "code": "slug"}) == [
            "pk",
            "slug",
        ]

    def test_non_str_dict_spec_yields_empty(self) -> None:
        """A non-str/dict spec contributes no db fields."""
        assert _instance_from_url_db_fields(7) == []


class TestInstanceLookupFromSpec:
    """_instance_lookup_from_spec builds an ORM lookup or None when a kwarg is absent."""

    def test_string_present_maps_to_self(self) -> None:
        """A present string kwarg maps the field name to its value."""
        assert _instance_lookup_from_spec("slug", {"slug": "intro"}) == {
            "slug": "intro"
        }

    def test_string_missing_returns_none(self) -> None:
        """A missing string kwarg yields None."""
        assert _instance_lookup_from_spec("slug", {}) is None

    def test_string_none_value_returns_none(self) -> None:
        """A string kwarg present but None yields None."""
        assert _instance_lookup_from_spec("slug", {"slug": None}) is None

    def test_string_falsy_zero_value_is_kept(self) -> None:
        """A present-but-falsy `0` is a valid lookup value, not treated as absent."""
        assert _instance_lookup_from_spec("id", {"id": 0}) == {"id": 0}

    def test_dict_falsy_empty_string_value_is_kept(self) -> None:
        """A present-but-falsy empty string is a valid lookup value, not treated as absent."""
        assert _instance_lookup_from_spec({"id": "pk"}, {"id": ""}) == {"pk": ""}

    def test_dict_single_pair_maps_to_db_field(self) -> None:
        """A single-pair dict maps the url kwarg value onto the db field."""
        assert _instance_lookup_from_spec({"id": "pk"}, {"id": 5}) == {"pk": 5}

    def test_dict_multi_pair_maps_each(self) -> None:
        """A multi-pair dict maps every url kwarg onto its db field."""
        lookup = _instance_lookup_from_spec(
            {"id": "pk", "code": "slug"}, {"id": 5, "code": "x"}
        )
        assert lookup == {"pk": 5, "slug": "x"}

    def test_dict_one_missing_kwarg_returns_none(self) -> None:
        """A dict with any missing url kwarg yields None."""
        assert (
            _instance_lookup_from_spec({"id": "pk", "code": "slug"}, {"id": 5}) is None
        )

    def test_non_str_dict_spec_returns_none(self) -> None:
        """A non-str/dict spec yields None."""
        assert _instance_lookup_from_spec(7, {"id": 5}) is None


@pytest.mark.django_db()
class TestGetInitial:
    """BaseModelForm.get_initial loads instances from the URL per Meta.instance_from_url."""

    def test_loads_group_by_name(self) -> None:
        """A string spec loads the matching Group by name."""
        group = Group.objects.create(name="editors")
        result = GroupByNameForm.get_initial(name="editors")
        assert result == group
        # model __eq__ only compares pk+class, so pin the loaded row's data
        assert result.pk == group.pk
        assert result.name == "editors"

    def test_loads_group_by_pk_via_dict(self) -> None:
        """A dict spec loads the matching Group by pk under a mismatched kwarg."""
        group = Group.objects.create(name="staff")
        result = GroupByPkForm.get_initial(id=group.pk)
        assert result == group
        assert result.pk == group.pk
        assert result.name == "staff"

    def test_multi_kwarg_dict_one_missing_returns_empty_dict(self) -> None:
        """A dict spec with a missing url kwarg returns create-mode initial without querying."""

        class GroupByIdAndSlugForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                instance_from_url: ClassVar[dict[str, str]] = {
                    "id": "pk",
                    "slug": "name",
                }

        assert GroupByIdAndSlugForm.get_initial(id=5) == {}

    def test_no_spec_returns_empty_dict(self) -> None:
        """A form without instance_from_url returns an empty initial dict."""
        assert GroupNoSpecForm.get_initial() == {}

    def test_missing_url_kwarg_returns_empty_dict(self) -> None:
        """A spec whose url kwarg is absent returns an empty initial dict."""
        assert GroupByNameForm.get_initial() == {}

    def test_object_not_found_raises_http404(self) -> None:
        """A spec resolving to no object raises Http404."""
        with pytest.raises(Http404):
            GroupByNameForm.get_initial(name="nope")


class TestValidateInstanceFromUrlE048:
    """_validate_instance_from_url records unknown model fields for E048."""

    def test_unknown_field_recorded(self) -> None:
        """A spec naming a field absent on the model records into the E048 list."""

        class UnknownFieldForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                instance_from_url = "does_not_exist"

        assert any(
            qualname.endswith("UnknownFieldForm") and field == "does_not_exist"
            for qualname, _label, field in _instance_from_url_unknown_field
        )

    def test_pk_field_records_nothing(self) -> None:
        """A `pk` lookup is always valid and records nothing."""

        class PkLookupForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                instance_from_url: ClassVar[dict[str, str]] = {"id": "pk"}

        assert not any(
            qualname.endswith("PkLookupForm")
            for qualname, _label, _field in _instance_from_url_unknown_field
        )

    def test_valid_field_records_nothing(self) -> None:
        """A spec naming a real model field records nothing."""

        class ValidFieldForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                instance_from_url = "name"

        assert not any(
            qualname.endswith("ValidFieldForm")
            for qualname, _label, _field in _instance_from_url_unknown_field
        )

    def test_double_underscore_validates_first_segment(self) -> None:
        """A `__`-lookup validates only the leading segment, so a real relation is fine."""

        class RelatedLookupForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                instance_from_url = "name__iexact"

        assert not any(
            qualname.endswith("RelatedLookupForm")
            for qualname, _label, _field in _instance_from_url_unknown_field
        )

    def test_no_model_records_nothing(self) -> None:
        """A ModelForm with instance_from_url but no Meta.model records nothing."""

        class NoModelForm(ModelForm):
            class Meta:
                instance_from_url = "anything"

        assert not any(
            qualname.endswith("NoModelForm")
            for qualname, _label, _field in _instance_from_url_unknown_field
        )


class TestValidateInstanceFromUrlE049:
    """_validate_instance_from_url records non-ModelForm usage for E049."""

    def test_non_model_form_recorded(self, settings, tmp_path) -> None:
        """instance_from_url on a plain Form records into the E049 list."""
        settings.BASE_DIR = tmp_path
        app_dir = tmp_path / "myapp"
        app_dir.mkdir()
        fake_path = str(app_dir / "forms.py")
        Path(fake_path).write_text("")

        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class PlainFormWithSpec(Form):
                class Meta:
                    instance_from_url = "x"

        assert any(
            qualname.endswith("PlainFormWithSpec")
            for qualname in _instance_from_url_on_non_model_form
        )


class TestChecksE048E049:
    """check_instance_from_url_* turn accumulator entries into Error messages."""

    def test_unknown_field_check_emits_one_error(self) -> None:
        """Each unknown-field entry produces one next.E048 error."""
        _instance_from_url_unknown_field.append(
            ("SomeForm", "auth.Group", "does_not_exist")
        )
        errors = check_instance_from_url_unknown_field()
        assert len(errors) == 1
        assert errors[0].id == "next.E048"
        assert "SomeForm" in errors[0].msg
        assert "does_not_exist" in errors[0].msg

    def test_non_model_form_check_emits_one_error(self) -> None:
        """Each non-ModelForm entry produces one next.E049 error."""
        _instance_from_url_on_non_model_form.append("PlainForm")
        errors = check_instance_from_url_on_non_model_form()
        assert len(errors) == 1
        assert errors[0].id == "next.E049"
        assert "PlainForm" in errors[0].msg

    def test_empty_lists_yield_no_errors(self) -> None:
        """No accumulator entries means no E048/E049 errors."""
        assert check_instance_from_url_unknown_field() == []
        assert check_instance_from_url_on_non_model_form() == []


class TestAcceptsVarKeyword:
    """_accepts_var_keyword detects a **kwargs parameter."""

    def test_true_for_var_keyword(self) -> None:
        """A function declaring **kwargs is reported as accepting var-keyword."""

        def fn(**kwargs: object) -> None:
            return None

        assert _accepts_var_keyword(fn) is True

    def test_false_without_var_keyword(self) -> None:
        """A function with only named params does not accept var-keyword."""

        def fn(a: int, b: int) -> None:
            return None

        assert _accepts_var_keyword(fn) is False

    @pytest.mark.parametrize(
        "uninspectable",
        [range, object()],
        ids=("value_error", "type_error"),
    )
    def test_false_for_uninspectable(self, uninspectable) -> None:
        """A callable whose signature can't be inspected is reported as False."""
        assert _accepts_var_keyword(uninspectable) is False


@pytest.mark.django_db()
class TestCallGetInitial:
    """_call_get_initial resolves deps and feeds url_kwargs only to **kwargs forms."""

    def test_var_keyword_path_injects_url_kwargs(self, mock_http_request) -> None:
        """The default ModelForm get_initial receives url_kwargs and loads the instance."""
        group = Group.objects.create(name="reviewers")
        request = mock_http_request(method="POST")
        result = _call_get_initial(
            GroupByNameForm,
            request,
            {"name": "reviewers"},
            cache={},
            stack=[],
        )
        assert result == group

    def test_non_var_keyword_path_omits_url_kwargs(self, mock_http_request) -> None:
        """A get_initial without **kwargs receives only its named params, never raw url_kwargs."""
        seen: dict[str, object] = {}

        class NamedOnlyForm(Form):
            name = django_forms.CharField()

            @classmethod
            def get_initial(cls, request: HttpRequest) -> dict[str, object]:
                # record exactly what bound, so a leaked url kwarg would show up here
                seen["received"] = {"request": request}
                return {}

        request = mock_http_request(method="POST")
        result = _call_get_initial(
            NamedOnlyForm,
            request,
            {"name": "ignored"},
            cache={},
            stack=[],
        )
        assert result == {}
        assert "request" in seen["received"]
        # the url kwarg name must not have been threaded into the non-**kwargs call
        assert "name" not in seen["received"]

    def test_missing_get_initial_raises_typeerror(self, mock_http_request) -> None:
        """A form class without get_initial raises TypeError."""

        class NoGetInitial:
            pass

        request = mock_http_request(method="POST")
        with pytest.raises(TypeError, match="must have get_initial method"):
            _call_get_initial(NoGetInitial, request, {}, cache={}, stack=[])


@pytest.mark.django_db()
class TestSaveUpdatesExistingRow:
    """A ModelForm bound to a loaded instance saves as an update, not a create."""

    def test_save_updates_existing_group(self, mock_http_request) -> None:
        """Loading via instance_from_url then saving updates the same row."""
        group = Group.objects.create(name="old-name")
        request = mock_http_request(method="POST")
        instance = _call_get_initial(
            GroupByNameForm,
            request,
            {"name": "old-name"},
            cache={},
            stack=[],
        )
        form = GroupByNameForm(data={"name": "new-name"}, instance=instance)
        assert form.is_valid()
        saved = form.save()
        assert saved.pk == group.pk
        assert Group.objects.get(pk=group.pk).name == "new-name"
        assert Group.objects.count() == 1


def _saving_handler(request: HttpRequest, form: ModelForm) -> object:
    """Persist a bound ModelForm and return a redirect-like object."""
    form.save()
    return type("R", (), {"url": "/done/"})()


@pytest.mark.django_db()
class TestDispatchInstanceFromUrlEndToEnd:
    """The full POST pipeline loads the URL instance and saves an update, not a create."""

    def _backend_for(self, form_class: type[ModelForm]) -> RegistryFormActionBackend:
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="edit_group",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=_saving_handler,
                form_class=form_class,
            )
        )
        return backend

    def test_string_spec_post_updates_existing_row(self, mock_http_request) -> None:
        """A `_url_param_name` hidden field drives a string-spec UPDATE through dispatch."""
        group = Group.objects.create(name="editors")
        backend = self._backend_for(GroupByNameForm)

        post = QueryDict(mutable=True)
        post["_url_param_name"] = "editors"
        post["name"] = "editors-renamed"
        request = mock_http_request(method="POST", POST=post, FILES=QueryDict())

        meta = backend.get_meta("edit_group")
        assert meta is not None
        response = FormActionDispatch.dispatch(backend, request, "edit_group", meta)

        assert response.status_code == 302
        assert Group.objects.count() == 1
        refreshed = Group.objects.get(pk=group.pk)
        assert refreshed.name == "editors-renamed"

    def test_dict_spec_post_updates_existing_row(self, mock_http_request) -> None:
        """A `_url_param_id` hidden field drives a dict-spec pk UPDATE through dispatch."""
        group = Group.objects.create(name="staff")
        backend = self._backend_for(GroupByPkForm)

        post = QueryDict(mutable=True)
        post["_url_param_id"] = str(group.pk)
        post["name"] = "staff-renamed"
        request = mock_http_request(method="POST", POST=post, FILES=QueryDict())

        meta = backend.get_meta("edit_group")
        assert meta is not None
        response = FormActionDispatch.dispatch(backend, request, "edit_group", meta)

        assert response.status_code == 302
        assert Group.objects.count() == 1
        refreshed = Group.objects.get(pk=group.pk)
        assert refreshed.name == "staff-renamed"

    def test_post_without_url_param_creates_new_row(self, mock_http_request) -> None:
        """With no `_url_param_*` field the spec is dormant and dispatch creates a new row."""
        Group.objects.create(name="existing")
        backend = self._backend_for(GroupByNameForm)

        post = QueryDict(mutable=True)
        post["name"] = "fresh"
        request = mock_http_request(method="POST", POST=post, FILES=QueryDict())

        meta = backend.get_meta("edit_group")
        assert meta is not None
        response = FormActionDispatch.dispatch(backend, request, "edit_group", meta)

        assert response.status_code == 302
        assert Group.objects.count() == 2
        assert Group.objects.filter(name="fresh").exists()


@pytest.mark.django_db()
class TestGetRenderInstanceFromUrl:
    """The GET-render path binds the loaded instance to the unbound render form."""

    def test_render_binds_loaded_instance(self, mock_http_request) -> None:
        """`_form_action_context_callable` loads the URL instance onto the render form."""
        group = Group.objects.create(name="reviewers")
        post = QueryDict(mutable=True)
        post["_url_param_name"] = "reviewers"
        request = mock_http_request(method="POST", POST=post)

        namespace = _form_action_context_callable(GroupByNameForm)(request)

        assert namespace.form.instance.pk == group.pk
        assert namespace.form.instance.name == "reviewers"
        assert namespace.form.is_bound is False

    def test_render_without_url_param_is_unbound_create(
        self, mock_http_request
    ) -> None:
        """Absent URL kwarg yields an unbound create form whose instance has no pk."""
        Group.objects.create(name="reviewers")
        request = mock_http_request(method="POST", POST=QueryDict())

        namespace = _form_action_context_callable(GroupByNameForm)(request)

        assert namespace.form.instance.pk is None

    def test_render_no_spec_is_unbound_create(self, mock_http_request) -> None:
        """A peer form with no spec renders an unbound create form regardless of url kwargs."""
        Group.objects.create(name="reviewers")
        post = QueryDict(mutable=True)
        post["_url_param_name"] = "reviewers"
        request = mock_http_request(method="POST", POST=post)

        namespace = _form_action_context_callable(GroupNoSpecForm)(request)

        assert namespace.form.instance.pk is None
