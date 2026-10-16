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
        """Refuse a save whose resulting row the user would no longer be let change.

        A row the user may not change already renders without fields, and its handler
        answers `403`, so the check runs only on rows the user may edit.
        """
        base = super().get_form(request, obj, change=change, **kwargs)
        model_admin = self
        editable = obj is None or self.has_change_permission(request, obj)

        class RowPermissionForm(base):  # type: ignore[misc, valid-type]
            def _post_clean(self) -> None:
                super()._post_clean()
                if self.errors or not editable:
                    return
                if not model_admin.has_change_permission(request, self.instance):
                    self.add_error(None, model_admin.refusal(self.instance))

        RowPermissionForm.__name__ = base.__name__
        return RowPermissionForm

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
