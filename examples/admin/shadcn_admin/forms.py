from dataclasses import dataclass
from typing import Any

from django import forms as django_forms
from django.contrib import messages
from django.contrib.admin.options import InlineModelAdmin, ModelAdmin
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Model, QuerySet
from django.forms.models import BaseInlineFormSet
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    QueryDict,
)

from next.deps import Depends, resolver
from next.forms import BaseModelForm, action, cleanup_extra_initial, form_spec
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
    """Build the inline formsets for the spec, skipping any absent from a POST."""
    formsets: list[BaseInlineFormSet] = []
    parent = spec.instance
    is_post = spec.request.method == "POST"
    for inline in spec.model_admin.get_inline_instances(spec.request, parent):
        formset_cls = inline.get_formset(spec.request, parent)
        prefix = formset_cls.get_default_prefix()
        if is_post:
            if f"{prefix}-TOTAL_FORMS" not in spec.request.POST:
                continue
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


@dataclass(frozen=True, slots=True)
class RelatedRow:
    """One existing related row, rendered as a keyed `admin:inline_change` form."""

    pk: int
    fields: tuple[Any, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RelatedSection:
    """One inline's live editing block: keyed rows plus a single add form."""

    token: str
    verbose_name: str
    verbose_name_plural: str
    add_label: str
    rows: tuple[RelatedRow, ...]
    add_fields: tuple[Any, ...]
    add_errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdminInlineSpec:
    """One inline of a parent admin, with the helpers its keyed row forms need."""

    spec: AdminFormSpec
    inline: InlineModelAdmin

    @classmethod
    def resolve(cls, spec: AdminFormSpec, token: str) -> "AdminInlineSpec":
        """Wrap the inline whose child model matches `token`, or 404."""
        for inline_cls in spec.model_admin.inlines:
            inline = inline_cls(spec.model_admin.model, spec.model_admin.admin_site)
            if inline.model._meta.model_name == token:
                return cls(spec, inline)
        msg = f"No inline {token!r} on {spec.app_label}.{spec.model_name}."
        raise Http404(msg)

    @classmethod
    def for_request(cls, spec: AdminFormSpec) -> "AdminInlineSpec":
        """Resolve by the POST `_inline` token, or the first inline on a render."""
        token = spec.request.POST.get("_inline", "")
        if token:
            return cls.resolve(spec, token)
        inlines = spec.model_admin.get_inline_instances(spec.request, spec.instance)
        if not inlines:  # pragma: no cover
            msg = f"No inlines on {spec.app_label}.{spec.model_name}."
            raise Http404(msg)
        return cls(spec, inlines[0])

    @classmethod
    def build_sections(cls, spec: AdminFormSpec) -> list[RelatedSection]:
        """Live editing blocks for every inline of the change view."""
        request = spec.request
        is_post = request.method == "POST"
        token = request.POST.get("_inline", "") if is_post else ""
        target_pk = request.POST.get("_inline_pk", "") if is_post else ""
        return [
            cls(spec, inline)._section(token, target_pk)
            for inline in spec.model_admin.get_inline_instances(request, spec.instance)
        ]

    @property
    def token(self) -> str:
        """The child model's name, the `_inline` discriminator on the wire."""
        return self.inline.model._meta.model_name

    @property
    def _formset(self) -> type[BaseInlineFormSet]:
        return self.inline.get_formset(self.spec.request, self.spec.instance)

    @property
    def fk_name(self) -> str:
        """Name of the foreign key tying a child back to the parent."""
        return self._formset.fk.name

    def children(self) -> QuerySet[Model]:
        """Return the existing related rows under the parent."""
        return self.inline.model._default_manager.filter(
            **{self.fk_name: self.spec.instance}
        )

    def object(self) -> Model | None:
        """Return the row named by `_inline_pk`, None if unnamed, 404 if missing."""
        pk = self.spec.request.POST.get("_inline_pk")
        if not pk:
            return None
        obj = self.children().filter(pk=pk).first()
        if obj is None:
            msg = f"No {self.token} row {pk!r} under this parent."
            raise Http404(msg)
        return obj

    def blank(self) -> Model:
        """Return a new, unsaved child already tied to the parent."""
        return self.inline.model(**{self.fk_name: self.spec.instance})

    def __call__(self, instance: Model | None) -> type[django_forms.ModelForm]:
        """Build the row `ModelForm`, bound to `instance` on dispatch."""
        base = self._formset.form

        class InlineRowForm(base, BaseModelForm):  # type: ignore[misc, valid-type]
            @classmethod
            def get_initial(cls) -> Model | None:
                return instance

        InlineRowForm.__name__ = f"Inline{base.__name__}"
        return InlineRowForm

    def has_change_permission(self) -> bool:
        """Return whether the request may edit this inline's rows."""
        return self.inline.has_change_permission(self.spec.request, self.spec.instance)

    def has_add_permission(self) -> bool:
        """Return whether the request may add a row to this inline."""
        return self.inline.has_add_permission(self.spec.request, self.spec.instance)

    def _bind(self, instance: Model, post: QueryDict | None) -> django_forms.ModelForm:
        form_cls = self._formset.form
        if post is None:
            return form_cls(instance=instance)
        form = form_cls(post, instance=instance)
        form.is_valid()
        return form

    def _row(self, obj: Model, post: QueryDict | None) -> RelatedRow:
        spec = form_spec(self._bind(obj, post))
        return RelatedRow(
            pk=obj.pk,
            fields=spec.sections[0].fields,
            errors=spec.non_field_errors,
        )

    def _section(self, token: str, target_pk: str) -> RelatedSection:
        active = self.token == token
        post = self.spec.request.POST
        rows = tuple(
            self._row(obj, post if active and target_pk == str(obj.pk) else None)
            for obj in self.children()
        )
        add_spec = form_spec(
            self._bind(self.blank(), post if active and not target_pk else None)
        )
        verbose = str(self.inline.model._meta.verbose_name)
        return RelatedSection(
            token=self.token,
            verbose_name=verbose,
            verbose_name_plural=str(self.inline.model._meta.verbose_name_plural),
            add_label=f"Add {verbose}",
            rows=rows,
            add_fields=add_spec.sections[0].fields,
            add_errors=add_spec.non_field_errors,
        )


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


def admin_inline_change_form_factory(
    admin_spec: AdminFormSpec = Depends("admin_spec"),
) -> type[django_forms.Form]:
    """Form class editing the inline row named by the POST, blank on a render."""
    inline = AdminInlineSpec.for_request(admin_spec)
    child = inline.object() if admin_spec.request.method == "POST" else None
    return inline(child)


def admin_inline_add_form_factory(
    admin_spec: AdminFormSpec = Depends("admin_spec"),
) -> type[django_forms.Form]:
    """Form class creating a new inline row under the parent."""
    inline = AdminInlineSpec.for_request(admin_spec)
    return inline(inline.blank())


def _save_inline(
    form: django_forms.ModelForm,
    spec: AdminFormSpec,
    *,
    verb: str,
) -> HttpResponse:
    obj = form.save()
    messages.success(
        spec.request,
        f"The {obj._meta.verbose_name} was {verb} successfully.",
    )
    return HttpResponseRedirect(spec.change_url(spec.instance))


@action(
    "admin:inline_change",
    form_class=admin_inline_change_form_factory,
    login_required=True,
)
def handle_inline_change(
    form: django_forms.ModelForm,
    spec: AdminFormSpec = Depends("admin_spec"),
) -> HttpResponse:
    """Save one edited inline row, then redirect back to the parent change view."""
    if not AdminInlineSpec.for_request(spec).has_change_permission():
        raise PermissionDenied
    return _save_inline(form, spec, verb="updated")


@action(
    "admin:inline_add",
    form_class=admin_inline_add_form_factory,
    login_required=True,
)
def handle_inline_add(
    form: django_forms.ModelForm,
    spec: AdminFormSpec = Depends("admin_spec"),
) -> HttpResponse:
    """Create one inline row under the parent, then redirect back to its change view."""
    if not AdminInlineSpec.for_request(spec).has_add_permission():
        raise PermissionDenied
    return _save_inline(form, spec, verb="added")


@action("admin:logout")
def admin_logout(request: HttpRequest) -> HttpResponse:
    """Log the user out and land on the signed-out page."""
    logout(request)
    return HttpResponseRedirect(utils.logout_url())
