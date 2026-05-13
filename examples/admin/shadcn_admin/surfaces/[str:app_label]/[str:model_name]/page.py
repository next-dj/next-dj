from typing import Any

from django.contrib.admin.options import ModelAdmin
from django.contrib.admin.utils import (
    display_for_field,
    display_for_value,
    label_for_field,
    lookup_field,
)
from django.contrib.admin.views.main import ChangeList
from django.db.models import Field, Model
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.safestring import SafeString, mark_safe
from shadcn_admin import utils

from next.forms import action
from next.pages import context


_EMPTY = mark_safe("&mdash;")


def _visible_columns(cl: ChangeList) -> list[str]:
    # `cl.list_display` adds "action_checkbox" when actions are registered;
    # we render the selection column ourselves through `selectable=`.
    return [n for n in cl.list_display if n != "action_checkbox"]


def _columns(
    cl: ChangeList,
    visible: list[str],
    model: type[Model],
    model_admin: ModelAdmin,
) -> list[dict[str, Any]]:
    base_params = {k: v for k, v in cl.params.items() if k != "o"}
    ordering_columns = cl.get_ordering_field_columns()
    sortable_by = cl.sortable_by
    columns: list[dict[str, Any]] = []
    for index, name in enumerate(visible):
        is_sortable = sortable_by is None or name in sortable_by
        direction = ordering_columns.get(index)
        prefix = "-" if direction == "asc" else ""
        sort_params = {**base_params, "o": f"{prefix}{index}"} if is_sortable else None
        columns.append(
            {
                "name": name,
                "label": str(label_for_field(name, model, model_admin=model_admin)),
                "sortable": is_sortable,
                "direction": direction,
                "sort_url": (
                    "?" + "&".join(f"{k}={v}" for k, v in sort_params.items())
                    if sort_params is not None
                    else None
                ),
            }
        )
    return columns


def _row_cell(
    name: str,
    obj: Model,
    model_admin: ModelAdmin,
) -> str | SafeString:
    try:
        field, _attr, value = lookup_field(name, obj, model_admin)
    except (AttributeError, ValueError):  # pragma: no cover
        return _EMPTY
    if value is None:
        return _EMPTY
    if isinstance(field, Field):
        return display_for_field(value, field, "—")
    return display_for_value(value, "—")  # pragma: no cover


def _rows(
    cl: ChangeList,
    visible: list[str],
    model_admin: ModelAdmin,
    app_label: str,
    model_name: str,
) -> list[dict[str, Any]]:
    return [
        {
            "pk": obj.pk,
            "cells": [_row_cell(name, obj, model_admin) for name in visible],
            "change_url": utils.change_url(app_label, model_name, obj.pk),
        }
        for obj in cl.result_list
    ]


def _pagination(cl: ChangeList) -> dict[str, Any]:
    page_num = cl.page_num
    num_pages = cl.paginator.num_pages

    def page_url(p: int) -> str:
        return "?" + "&".join(f"{k}={v}" for k, v in {**cl.params, "p": str(p)}.items())

    return {
        "count": cl.result_count,
        "full_count": cl.full_result_count,
        "page": page_num,
        "num_pages": num_pages,
        "has_previous": page_num > 1,
        "has_next": page_num < num_pages,
        "previous_url": page_url(page_num - 1) if page_num > 1 else None,
        "next_url": page_url(page_num + 1) if page_num < num_pages else None,
    }


def _filters(cl: ChangeList) -> list[dict[str, Any]]:
    return [
        {
            "title": str(spec.title),
            "choices": [
                {
                    "label": c["display"],
                    "selected": bool(c["selected"]),
                    "url": c["query_string"],
                }
                for c in spec.choices(cl)
            ],
        }
        for spec in (cl.filter_specs or [])
    ]


def _actions(
    model_admin: ModelAdmin,
    model: type[Model],
    request: HttpRequest,
) -> list[dict[str, Any]]:
    placeholders = {
        "verbose_name": str(model._meta.verbose_name),
        "verbose_name_plural": str(model._meta.verbose_name_plural),
    }
    return [
        {"name": name, "description": str(description) % placeholders}
        for name, (_func, _name, description) in model_admin.get_actions(
            request
        ).items()
    ]


@context("changelist_state")
def changelist_state(
    request: HttpRequest,
    app_label: str,
    model_name: str,
) -> dict[str, Any]:
    """Render changelist data from `ModelAdmin.get_changelist_instance(request)`."""
    model, model_admin = utils.resolve_model_admin(app_label, model_name)
    cl = model_admin.get_changelist_instance(request)
    visible = _visible_columns(cl)
    return {
        "app_label": app_label,
        "model_name": model_name,
        "verbose_name": str(model._meta.verbose_name),
        "verbose_name_plural": str(model._meta.verbose_name_plural),
        "add_url": utils.add_url(app_label, model_name),
        "post_url": utils.changelist_url(app_label, model_name),
        "columns": _columns(cl, visible, model, model_admin),
        "rows": _rows(cl, visible, model_admin, app_label, model_name),
        "pagination": _pagination(cl),
        "filters": _filters(cl),
        "actions": _actions(model_admin, model, request),
        "search_enabled": bool(cl.search_fields),
        "query": cl.query,
        "carried_params": [(k, v) for k, v in cl.params.items() if k not in ("q", "p")],
        "url_params": [("app_label", app_label), ("model_name", model_name)],
        "has_change_permission": model_admin.has_change_permission(request),
        "has_add_permission": model_admin.has_add_permission(request),
    }


@action("admin:bulk_action")
def bulk_action(
    request: HttpRequest,
    app_label: str,
    model_name: str,
) -> HttpResponse:
    """Run a Django admin bulk action against the selected queryset.

    `response_action` returns either `None` (action handled, fall through)
    or an `HttpResponse`. For action functions that themselves return
    `None`, Django wraps the result in
    `HttpResponseRedirect(request.get_full_path())` — i.e. the form-action
    URL, which only accepts POST. We replace any such redirect with one
    pointing at the changelist so a user (or test client following the
    302) lands on a real GET endpoint.
    """
    _, model_admin = utils.resolve_model_admin(app_label, model_name)
    response = model_admin.response_action(
        request,
        queryset=model_admin.get_queryset(request),
    )
    if response is None or isinstance(response, HttpResponseRedirect):
        return HttpResponseRedirect(utils.changelist_url(app_label, model_name))
    return response  # pragma: no cover
