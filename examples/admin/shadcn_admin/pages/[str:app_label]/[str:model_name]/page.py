from typing import Any

from django.contrib.admin.utils import (
    display_for_field,
    display_for_value,
    label_for_field,
    lookup_field,
)
from django.db.models import Field
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.safestring import SafeString, mark_safe
from shadcn_admin.utils import resolve_model_admin

from next.forms import action
from next.pages import context


_EMPTY = mark_safe("&mdash;")


@context("changelist_state")
def changelist_state(
    request: HttpRequest,
    app_label: str,
    model_name: str,
) -> dict[str, Any]:
    """Render changelist data from `ModelAdmin.get_changelist_instance(request)`."""
    model, model_admin = resolve_model_admin(app_label, model_name)
    cl = model_admin.get_changelist_instance(request)

    # `cl.list_display` adds "action_checkbox" when actions are registered;
    # we render the selection column ourselves through `selectable=`.
    visible_columns = [n for n in cl.list_display if n != "action_checkbox"]

    base_params = {k: v for k, v in cl.params.items() if k != "o"}
    ordering_columns = cl.get_ordering_field_columns()
    sortable_by = cl.sortable_by

    columns: list[dict[str, Any]] = []
    for index, name in enumerate(visible_columns):
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

    rows: list[dict[str, Any]] = []
    for obj in cl.result_list:
        cells: list[str | SafeString] = []
        for name in visible_columns:
            try:
                field, _attr, value = lookup_field(name, obj, model_admin)
            except (AttributeError, ValueError):  # pragma: no cover
                cells.append(_EMPTY)
                continue
            if value is None:
                cells.append(_EMPTY)
            elif isinstance(field, Field):
                cells.append(display_for_field(value, field, "—"))
            else:  # pragma: no cover
                cells.append(display_for_value(value, "—"))
        rows.append(
            {
                "pk": obj.pk,
                "cells": cells,
                "change_url": f"/admin/{app_label}/{model_name}/{obj.pk}/change/",
            }
        )

    page_num = cl.page_num
    num_pages = cl.paginator.num_pages

    def _page_url(p: int) -> str:
        return "?" + "&".join(f"{k}={v}" for k, v in {**cl.params, "p": str(p)}.items())

    return {
        "app_label": app_label,
        "model_name": model_name,
        "verbose_name": str(model._meta.verbose_name),
        "verbose_name_plural": str(model._meta.verbose_name_plural),
        "add_url": f"/admin/{app_label}/{model_name}/add/",
        "post_url": f"/admin/{app_label}/{model_name}/",
        "columns": columns,
        "rows": rows,
        "pagination": {
            "count": cl.result_count,
            "full_count": cl.full_result_count,
            "page": page_num,
            "num_pages": num_pages,
            "has_previous": page_num > 1,
            "has_next": page_num < num_pages,
            "previous_url": _page_url(page_num - 1) if page_num > 1 else None,
            "next_url": _page_url(page_num + 1) if page_num < num_pages else None,
        },
        "filters": [
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
        ],
        "actions": [
            {
                "name": name,
                "description": str(description)
                % {
                    "verbose_name": str(model._meta.verbose_name),
                    "verbose_name_plural": str(model._meta.verbose_name_plural),
                },
            }
            for name, (_func, _name, description) in model_admin.get_actions(
                request
            ).items()
        ],
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
    """Run a Django admin bulk action against the selected queryset."""
    _, model_admin = resolve_model_admin(app_label, model_name)
    response = model_admin.response_action(
        request,
        queryset=model_admin.get_queryset(request),
    )
    if response is None:
        return HttpResponseRedirect(f"/admin/{app_label}/{model_name}/")
    return response  # pragma: no cover
