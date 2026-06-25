"""Form widgets that render through next-component runtime."""

import difflib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, override

from django import forms as django_forms
from django.conf import settings
from django.forms import widgets as _django_widgets
from django.forms.renderers import BaseRenderer
from django.http import HttpRequest
from django.utils.safestring import SafeString

from next.components.facade import get_component, render_component
from next.components.manager import components_manager
from next.static import StaticCollector, collect_component_assets


if TYPE_CHECKING:
    from django.forms.utils import ErrorList

    from next.components.info import ComponentInfo


# Per-request component lookup cache attached to the request object, mirroring
# REQUEST_DEP_CACHE_ATTR in next.deps. Keyed by (component name, anchor path).
COMPONENT_LOOKUP_CACHE_ATTR: Final[str] = "_next_component_lookup_cache"


def _unregistered_component_error(name: str, anchor: "str | Path") -> RuntimeError:
    """Build the render-time error for a component name that resolves to nothing."""
    visible = sorted(components_manager.collect_visible_components(Path(anchor)))
    matches = difflib.get_close_matches(name, visible)
    msg = (
        f"ComponentWidget references component {name!r} that is not "
        f"registered. Searched from {anchor}. Create {name}.djx in a "
        "_components directory visible from that path, or register the "
        "component through a components backend."
    )
    if matches:
        rendered = ", ".join(repr(match) for match in matches)
        msg = f"{msg} Closest matches: {rendered}."
    return RuntimeError(msg)


class ComponentWidget(django_forms.Widget):
    """A form widget that renders a registered next-component."""

    _template_path: "str | Path | None" = None
    _request: HttpRequest | None = None
    _errors: "ErrorList | tuple[()]" = ()
    _static_collector: StaticCollector | None = None

    def __init__(
        self,
        component_name: str,
        *,
        attrs: dict[str, Any] | None = None,
        **component_kwargs: object,
    ) -> None:
        """Store the target component name and its extra render kwargs."""
        self.component_name = component_name
        self.extra_kwargs = component_kwargs
        super().__init__(attrs)

    def _resolve_component(self, anchor: "str | Path") -> "ComponentInfo | None":
        """Resolve the named component, cached per request when one is bound."""
        request = self._request
        cache: dict[tuple[str, str], ComponentInfo] | None = None
        key = (self.component_name, str(anchor))
        if request is not None:
            cache = getattr(request, COMPONENT_LOOKUP_CACHE_ATTR, None)
            if cache is None:
                cache = {}
                setattr(request, COMPONENT_LOOKUP_CACHE_ATTR, cache)
            cached = cache.get(key)
            if cached is not None:
                return cached
        info = get_component(self.component_name, Path(anchor))
        if cache is not None and info is not None:
            cache[key] = info
        return info

    @override
    def render(
        self,
        name: str,
        value: object,
        attrs: dict[str, Any] | None = None,
        renderer: BaseRenderer | None = None,
    ) -> SafeString:
        """Resolve the component within scope and render it to HTML."""
        del renderer
        anchor = (
            self._template_path or getattr(settings, "BASE_DIR", None) or Path.cwd()
        )
        info = self._resolve_component(anchor)
        if info is None:
            raise _unregistered_component_error(self.component_name, anchor)
        collect_component_assets(info, self._static_collector)
        merged = self.build_attrs(self.attrs, attrs or {})
        # Hyphenated keys such as aria-invalid cannot be read as template vars, so
        # alias them to an underscore form (aria_invalid) unless that name already
        # exists as a real attr. The raw merged mapping stays under "attrs" so
        # template iteration still sees the hyphenated originals. The shared
        # input/textarea components read identifier-named vars and are also used
        # directly via {% component %}, so spreading merged and extra_kwargs to the
        # top level keeps those templates rendering. attrs, name, value, and errors
        # win over any same-named extra kwarg.
        aliased = {
            k.replace("-", "_"): v
            for k, v in merged.items()
            if "-" in k and k.replace("-", "_") not in merged
        }
        context = {
            **merged,
            **aliased,
            **self.extra_kwargs,
            "attrs": merged,
            "name": name,
            "value": self.format_value(value),
            "errors": self._errors,
        }
        # render_component returns template-rendered, already-escaped HTML, so a
        # SafeString wrapper matches the Widget.render contract without re-escaping.
        html = render_component(info, context, request=self._request)
        return SafeString(html)


def bind_component_widgets(
    form: "django_forms.BaseForm | django_forms.BaseFormSet",
    *,
    template_path: str | Path | None,
    request: HttpRequest | None = None,
    collector: StaticCollector | None = None,
    with_errors: bool = False,
) -> None:
    """Inject scope path, request, collector, and field errors onto ComponentWidgets.

    A formset has no `fields` of its own, so each of its member forms is
    bound instead.
    """
    if isinstance(form, django_forms.BaseFormSet):
        for member in form.forms:
            bind_component_widgets(
                member,
                template_path=template_path,
                request=request,
                collector=collector,
                with_errors=with_errors,
            )
        return
    for field_name, field in form.fields.items():
        widget = getattr(field, "widget", None)
        if not isinstance(widget, ComponentWidget):
            continue
        # Django deep-copies base_fields and their widgets per form instance, so
        # these attributes stay scoped to this form and never leak across forms.
        widget._template_path = template_path
        widget._request = request
        widget._static_collector = collector
        if with_errors:
            widget._errors = form[field_name].errors


_MISSING = object()


def __getattr__(name: str) -> object:
    """Resolve public `django.forms.widgets` names that next.dj does not override."""
    if not name.startswith("_"):
        value = getattr(_django_widgets, name, _MISSING)
        if value is not _MISSING:
            return value
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    """List the curated surface plus the public `django.forms.widgets` namespace."""
    django_public = {n for n in dir(_django_widgets) if not n.startswith("_")}
    return sorted(set(__all__) | django_public)


__all__ = ["ComponentWidget", "bind_component_widgets"]
