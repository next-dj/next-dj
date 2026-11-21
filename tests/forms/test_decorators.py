from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from django import forms, forms as django_forms
from django.contrib.auth.models import Group
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import BaseModelForm, Form, ModelForm
from next.forms.base import _is_self_registered
from next.forms.decorators import action as action_decorator
from next.forms.dispatch.build import _form_action_context_callable
from next.forms.manager import build_form_namespace_for_action, form_action_manager
from next.forms.registration import registration_diagnostics
from next.transactions import AtomicOverride
from tests.support import attribution, handler_declared_here


class TestBuildFormNamespaceForAction:
    """``build_form_namespace_for_action`` when action has no form class."""

    def test_returns_none_for_action_without_form_class(
        self, mock_http_request
    ) -> None:
        """Actions without ``form_class`` return None."""
        req = mock_http_request(method="GET")
        assert build_form_namespace_for_action("test_no_form", req) is None


class TestActionDecorator:
    """``@action("name")`` registers a form-less handler."""

    def test_registers_form_less_action(self) -> None:
        """A decorated function is registered in the backend without form_class."""

        def logout_handler(request: HttpRequest) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        action_decorator("logout_test")(logout_handler)

        meta = form_action_manager.default_backend.get_meta("logout_test")
        assert meta is not None
        assert meta["handler"] is logout_handler
        assert meta.get("form_class") is None

    def test_returns_original_function(self) -> None:
        """Decorator returns the original function unchanged."""

        def my_handler(request: HttpRequest) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        result = action_decorator("return_test_handler")(my_handler)
        assert result is my_handler

    def test_applied_to_class_returns_class_without_registering(self) -> None:
        """Applying @action to a class is recorded and skips registration."""

        @action_decorator("bad_class_action")
        class SomeClass:
            pass

        assert isinstance(SomeClass, type)
        meta = form_action_manager.default_backend.get_meta("bad_class_action")
        assert meta is None

    def test_class_name_recorded_in_applied_to_class_list(self) -> None:
        """When @action is applied to a class, its qualname is recorded."""

        @action_decorator("recorded_class")
        class MyBadClass:
            pass

        assert any(
            "MyBadClass" in entry
            for entry in registration_diagnostics.action_applied_to_class
        )

    def test_scope_is_shared_for_non_anchor_file(self) -> None:
        """Handler registered from a non-anchor file gets scope='shared'."""

        def handler(request: HttpRequest) -> None:
            pass

        action_decorator("shared_scope_handler")(handler)

        meta = form_action_manager.default_backend.get_meta("shared_scope_handler")
        assert meta is not None
        assert meta["scope"] == "shared"


class TestActionDecoratorBareForm:
    """``@action`` without arguments derives the name from the function."""

    def test_bare_form_registers_under_function_name(self) -> None:
        """A bare @action registers the function under its own name."""

        @action_decorator
        def bare_form_handler(request: HttpRequest) -> None:
            pass

        meta = form_action_manager.default_backend.get_meta("bare_form_handler")
        assert meta is not None
        assert meta["handler"] is bare_form_handler
        assert meta["scope"] == "shared"

    def test_empty_parentheses_register_under_function_name(self) -> None:
        """@action() with no name registers the function under its own name."""

        @action_decorator()
        def empty_parens_handler(request: HttpRequest) -> None:
            pass

        meta = form_action_manager.default_backend.get_meta("empty_parens_handler")
        assert meta is not None
        assert meta["handler"] is empty_parens_handler

    def test_bare_form_on_class_records_e053_without_registering(self) -> None:
        """A bare @action on a class is buffered for E053 and returned unchanged."""

        @action_decorator
        class BareDecoratedClass:
            pass

        assert isinstance(BareDecoratedClass, type)
        assert any(
            "BareDecoratedClass" in entry
            for entry in registration_diagnostics.action_applied_to_class
        )
        assert (
            form_action_manager.default_backend.get_meta("bare_decorated_class") is None
        )


class TestActionDecoratorAttribution:
    """Where ``@action`` attributes a handler declared in another module."""

    def test_imported_function_keys_on_declaring_file_not_call_site(self) -> None:
        """An imported handler keys on its own module, not on the decorating file."""
        action_decorator(name="imported_handler_action")(handler_declared_here)

        meta = form_action_manager.default_backend.get_meta("imported_handler_action")
        assert meta is not None
        assert meta["file_path"] == str(Path(attribution.__file__).resolve())
        assert meta["file_path"] != str(Path(__file__).resolve())


class TestActionDecoratorScope:
    """The scope keyword overrides the file-derived scope."""

    def test_scope_page_overrides_file_derived_scope(self) -> None:
        """scope='page' wins over the 'shared' default of a non-anchor file."""

        @action_decorator(scope="page")
        def page_scoped_handler(request: HttpRequest) -> None:
            pass

        page_path = str(Path(__file__).resolve())
        meta = form_action_manager.default_backend.get_meta(
            "page_scoped_handler", page_path
        )
        assert meta is not None
        assert meta["scope"] == "page"

    def test_invalid_scope_skips_registration_and_buffers_e047(self) -> None:
        """An invalid scope value skips registration and feeds the E047 buffer."""

        @action_decorator(scope="global")
        def bad_scope_handler(request: HttpRequest) -> None:
            pass

        assert form_action_manager.default_backend.get_meta("bad_scope_handler") is None
        assert any(
            "bad_scope_handler" in qualname and bad == "global"
            for qualname, bad in registration_diagnostics.invalid_action_scope
        )

    def test_invalid_scope_returns_function_untouched(self) -> None:
        """The decorated function survives an invalid scope unchanged."""

        def untouched_handler(request: HttpRequest) -> None:
            pass

        result = action_decorator(scope="nope")(untouched_handler)
        assert result is untouched_handler


class TestActionDecoratorFormClass:
    """form_class accepts factories and non-self-registering Form classes."""

    def test_self_registered_form_class_raises_type_error(self) -> None:
        """A Form class that registered its own endpoint is rejected."""

        class SelfRegisteredContactForm(Form):
            name = forms.CharField(max_length=100)

        with pytest.raises(TypeError, match="already registers"):
            action_decorator("send_contact", form_class=SelfRegisteredContactForm)

    def test_abstract_form_class_is_accepted(self) -> None:
        """A Meta.abstract Form class passes through and is stored as-is."""

        class AbstractContactForm(Form):
            name = forms.CharField(max_length=100)

            class Meta:
                abstract = True

        @action_decorator("send_abstract_contact", form_class=AbstractContactForm)
        def send_abstract_contact(request: HttpRequest) -> None:
            pass

        meta = form_action_manager.default_backend.get_meta("send_abstract_contact")
        assert meta is not None
        assert meta["form_class"] is AbstractContactForm
        assert meta["handler"] is send_abstract_contact

    def test_plain_django_form_class_is_accepted(self) -> None:
        """A vanilla django.forms class never self-registers and passes through."""

        class PlainDjangoForm(django_forms.Form):
            name = django_forms.CharField()

        @action_decorator("send_plain_django", form_class=PlainDjangoForm)
        def send_plain_django(request: HttpRequest) -> None:
            pass

        meta = form_action_manager.default_backend.get_meta("send_plain_django")
        assert meta is not None
        assert meta["form_class"] is PlainDjangoForm

    def test_form_class_outside_base_dir_is_accepted(self, settings) -> None:
        """A Form class skipped by the BASE_DIR gate carries no marker."""
        settings.BASE_DIR = "/nonexistent-base-dir"

        class OutsideBaseDirForm(Form):
            name = forms.CharField(max_length=100)

        assert not _is_self_registered(OutsideBaseDirForm)

        @action_decorator("send_outside", form_class=OutsideBaseDirForm)
        def send_outside(request: HttpRequest) -> None:
            pass

        meta = form_action_manager.default_backend.get_meta("send_outside")
        assert meta is not None
        assert meta["form_class"] is OutsideBaseDirForm


class TestBaseFormGetInitial:
    """``BaseForm.get_initial`` in its default form and when a subclass overrides it."""

    def test_get_initial_returns_empty_dict_by_default(self) -> None:
        """Default get_initial returns empty dict."""
        assert Form.get_initial() == {}

    def test_get_initial_can_be_overridden(self) -> None:
        """Subclasses can override get_initial."""

        class CustomForm(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest) -> dict:
                return {"name": "default_name"}

        request = HttpRequest()
        result = CustomForm.get_initial(request)
        assert result == {"name": "default_name"}

    def test_get_initial_receives_request(self) -> None:
        """get_initial receives request object."""

        class RequestAwareForm(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest) -> dict:
                return {"name": request.method}

        request = HttpRequest()
        request.method = "GET"
        result = RequestAwareForm.get_initial(request)
        assert result == {"name": "GET"}

    def test_basemodelform_get_initial_returns_empty_dict(self) -> None:
        """BaseModelForm.get_initial returns empty dict by default."""
        assert BaseModelForm.get_initial() == {}

    def test_modelform_get_initial_returns_empty_dict(self) -> None:
        """ModelForm.get_initial returns empty dict by default."""
        assert ModelForm.get_initial() == {}

    def test_form_class_without_get_initial_raises_error_in_context(self) -> None:
        """Form class without get_initial raises TypeError when lazy context runs."""

        class CustomDjangoForm(django_forms.Form):
            name = django_forms.CharField(max_length=100)

        with pytest.raises(
            TypeError, match=r"^CustomDjangoForm has no get_initial method"
        ):
            _form_action_context_callable(CustomDjangoForm)(HttpRequest())

    def test_form_with_model_instance_but_not_modelform_raises_error(self) -> None:
        """Using instance parameter with non-ModelForm raises TypeError."""

        class CustomForm(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest) -> object:
                return Group(name="editors")

        request = HttpRequest()
        with pytest.raises(
            TypeError, match=r"CustomForm is not a ModelForm\. Subclass next\.forms"
        ):
            _form_action_context_callable(CustomForm)(request)

    def test_context_func_with_modelform_returning_instance(self) -> None:
        """context_func creates form with instance when ModelForm returns instance."""
        mock_model = MagicMock()
        mock_model._meta = MagicMock()
        mock_model._meta.get_fields.return_value = []

        class TestModelForm(ModelForm):
            name = forms.CharField(max_length=100)

            class Meta:
                model = mock_model
                fields: ClassVar[list[str]] = ["name"]

            @classmethod
            def get_initial(cls, request: HttpRequest) -> object:
                mock_instance = MagicMock()
                mock_instance._meta = MagicMock()
                mock_instance._meta.model = mock_model
                return mock_instance

        request = HttpRequest()
        result = _form_action_context_callable(TestModelForm)(request)
        assert hasattr(result, "form")
        assert result.form is not None

    def test_context_func_gets_url_kwargs_from_origin_when_no_resolver_match(
        self,
    ) -> None:
        """context_func resolves the posted origin when resolver_match is absent."""

        class FormWithId(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest, **kwargs) -> dict:
                return {"name": f"from-{kwargs['id']}"}

        request = HttpRequest()
        request.method = "POST"
        request.POST = {"_next_form_origin": "/items/42/"}
        result = _form_action_context_callable(FormWithId)(request)
        assert hasattr(result, "form")
        assert result.form.initial.get("name") == "from-42"

    def test_context_func_gets_url_kwargs_from_resolver_match(self) -> None:
        """context_func uses resolver_match.kwargs when present."""

        class FormWithId(Form):
            name = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest, item_id: int) -> dict:
                return {"name": f"resolver-{item_id}"}

        request = HttpRequest()
        request.resolver_match = MagicMock()
        request.resolver_match.kwargs = {"item_id": 7}
        result = _form_action_context_callable(FormWithId)(request)
        assert result.form.initial.get("name") == "resolver-7"

    def test_context_func_origin_string_kwarg_stays_a_string(self) -> None:
        """A string URL converter value reaches get_initial untouched."""

        class FormWithName(Form):
            slug = forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest, name: str) -> dict:
                return {"slug": name}

        request = HttpRequest()
        request.method = "POST"
        request.POST = {"_next_form_origin": "/groups/my-slug/"}
        result = _form_action_context_callable(FormWithName)(request)
        assert result.form.initial.get("slug") == "my-slug"


def _atomic_handler(request: HttpRequest) -> HttpResponseRedirect:
    return HttpResponseRedirect("/")


class TestActionOptions:
    """``@action`` refuses a keyword it does not take, as a plain function would."""

    def test_a_misspelled_option_raises_type_error(self) -> None:
        with pytest.raises(
            TypeError,
            match=r"^action\(\) got an unexpected keyword argument 'form_klass'$",
        ):
            action_decorator("misspelled_option", form_klass=Form)
        assert form_action_manager.default_backend.get_meta("misspelled_option") is None

    def test_the_first_unknown_option_is_named(self) -> None:
        with pytest.raises(TypeError, match="'atomik'"):
            action_decorator("two_unknown", scope="shared", atomik=True, trnsport="any")


class TestActionAtomic:
    """``@action(atomic=...)`` stores the transaction choice on the action metadata."""

    def test_no_choice_stores_nothing(self) -> None:
        action_decorator("atomic_unset")(_atomic_handler)
        meta = form_action_manager.default_backend.get_meta("atomic_unset")
        assert meta is not None
        assert "atomic" not in meta

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, AtomicOverride(atomic=True)),
            (False, AtomicOverride(atomic=False)),
            (
                AtomicOverride(atomic=True, aliases=["default"]),
                AtomicOverride(atomic=True, aliases=("default",)),
            ),
            (
                AtomicOverride(atomic=True, aliases="all"),
                AtomicOverride(atomic=True, aliases="all"),
            ),
        ],
    )
    def test_a_usable_choice_is_stored(
        self, value: object, expected: AtomicOverride
    ) -> None:
        action_decorator("atomic_set", atomic=value)(_atomic_handler)
        meta = form_action_manager.default_backend.get_meta("atomic_set")
        assert meta is not None
        assert meta["atomic"] == expected

    @pytest.mark.parametrize(
        ("value", "fragment"),
        [
            ("yes", "atomic must be True, False, None or an AtomicOverride"),
            (AtomicOverride(atomic=1), "atomic must be True or False"),
            (AtomicOverride(atomic=True, aliases="default"), "aliases must be"),
        ],
    )
    def test_an_unusable_choice_is_recorded_and_dropped(
        self, value: object, fragment: str
    ) -> None:
        action_decorator("atomic_bad", atomic=value)(_atomic_handler)
        owner, problem = registration_diagnostics.invalid_atomic[-1]
        assert "_atomic_handler" in owner
        assert fragment in problem
        meta = form_action_manager.default_backend.get_meta("atomic_bad")
        assert meta is not None
        assert "atomic" not in meta


class _QuietForm(Form):
    """An abstract form whose actions run in autocommit."""

    email = forms.EmailField()

    class Meta:
        abstract = True
        atomic = False


class _QuietMixin(_QuietForm):
    """A form that inherits `Meta.atomic` through the inherited `Meta`."""

    class Meta(_QuietForm.Meta):
        abstract = True


class _MisfitForm(Form):
    """An abstract form whose `Meta.atomic` is no usable choice."""

    class Meta:
        abstract = True
        atomic = "yes"


class TestActionInheritsMetaAtomic:
    """``@action(form_class=F)`` without ``atomic=`` takes ``F.Meta.atomic``."""

    @pytest.mark.parametrize("form_class", [_QuietForm, _QuietMixin])
    def test_the_form_class_decides_without_an_atomic_option(
        self, form_class: type[Form]
    ) -> None:
        action_decorator("inherits_atomic", form_class=form_class)(_atomic_handler)
        meta = form_action_manager.default_backend.get_meta("inherits_atomic")
        assert meta is not None
        assert meta["atomic"] == AtomicOverride(atomic=False)

    def test_the_atomic_option_wins(self) -> None:
        action_decorator("overrides_atomic", form_class=_QuietForm, atomic=True)(
            _atomic_handler
        )
        meta = form_action_manager.default_backend.get_meta("overrides_atomic")
        assert meta is not None
        assert meta["atomic"] == AtomicOverride(atomic=True)

    def test_an_explicit_none_keeps_the_method_rule(self) -> None:
        action_decorator("method_rule_atomic", form_class=_QuietForm, atomic=None)(
            _atomic_handler
        )
        meta = form_action_manager.default_backend.get_meta("method_rule_atomic")
        assert meta is not None
        assert "atomic" not in meta

    def test_a_factory_form_class_declares_nothing(self) -> None:
        action_decorator("factory_atomic", form_class=lambda: _QuietForm)(
            _atomic_handler
        )
        meta = form_action_manager.default_backend.get_meta("factory_atomic")
        assert meta is not None
        assert "atomic" not in meta

    def test_an_unusable_meta_atomic_is_recorded_and_dropped(self) -> None:
        action_decorator("misfit_atomic", form_class=_MisfitForm)(_atomic_handler)
        owner, problem = registration_diagnostics.invalid_atomic[-1]
        assert owner == "Meta.atomic of '_MisfitForm'"
        assert "atomic must be True, False, None or an AtomicOverride" in problem
        meta = form_action_manager.default_backend.get_meta("misfit_atomic")
        assert meta is not None
        assert "atomic" not in meta
