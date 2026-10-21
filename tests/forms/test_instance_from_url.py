from collections.abc import Generator
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import pytest
from django import forms as django_forms
from django.contrib.auth.models import Group
from django.http import Http404, HttpRequest, QueryDict
from django.test import Client

from next.deps import REQUEST_DEP_CACHE_ATTR
from next.deps.objects import SCOPE_DEPENDENCY
from next.forms import ActionRegistration, Form, ModelForm, RegistryFormActionBackend
from next.forms.base import _instance_from_url_db_fields, _instance_lookup_from_spec
from next.forms.checks import (
    check_instance_from_url_on_non_model_form,
    check_instance_from_url_unknown_field,
)
from next.forms.dispatch import FormActionDispatch
from next.forms.dispatch.build import (
    _accepts_var_keyword,
    _call_get_initial,
    _form_action_context_callable,
)
from next.forms.registration import registration_diagnostics
from tests.support import bound_dependency, routed, write_page


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

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            pytest.param("slug", ["slug"], id="string-names-one-field"),
            pytest.param(
                {"id": "pk", "code": "slug"}, ["pk", "slug"], id="dict-yields-values"
            ),
            pytest.param(7, [], id="non-str-non-dict-yields-none"),
        ],
    )
    def test_spec_yields_db_fields(self, spec, expected: list[str]) -> None:
        assert _instance_from_url_db_fields(spec) == expected


class TestInstanceLookupFromSpec:
    """_instance_lookup_from_spec builds an ORM lookup or None when a kwarg is absent."""

    @pytest.mark.parametrize(
        ("spec", "url_kwargs", "expected"),
        [
            pytest.param(
                "slug", {"slug": "intro"}, {"slug": "intro"}, id="str-present"
            ),
            pytest.param("slug", {}, None, id="str-missing"),
            pytest.param("slug", {"slug": None}, None, id="str-none-value"),
            pytest.param("id", {"id": 0}, {"id": 0}, id="str-falsy-zero-kept"),
            pytest.param(
                {"id": "pk"}, {"id": ""}, {"pk": ""}, id="dict-empty-str-kept"
            ),
            pytest.param({"id": "pk"}, {"id": 5}, {"pk": 5}, id="dict-single-pair"),
            pytest.param(
                {"id": "pk", "code": "slug"},
                {"id": 5, "code": "x"},
                {"pk": 5, "slug": "x"},
                id="dict-multi-pair",
            ),
            pytest.param(
                {"id": "pk", "code": "slug"}, {"id": 5}, None, id="dict-one-missing"
            ),
            pytest.param(7, {"id": 5}, None, id="non-str-non-dict"),
        ],
    )
    def test_lookup(self, spec, url_kwargs, expected) -> None:
        assert _instance_lookup_from_spec(spec, url_kwargs) == expected


@pytest.mark.django_db()
class TestGetInitial:
    """BaseModelForm.get_initial loads the instance Meta.instance_from_url names."""

    def test_loads_group_by_name(self) -> None:
        """A string spec loads the matching Group by name."""
        group = Group.objects.create(name="editors")
        result = GroupByNameForm.get_initial(name="editors")
        assert result == group
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
        """A dict spec missing its url kwarg answers create mode, unqueried."""

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


def _flagged_unknown_field(class_name: str) -> bool:
    return any(
        qualname.endswith(class_name)
        for qualname, _label, _field in registration_diagnostics.instance_from_url_unknown_field
    )


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
            for qualname, _label, field in registration_diagnostics.instance_from_url_unknown_field
        )

    def test_pk_field_records_nothing(self) -> None:
        """A `pk` lookup is always valid and records nothing."""

        class PkLookupForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                instance_from_url: ClassVar[dict[str, str]] = {"id": "pk"}

        assert not _flagged_unknown_field("PkLookupForm")

    def test_valid_field_records_nothing(self) -> None:
        """A spec naming a real model field records nothing."""

        class ValidFieldForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                instance_from_url = "name"

        assert not _flagged_unknown_field("ValidFieldForm")

    def test_double_underscore_validates_first_segment(self) -> None:
        """A `__`-lookup validates only the leading segment, so a real relation is fine."""

        class RelatedLookupForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                instance_from_url = "name__iexact"

        assert not _flagged_unknown_field("RelatedLookupForm")

    def test_no_model_records_nothing(self) -> None:
        """A ModelForm with instance_from_url but no Meta.model records nothing."""

        class NoModelForm(ModelForm):
            class Meta:
                instance_from_url = "anything"

        assert not _flagged_unknown_field("NoModelForm")


class TestValidateInstanceFromUrlE049:
    """_validate_instance_from_url records non-ModelForm usage for E049."""

    def test_non_model_form_recorded(self, settings, tmp_path) -> None:
        """instance_from_url on a plain Form records into the E049 list."""
        settings.BASE_DIR = tmp_path
        app_dir = tmp_path / "myapp"
        app_dir.mkdir()
        fake_path = str(app_dir / "forms.py")
        Path(fake_path).write_text("")

        with patch("next.forms.base._definition_file_of", return_value=fake_path):

            class PlainFormWithSpec(Form):
                class Meta:
                    instance_from_url = "x"

        assert any(
            qualname.endswith("PlainFormWithSpec")
            for qualname in registration_diagnostics.instance_from_url_on_non_model_form
        )


class TestChecksE048E049:
    """check_instance_from_url_* turn accumulator entries into Error messages."""

    def test_unknown_field_check_emits_one_error(self) -> None:
        """Each unknown-field entry produces one next.E048 error."""
        registration_diagnostics.instance_from_url_unknown_field.append(
            ("SomeForm", "auth.Group", "does_not_exist")
        )
        errors = check_instance_from_url_unknown_field()
        assert len(errors) == 1
        assert errors[0].id == "next.E048"
        assert "SomeForm" in errors[0].msg
        assert "does_not_exist" in errors[0].msg

    def test_non_model_form_check_emits_one_error(self) -> None:
        """Each non-ModelForm entry produces one next.E049 error."""
        registration_diagnostics.instance_from_url_on_non_model_form.append("PlainForm")
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

        def fn(**kwargs) -> None:
            return None

        assert _accepts_var_keyword(fn) is True

    def test_false_without_var_keyword(self) -> None:
        """A function with only named params does not accept var-keyword."""

        def fn(a: int, b: int) -> None:
            return None

        assert _accepts_var_keyword(fn) is False

    @pytest.mark.parametrize(
        "uninspectable", [range, object()], ids=("value_error", "type_error")
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
            GroupByNameForm, request, {"name": "reviewers"}, deps=({}, [])
        )
        assert result == group

    def test_non_var_keyword_path_omits_url_kwargs(self, mock_http_request) -> None:
        """A get_initial without **kwargs gets its named params, never url_kwargs."""
        seen: dict[str, object] = {}

        class NamedOnlyForm(Form):
            name = django_forms.CharField()

            @classmethod
            def get_initial(cls, request: HttpRequest) -> dict[str, object]:
                seen["received"] = {"request": request}
                return {}

        request = mock_http_request(method="POST")
        result = _call_get_initial(
            NamedOnlyForm, request, {"name": "ignored"}, deps=({}, [])
        )
        assert result == {}
        assert "request" in seen["received"]
        assert "name" not in seen["received"]

    def test_missing_get_initial_raises_typeerror(self, mock_http_request) -> None:
        """A form class without get_initial raises TypeError."""

        class NoGetInitial:
            pass

        request = mock_http_request(method="POST")
        with pytest.raises(TypeError, match=r"^NoGetInitial has no get_initial method"):
            _call_get_initial(NoGetInitial, request, {}, deps=({}, []))


@pytest.mark.django_db()
class TestSaveUpdatesExistingRow:
    """A ModelForm bound to a loaded instance saves as an update, not a create."""

    def test_save_updates_existing_group(self, mock_http_request) -> None:
        """Loading via instance_from_url then saving updates the same row."""
        group = Group.objects.create(name="old-name")
        request = mock_http_request(method="POST")
        instance = _call_get_initial(
            GroupByNameForm, request, {"name": "old-name"}, deps=({}, [])
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

    def _dispatch(self, mock_http_request, form_class, fields: dict[str, str]):
        post = QueryDict(mutable=True)
        post.update(fields)
        request = mock_http_request(method="POST", POST=post, FILES=QueryDict())
        backend = self._backend_for(form_class)
        meta = backend.get_meta("edit_group")
        assert meta is not None
        return FormActionDispatch.dispatch(backend, request, "edit_group", meta)

    @pytest.mark.parametrize(
        ("form_class", "created", "origin"),
        [
            pytest.param(GroupByNameForm, "editors", "/groups/editors/", id="string"),
            pytest.param(GroupByPkForm, "staff", "/items/{pk}/", id="dict"),
        ],
    )
    def test_spec_post_updates_existing_row(
        self, mock_http_request, form_class, created: str, origin: str
    ) -> None:
        """The origin kwargs drive a spec UPDATE through dispatch."""
        group = Group.objects.create(name=created)

        response = self._dispatch(
            mock_http_request,
            form_class,
            {
                "_next_form_origin": origin.format(pk=group.pk),
                "name": f"{created}-renamed",
            },
        )

        assert response.status_code == 302
        assert Group.objects.count() == 1
        assert Group.objects.get(pk=group.pk).name == f"{created}-renamed"

    def test_post_without_origin_kwargs_creates_new_row(
        self, mock_http_request
    ) -> None:
        """With no origin URL kwargs the spec is dormant and dispatch creates a new row."""
        Group.objects.create(name="existing")

        response = self._dispatch(mock_http_request, GroupByNameForm, {"name": "fresh"})

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
        post["_next_form_origin"] = "/groups/reviewers/"
        request = mock_http_request(method="POST", POST=post)

        namespace = _form_action_context_callable(GroupByNameForm)(request)

        assert namespace.form.instance.pk == group.pk
        assert namespace.form.instance.name == "reviewers"
        assert namespace.form.is_bound is False

    def test_render_without_origin_kwargs_is_unbound_create(
        self, mock_http_request
    ) -> None:
        """Absent URL kwarg yields an unbound create form whose instance has no pk."""
        Group.objects.create(name="reviewers")
        request = mock_http_request(method="POST", POST=QueryDict())

        namespace = _form_action_context_callable(GroupByNameForm)(request)

        assert namespace.form.instance.pk is None

    def test_render_no_spec_is_unbound_create(self, mock_http_request) -> None:
        """A peer form with no spec renders unbound whatever the url kwargs."""
        Group.objects.create(name="reviewers")
        post = QueryDict(mutable=True)
        post["_next_form_origin"] = "/groups/reviewers/"
        request = mock_http_request(method="POST", POST=post)

        namespace = _form_action_context_callable(GroupNoSpecForm)(request)

        assert namespace.form.instance.pk is None


@pytest.fixture()
def auth_scope(scoped_models) -> Generator[None, None, None]:
    with bound_dependency(SCOPE_DEPENDENCY, lambda: "auth"):
        yield


@pytest.mark.django_db()
@pytest.mark.usefixtures("auth_scope")
class TestScopedGetInitial:
    """A model with `visible_to` and a `scope` loads its row the way `DObject` does."""

    def test_a_visible_row_by_a_field(self) -> None:
        group = Group.objects.create(name="auth:editors")
        assert GroupByNameForm.get_initial(HttpRequest(), name="auth:editors") == group

    @pytest.mark.parametrize(
        ("form", "kwargs"),
        [
            pytest.param(GroupByNameForm, {"name": "other:outsiders"}, id="field"),
            pytest.param(GroupByPkForm, {"id": None}, id="pk"),
            pytest.param(GroupByPkForm, {"id": "not-a-pk"}, id="malformed-pk"),
        ],
    )
    def test_a_hidden_row_answers_404(self, form, kwargs) -> None:
        outsiders = Group.objects.create(name="other:outsiders")
        kwargs = {
            key: outsiders.pk if value is None else value
            for key, value in kwargs.items()
        }
        with pytest.raises(Http404):
            form.get_initial(HttpRequest(), **kwargs)

    def test_a_call_without_the_request_names_the_fix(self) -> None:
        Group.objects.create(name="auth:editors")
        with pytest.raises(TypeError, match=r"super\(\)\.get_initial\(request"):
            GroupByNameForm.get_initial(name="auth:editors")

    def test_the_row_is_shared_with_the_request_cache(
        self, django_assert_num_queries
    ) -> None:
        group = Group.objects.create(name="auth:staff")
        request = HttpRequest()
        setattr(request, REQUEST_DEP_CACHE_ATTR, {})
        with django_assert_num_queries(1):
            first = GroupByPkForm.get_initial(request, id=str(group.pk))
            second = GroupByPkForm.get_initial(request, id=group.pk)
        assert first is second
        assert set(getattr(request, REQUEST_DEP_CACHE_ATTR)) >= {
            f"DObject[auth.group]({group.pk!r})"
        }


@pytest.mark.django_db()
def test_without_a_scope_the_row_loads_unscoped(scoped_models) -> None:
    outsiders = Group.objects.create(name="other:outsiders")
    assert GroupByNameForm.get_initial(name="other:outsiders") == outsiders


_SHARED_PAGE = """
from typing import ClassVar

from django.contrib.auth.models import Group
from django.http import HttpResponse

from next.deps import DObject
from next.forms import ModelForm, action
from next.pages import context


@context("group")
def group(group: DObject[Group]) -> Group:
    return group


class SharedGroupForm(ModelForm):
    class Meta:
        model = Group
        fields: ClassVar[list[str]] = ["name"]

    @classmethod
    def get_initial(cls, group: DObject[Group]) -> Group:
        return group


@action("shared_group", form_class=SharedGroupForm)
def save(form: SharedGroupForm) -> HttpResponse:
    return HttpResponse("saved")
"""


_URL_PAGE = """
from typing import ClassVar

from django.contrib.auth.models import Group
from django.http import HttpResponse

from next.deps import DObject
from next.forms import ModelForm, action
from next.pages import context


@context("group")
def group(group: DObject[Group]) -> Group:
    return group


class UrlGroupForm(ModelForm):
    class Meta:
        model = Group
        fields: ClassVar[list[str]] = ["name"]
        instance_from_url: ClassVar[dict[str, str]] = {"group": "pk"}


@action("shared_group", form_class=UrlGroupForm)
def save(form: UrlGroupForm) -> HttpResponse:
    return HttpResponse("saved")
"""


@pytest.mark.django_db()
@pytest.mark.usefixtures("auth_scope")
@pytest.mark.parametrize(
    "source",
    [
        pytest.param(_SHARED_PAGE, id="get-initial"),
        pytest.param(_URL_PAGE, id="instance-from-url"),
    ],
)
def test_the_page_and_its_form_load_the_row_once(
    tmp_path: Path, django_assert_num_queries, source: str
) -> None:
    group = Group.objects.create(name="auth:staff")
    root = tmp_path / "pages"
    write_page(
        root,
        "[int:group]",
        source,
        body='{{ group.name }}|{% form "shared_group" %}{{ form.name.value }}'
        "{% endform %}",
    )
    with routed(root), django_assert_num_queries(1):
        response = Client().get(f"/{group.pk}/")
    assert response.status_code == 200
    assert response.content.decode().count("auth:staff") == 2
