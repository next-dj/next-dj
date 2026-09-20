"""Form widgets that render through next-component runtime."""

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, override

from django import forms as django_forms
from django.forms import widgets as _django_widgets
from django.forms.renderers import BaseRenderer
from django.http import HttpRequest
from django.utils.safestring import SafeString

from next.components.facade import get_component, render_component
from next.components.manager import components_manager
from next.components.renderers import COMPONENT_PROPS_CONTEXT_KEY
from next.seeding import EMPTY_FRAME, RenderFrame, current_ambient_frame
from next.static import StaticCollector, collect_component_assets
from next.utils import resolve_base_dir

from .errors import UnregisteredComponentError


if TYPE_CHECKING:
    from django.forms.utils import ErrorList

    from next.components.info import ComponentInfo


# Per-request component lookup cache attached to the request object, mirroring
# REQUEST_DEP_CACHE_ATTR in next.deps. Keyed by (component name, anchor path).
COMPONENT_LOOKUP_CACHE_ATTR: Final[str] = "_next_component_lookup_cache"


_UNBOUND_ANCHOR_NAME: Final[str] = "<unbound-widget>"


def _project_anchor() -> Path:
    """Return the anchor a widget searches from when no render bound one.

    The name is synthetic, because a lookup walks outward from the directory of a file.
    """
    return (resolve_base_dir() or Path.cwd()) / _UNBOUND_ANCHOR_NAME


class ComponentWidget(django_forms.Widget):
    """A form widget that renders a registered next-component."""

    _frame: RenderFrame = EMPTY_FRAME
    _errors: "ErrorList | tuple[()]" = ()

    def __init__(
        self,
        component_name: str,
        *,
        attrs: dict[str, Any] | None = None,
        **component_kwargs,
    ) -> None:
        """Store the target component name and its extra render kwargs."""
        self.component_name = component_name
        self.extra_kwargs = component_kwargs
        super().__init__(attrs)

    def _render_frame(self) -> "tuple[RenderFrame, str | Path]":
        """Return the frame of this render and the anchor it searches from.

        A widget of `empty_form` is built after the bind, so only the ambient one fits.
        """
        if self._frame.template_path is not None:
            return self._frame, self._frame.template_path
        ambient = current_ambient_frame()
        if ambient.template_path is not None:
            return ambient, ambient.template_path
        anchor = _project_anchor()
        return replace(ambient, template_path=anchor), anchor

    def _resolve_component(
        self, anchor: "str | Path", request: HttpRequest | None
    ) -> "ComponentInfo | None":
        """Resolve the named component, cached per request when one is bound."""
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
        frame, anchor = self._render_frame()
        info = self._resolve_component(anchor, frame.request)
        if info is None:
            raise UnregisteredComponentError(
                self.component_name,
                anchor,
                components_manager.collect_visible_components(Path(anchor)),
            )
        collect_component_assets(info, frame.collector)
        merged = self.build_attrs(self.attrs, attrs or {})
        # Hyphenated keys such as aria-invalid cannot be read as template vars,
        # so they alias to an underscore form unless that name is already taken.
        aliased = {
            k.replace("-", "_"): v
            for k, v in merged.items()
            if "-" in k and k.replace("-", "_") not in merged
        }
        # The shared input and textarea components read identifier-named vars, so
        # the merged attrs spread to the top level while "attrs" keeps the originals.
        context = {
            **merged,
            **aliased,
            **self.extra_kwargs,
            "attrs": merged,
            "name": name,
            "value": self.format_value(value),
            "errors": self._errors,
        }
        # Every name above comes from this widget and its field binding, so a
        # keyless component context must not take any of them over.
        context[COMPONENT_PROPS_CONTEXT_KEY] = frozenset(context)
        frame.seed(context)
        # render_component returns template-rendered, already-escaped HTML, so a
        # SafeString wrapper matches the Widget.render contract without re-escaping.
        html = render_component(info, context, request=frame.request)
        return SafeString(html)


def bind_component_widgets(
    form: "django_forms.BaseForm | django_forms.BaseFormSet",
    *,
    template_path: str | Path | None,
    request: HttpRequest | None = None,
    collector: StaticCollector | None = None,
    page_module_path: str | Path | None = None,
    action_anchor: str | Path | None = None,
    with_errors: bool = False,
) -> RenderFrame:
    """Inject the render frame and field errors onto ComponentWidgets, and return it.

    The anchor is settled here, so a render reuses the frame instead of copying it.
    """
    frame = RenderFrame(
        template_path=template_path or _project_anchor(),
        page_module_path=page_module_path,
        action_anchor=action_anchor,
        request=request,
        collector=collector,
    )
    _bind_frame(form, frame, with_errors=with_errors)
    return frame


def _bind_frame(
    form: "django_forms.BaseForm | django_forms.BaseFormSet",
    frame: RenderFrame,
    *,
    with_errors: bool,
) -> None:
    """Hand `frame` to every ComponentWidget of a form or of a formset member.

    A formset has no `fields` of its own, so each member form is bound instead.
    """
    if isinstance(form, django_forms.BaseFormSet):
        for member in form.forms:
            _bind_frame(member, frame, with_errors=with_errors)
        return
    for field_name, field in form.fields.items():
        widget = getattr(field, "widget", None)
        if not isinstance(widget, ComponentWidget):
            continue
        # Django deep-copies base_fields and their widgets per form instance, so
        # the frame stays scoped to this form and never leaks across forms.
        widget._frame = frame
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
