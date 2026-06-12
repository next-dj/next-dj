from dataclasses import dataclass
from typing import Any

from django import forms as django_forms
from django.contrib import messages
from django.contrib.admin.options import ModelAdmin
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Model
from django.forms.models import BaseInlineFormSet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from next.deps import Depends, resolver
from next.forms import BaseModelForm, action, cleanup_extra_initial
from shadcn_admin import utils


_LOG_CHANGE_MESSAGE = "Changed via shadcn-admin form"


@dataclass(frozen=True, slots=True)
class AdminFormSpec:
    """Frozen bundle of `(model, ModelAdmin, instance)` shared per request."""

    request: HttpRequest
    app_label: str
    model_name: str
    model: type[Model]
    model_admin: ModelAdmin
    instance: Model | None

    @classmethod
    def resolve(
        cls,
        request: HttpRequest,
        app_label: str,
        model_name: str,
        pk: int | None = None,
    ) -> "AdminFormSpec":
        """Look up `(model, ModelAdmin, instance)` once and freeze them into a spec."""
        if pk is None:
            model, model_admin = utils.resolve_model_admin(app_label, model_name)
            instance: Model | None = None
        else:
            model, model_admin, instance = utils.resolve_object_or_404(
                request, app_label, model_name, pk
            )
        return cls(
            request=request,
            app_label=app_label,
            model_name=model_name,
            model=model,
            model_admin=model_admin,
            instance=instance,
        )

    @property
    def is_change(self) -> bool:
        """`True` for the change view (`instance` is set), `False` for add."""
        return self.instance is not None

    @property
    def changelist_url(self) -> str:
        """URL of the changelist this form returns to on plain Save."""
        return utils.changelist_url(self.app_label, self.model_name)

    @property
    def add_url(self) -> str:
        """URL of the add view (used by Save-and-add-another)."""
        return utils.add_url(self.app_label, self.model_name)

    def change_url(self, obj: Model) -> str:
        """URL of the change view for the just-saved object."""
        return utils.change_url(self.app_label, self.model_name, obj.pk)

    @property
    def delete_url(self) -> str | None:
        """URL of the delete view when this spec carries an instance."""
        if self.instance is None:
            return None
        return utils.delete_url(self.app_label, self.model_name, self.instance.pk)


def build_inline_formsets(spec: AdminFormSpec) -> list[BaseInlineFormSet]:
    """Build the inline formsets for the spec, bound to POST when present."""
    formsets: list[BaseInlineFormSet] = []
    parent = spec.instance
    for inline in spec.model_admin.get_inline_instances(spec.request, parent):
        formset_cls = inline.get_formset(spec.request, parent)
        prefix = formset_cls.get_default_prefix()
        if spec.request.method == "POST":
            fs = formset_cls(
                spec.request.POST,
                spec.request.FILES,
                instance=parent,
                prefix=prefix,
            )
        else:
            fs = formset_cls(instance=parent, prefix=prefix)
        cleanup_extra_initial(fs)
        formsets.append(fs)
    return formsets


def _build_form_class(spec: AdminFormSpec) -> type[django_forms.Form]:
    """Wrap `ModelAdmin.get_form` so inline-formset errors surface on the main form.

    `AdminForm.clean()` builds the inline formsets a second time bound to
    the same POST data, calls `is_valid()` on each, and raises
    `ValidationError` if any inline row is broken. The dispatcher catches
    that through `form.is_valid() == False` and re-renders the origin page
    — `form_state` then rebuilds the formsets and shows the errors next to
    the bad rows.
    """
    base = spec.model_admin.get_form(spec.request, spec.instance, change=spec.is_change)

    class AdminForm(base, BaseModelForm):  # type: ignore[misc, valid-type]
        @classmethod
        def get_initial(cls) -> Model | None:
            return spec.instance

        def clean(self) -> dict[str, Any] | None:
            cleaned = super().clean()
            errors: list[ValidationError] = []
            for fs in build_inline_formsets(spec):
                if not fs.is_valid():
                    for inline_form in fs.forms:
                        for field_errors in inline_form.errors.values():
                            errors.extend(field_errors)
                    errors.extend(fs.non_form_errors())
            if errors:
                raise ValidationError(errors)
            return cleaned

    AdminForm.__name__ = f"Admin{base.__name__}"
    return AdminForm


@resolver.dependency("admin_spec")
def admin_spec(
    request: HttpRequest,
    app_label: str,
    model_name: str,
    pk: int | None = None,
) -> AdminFormSpec:
    """Resolve `(model, ModelAdmin, instance)` once per dispatch.

    Registered as a named dependency so `Depends("admin_spec")` shares a
    single resolution across factory, `get_initial`, action handler, and
    any re-render component context within the same POST.
    """
    return AdminFormSpec.resolve(request, app_label, model_name, pk=pk)


def admin_add_form_factory(
    admin_spec: AdminFormSpec = Depends("admin_spec"),
) -> type[django_forms.Form]:
    """Per-request form class for the add view."""
    return _build_form_class(admin_spec)


def admin_change_form_factory(
    admin_spec: AdminFormSpec = Depends("admin_spec"),
) -> type[django_forms.Form]:
    """Per-request form class for the change view."""
    return _build_form_class(admin_spec)


def _persist(
    form: django_forms.ModelForm,
    spec: AdminFormSpec,
    *,
    change: bool,
) -> HttpResponse:
    obj = form.save(commit=False)
    formsets = build_inline_formsets(spec)
    for fs in formsets:
        fs.is_valid()
    spec.model_admin.save_model(spec.request, obj, form, change=change)
    form.save_m2m()
    for fs in formsets:
        fs.instance = obj
        fs.save()
    if change:
        spec.model_admin.log_change(spec.request, obj, _LOG_CHANGE_MESSAGE)
    else:
        spec.model_admin.log_addition(spec.request, obj, [{"added": {}}])
    verb = "updated" if change else "added"
    messages.success(
        spec.request,
        f"The {spec.model._meta.verbose_name} {obj} was {verb} successfully.",
    )
    return _redirect_after_save(spec, obj)


def _redirect_after_save(spec: AdminFormSpec, obj: Model) -> HttpResponseRedirect:
    post = spec.request.POST
    if "_save_continue" in post:
        return HttpResponseRedirect(spec.change_url(obj))
    if "_save_addanother" in post:
        return HttpResponseRedirect(spec.add_url)
    return HttpResponseRedirect(spec.changelist_url)


@action("admin:add", form_class=admin_add_form_factory, login_required=True)
def handle_add(
    form: django_forms.ModelForm,
    spec: AdminFormSpec = Depends("admin_spec"),
) -> HttpResponse:
    """Save a new object and its inline formsets, then redirect."""
    if not spec.model_admin.has_add_permission(spec.request):
        raise PermissionDenied
    return _persist(form, spec, change=False)


@action("admin:change", form_class=admin_change_form_factory, login_required=True)
def handle_change(
    form: django_forms.ModelForm,
    spec: AdminFormSpec = Depends("admin_spec"),
) -> HttpResponse:
    """Persist main form and inline formsets via `ModelAdmin.save_model`."""
    if not spec.model_admin.has_change_permission(spec.request, spec.instance):
        raise PermissionDenied
    return _persist(form, spec, change=True)


@action("admin:logout")
def admin_logout(request: HttpRequest) -> HttpResponse:
    """Log the user out and land on the signed-out page."""
    logout(request)
    return HttpResponseRedirect(utils.logout_url())
