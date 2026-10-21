from typing import ClassVar, cast

from django import forms as django_forms
from django.contrib import admin
from django.contrib.auth import get_permission_codename
from django.db.models import Model
from django.http import HttpRequest


admin.site.disable_action("delete_selected")


def may(request: HttpRequest, verb: str, model: type[Model], obj: Model | None) -> bool:
    """Ask `user.has_perm` for `verb` on `model`, passing the row when there is one."""
    opts = model._meta
    return request.user.has_perm(
        f"{opts.app_label}.{get_permission_codename(verb, opts)}", obj
    )


class RowPermissionForm(django_forms.ModelForm):
    """Refuse a save whose resulting row the admin would no longer let the user change.

    `RowPermissionAdmin.get_form` mixes it into the form it builds, with its request.
    """

    row_admin: ClassVar["RowPermissionAdmin"]
    row_request: ClassVar[HttpRequest]
    row_editable: ClassVar[bool]

    def full_clean(self) -> None:
        """Clean the form, then refuse a row the user may not change once saved."""
        super().full_clean()
        if not self.is_bound or self.errors or not self.row_editable:
            return
        if not self.row_admin.has_change_permission(self.row_request, self.instance):
            self.add_error(None, self.row_admin.refusal(self.instance))


class RowPermissionAdmin(admin.ModelAdmin):
    """Pass the row to `has_perm`, which the default `ModelAdmin` checks leave out."""

    def has_change_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Ask whether the user may change `obj`, or any row when it is `None`."""
        return may(request, "change", self.model, obj)

    def has_delete_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Ask whether the user may delete `obj`, or any row when it is `None`."""
        return may(request, "delete", self.model, obj)

    def get_form(
        self,
        request: HttpRequest,
        obj: Model | None = None,
        *,
        change: bool = False,
        **kwargs,
    ) -> type[django_forms.ModelForm]:
        """Return the admin form with `RowPermissionForm` mixed in for this request."""
        base = super().get_form(request, obj, change=change, **kwargs)
        bound = {
            "row_admin": self,
            "row_request": request,
            "row_editable": obj is None or self.has_change_permission(request, obj),
        }
        form = type(base.__name__, (RowPermissionForm, base), bound)
        return cast("type[django_forms.ModelForm]", form)

    def get_object_links(
        self, request: HttpRequest, obj: Model
    ) -> list[tuple[str, str]]:
        """Return the `(label, url)` pages about `obj` its change view links to."""
        return []

    def refusal(self, obj: Model) -> str:
        """Return the form error for a save the user may not make."""
        return f"You may not save this {obj._meta.verbose_name} in this state."


class ParentRowPermissions:
    """Grant an inline's rows the right to change the parent row, asked with the row."""

    parent_model: type[Model]

    def has_add_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Let rows be added only under a parent the user may change."""
        return may(request, "change", self.parent_model, obj)

    def has_change_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Lock the rows of a parent the user may not change."""
        return may(request, "change", self.parent_model, obj)

    def has_delete_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Lock the rows of a parent the user may not change."""
        return may(request, "change", self.parent_model, obj)
