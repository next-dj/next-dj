# Feature flags admin

A small feature-flag console built on **next-dj**. Toggle flags in an admin
panel and watch them gate content on a demo page. Values live in
`LocMemCache` with a `post_save` receiver invalidating each entry on write,
and every page render bumps a counter so you can see the `page_rendered`
signal firing in real time.

The example focuses on the signal / receiver / cache layer of the framework:
a composite component with a Python `render()` that returns empty to hide
gated content, a custom DI provider that resolves a `Flag` by name, a
bulk-toggle form, a nested admin layout, and two signal receivers
(`post_save` for cache invalidation, `page_rendered` for metrics).

## What you will see

| URL | Description |
|-----|-------------|
| `/` | Two columns — enabled flags on the left, disabled on the right. |
| `/admin/` | Bulk-toggle form. Each row shows a live "on/off" preview component. |
| `/admin/metrics/` | Per-page render counters from the `page_rendered` receiver. |
| `/demo/` | `feature_guard` components for three demo flags. Disabled flags render nothing. |

## How to run

```bash
cd examples/feature-flags
uv run python manage.py migrate
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest                         # 31 tests, 100% coverage
```

Tailwind loads via the Play CDN in
[`panels/layout.djx`](flags/panels/layout.djx). No Node, no build step.

Seed a few flags from the Django shell:

```bash
uv run python manage.py shell -c "
from flags.models import Flag
Flag.objects.create(name='beta_checkout', label='Beta checkout',
                    description='Use the new checkout flow.', enabled=True)
Flag.objects.create(name='dark_sidebar', label='Dark sidebar',
                    description='Experimental dark-mode navigation.', enabled=False)
Flag.objects.create(name='ai_suggestions', label='AI suggestions',
                    description='Recommendations powered by the v2 model.', enabled=False)
"
```

## Project tour

```
examples/feature-flags/
├── config/
│   ├── settings.py             # PAGES_DIR="panels", COMPONENTS_DIR="_chunks", LocMemCache
│   └── urls.py                 # Only include('next.urls') — file router owns every route
└── flags/
    ├── apps.py                 # AppConfig.ready() imports providers + receivers
    ├── models.py               # Flag(name, label, description, enabled, updated_at)
    ├── providers.py            # DFlag[T] marker + FlagProvider (URL kwarg or component prop)
    ├── cache.py                # get_cached_flag, invalidate_flag, MISSING_SENTINEL
    ├── metrics.py              # record_render, render_counts — LocMemCache counters
    ├── receivers.py            # post_save / post_delete / page_rendered wiring
    └── panels/                 # ← PAGES_DIR
        ├── layout.djx          # Root chrome — Tailwind, header, top nav
        ├── page.py             # @context("active_flags") + @context("disabled_flags")
        ├── template.djx        # Two-column flag list
        ├── admin/
        │   ├── layout.djx      # Nested toolbar + subnav (Flags · Render metrics · Back)
        │   ├── page.py         # BulkToggleForm + @action("bulk_toggle")
        │   ├── template.djx    # Form body with per-row {% component "toggle_preview" %}
        │   └── metrics/
        │       ├── page.py     # @context("render_counts") from the metrics module
        │       └── template.djx
        ├── demo/
        │   ├── page.py         # @context("demo_flags") for the per-flag status header
        │   └── template.djx    # Three {% component "feature_guard" %} calls
        └── _chunks/            # ← COMPONENTS_DIR
            ├── feature_guard/  # Composite: component.py with render() that gates content
            ├── toggle_preview/ # Composite: @component.context("state") + component.djx
            └── nav_link/       # Shared active-aware link
```

## Walking the code

The rest of this README walks the project in the order you would naturally
learn the framework.

### 1. Rename `pages/` and `_components/` to fit the domain

[`config/settings.py`](config/settings.py) reshapes the conventional folders
through `NEXT_FRAMEWORK`:

```python
NEXT_FRAMEWORK = {
    "DEFAULT_PAGE_BACKENDS": [{
        "BACKEND": "next.urls.FileRouterBackend",
        "PAGES_DIR": "panels",
    }],
    "DEFAULT_COMPONENT_BACKENDS": [{
        "BACKEND": "next.components.FileComponentsBackend",
        "COMPONENTS_DIR": "_chunks",
    }],
}
```

Both keys are strings (not paths). The file router and components backend
look up `panels/` inside every installed app. Any directory name works — the
word "pages" is just a default, not a framework requirement.

### 2. `Flag` model and cache-through lookup

[`flags/models.py`](flags/models.py) holds a tiny `Flag(name, label,
description, enabled, updated_at)` table.
[`flags/cache.py`](flags/cache.py) wraps the model with a read-through
`LocMemCache` layer:

```python
def get_cached_flag(name: str) -> Flag | None:
    key = _key(name)
    cached = cache.get(key)
    if cached == MISSING_SENTINEL:
        return None
    if cached is not None:
        return cached
    try:
        flag = Flag.objects.get(name=name)
    except Flag.DoesNotExist:
        cache.set(key, MISSING_SENTINEL, FLAG_CACHE_TTL)
        return None
    cache.set(key, flag, FLAG_CACHE_TTL)
    return flag
```

The sentinel for "flag does not exist" (`"__missing__"`) is stored the same
way as a hit. That way a repeated lookup for a typo-ed flag name never
re-queries the database until the cache invalidates.

### 3. DI provider — `DFlag[Flag]`

[`flags/providers.py`](flags/providers.py) registers a generic marker and a
resolver that reads the flag name from two places:

```python
class DFlag[T](DDependencyBase[T]):
    __slots__ = ()


class FlagProvider(RegisteredParameterProvider):
    def can_handle(self, param, _context):
        return get_origin(param.annotation) is DFlag

    def resolve(self, param, context):
        (model_cls,) = get_args(param.annotation)
        name = context.url_kwargs.get("name") or context.context_data.get("flag_name")
        if not name:
            raise LookupError(...)
        return get_cached_flag(str(name)) or model_cls(name=str(name), enabled=False)
```

Two call sites drive the same provider:

- **Pages / URL kwargs**: not used here, but the pattern
  `panels/admin/flags/[name]/` would resolve `flag: DFlag[Flag]` from
  `context.url_kwargs["name"]`. Mirrors the `DLink[Link]` pattern from the
  URL shortener example.
- **Components / template props**: the `feature_guard` component is called
  as `{% component "feature_guard" flag_name="beta_checkout" %}`, and the
  literal string flows into the component's template context. The provider
  reads `context.context_data["flag_name"]`.

When the flag does not exist, the provider returns a **disabled placeholder**
(`Flag(name=..., enabled=False)`) instead of `None`. Guard components can
then blindly check `flag.enabled` without three-way `None` logic at every
usage site. It is a deliberate choice — a `None` would force every call
site to handle a ternary (on / off / unknown), and "unknown" is always
treated as off here anyway.

Because the annotation is inspected at DI time, the component module
**must not use** `from __future__ import annotations` — PEP 563 would
string-ify `DFlag[Flag]` and `get_origin` would return `None`. That is the
same gotcha any custom DI marker runs into in this codebase.

### 4. Composite `feature_guard` — Python `render()` returns empty to hide

[`flags/panels/_chunks/feature_guard/component.py`](flags/panels/_chunks/feature_guard/component.py)
has no `component.djx` — it is a pure-Python composite:

```python
def render(flag: DFlag[Flag]) -> str:
    if not flag.enabled:
        return ""
    return _BANNER.render(Context({"flag": flag, "label": ..., "description": ...}))
```

`CompositeComponentRenderer` detects the `render` attribute on
`component.py`, resolves `flag` through the DI chain above, and replaces the
`{% component ... %}` tag with whatever `render()` returns. Empty string
means the component is invisible — no wrapper `<div>`, no comments, no
whitespace in the output. This is the cleanest way to gate content server
side.

The HTML is a pre-parsed `django.template.Template`. Django's auto-escape
handles `flag.name`, `flag.label`, and `flag.description` — the same values
could come from user input with no extra `escape()` calls. Never register a
loader or component that returns user-supplied HTML without this guarantee.

### 5. Composite `toggle_preview` — template + `@component.context`

The admin form uses a second composite to show an "on / off" badge next to
every row. It takes the template path instead of the Python-render path
because the badge has no conditional logic around whether to render at all:

```python
# _chunks/toggle_preview/component.py
@component.context("state")
def _state(flag: DFlag[Flag]) -> dict[str, str]:
    if flag.enabled:
        return {"label": "on", "classes": "bg-emerald-100 text-emerald-800"}
    return {"label": "off", "classes": "bg-slate-100 text-slate-600"}
```

```djx
{# _chunks/toggle_preview/component.djx #}
<span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium {{ state.classes }}">
  {{ state.label }}
</span>
```

Two composites, two render strategies. `CompositeComponentRenderer` prefers
`render()` when present; otherwise it loads the sibling `component.djx` and
evaluates `@component.context` callables before rendering. Both paths share
the DI chain — `flag: DFlag[Flag]` works identically in each.

Inside the admin `template.djx` the `for`-loop binds `flag` to each Flag
row. `{% component "toggle_preview" flag_name=flag.name %}` passes that
name as a literal prop, flattening the parent's `flag` variable out of the
child context so `DFlag[Flag]` resolves via the prop rather than via
`ContextByNameProvider` matching on the parameter name.

### 6. Bulk-toggle form

[`flags/panels/admin/page.py`](flags/panels/admin/page.py) registers a
single `@forms.action("bulk_toggle")` handling a checkbox grid:

```python
class BulkToggleForm(Form):
    enabled_names = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "hidden"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["enabled_names"].choices = [
            (name, name) for name in Flag.objects.values_list("name", flat=True)
        ]


@action("bulk_toggle", form_class=BulkToggleForm)
def _bulk_toggle(form: BulkToggleForm) -> HttpResponseRedirect:
    enabled = set(form.cleaned_data["enabled_names"])
    for flag in Flag.objects.all():
        should_be_on = flag.name in enabled
        if flag.enabled != should_be_on:
            flag.enabled = should_be_on
            flag.save(update_fields=["enabled", "updated_at"])
    return HttpResponseRedirect("/admin/")
```

Two details worth noting:

- `choices` is populated in `__init__` rather than at class-define time so
  the field reflects the current set of flags on every request.
- Only **changed** flags are saved. Untouched rows do not fire `post_save`,
  which keeps the cache-invalidation receiver honest — nothing gets
  invalidated unless there is a real state transition.

The widget has Tailwind `class="hidden"` so each checkbox is visually
replaced by an inline `<input type="checkbox">` in the template — the label
wraps the whole row so the entire card is clickable.

### 7. Receivers — `post_save`, `post_delete`, `page_rendered`

[`flags/receivers.py`](flags/receivers.py) wires three receivers at app
ready time:

```python
@receiver(post_save, sender=Flag)
def _invalidate_on_save(sender, instance, **_):
    invalidate_flag(instance.name)

@receiver(post_delete, sender=Flag)
def _invalidate_on_delete(sender, instance, **_):
    invalidate_flag(instance.name)

@receiver(page_rendered)
def _count_page_render(sender, file_path, **_):
    record_render(file_path.name)
```

The two database receivers mean the cache and the DB can never drift apart.
Toggle a flag, the row saves, the cache entry disappears, the next read
refetches — in that order, in under a millisecond.

`page_rendered` is a `next.pages.signals` signal, not a Django one. It
fires at the end of every page render and carries the full `file_path` of
the `page.py` that produced it. Because every page file is literally named
`page.py`, the receiver derives the key from the path segments *under*
`panels/` — the root page becomes `"/"`, `admin/page.py` becomes
`"admin"`, `admin/metrics/page.py` becomes `"admin/metrics"`. The
`/admin/metrics/` page reads the counters through `render_counts()`, and
because the signal fires *after* rendering, the metrics page's own entry
only appears on the next visit.

### 8. Shared `nav_link` component — active state from `request.resolver_match`

Same pattern as the other examples — no duplication, no manual "current
page" flags. The link component reads the view name from the resolver and
compares:

```python
@component.context("is_active")
def _is_active(url_name: str, request: HttpRequest, active_when: str = "") -> bool:
    view_name = request.resolver_match.view_name
    if active_when:
        return active_when in view_name
    return view_name == url_name
```

The root header uses `active_when="page_admin"` so `/admin/` and
`/admin/metrics/` both highlight the "Admin" tab. The subnav inside the
admin layout uses exact matches (`next:page_admin`, `next:page_admin_metrics`).

### 9. URL names from the file router

| File | URL | Name |
|------|-----|------|
| `panels/page.py` | `/` | `next:page_` |
| `panels/admin/page.py` | `/admin/` | `next:page_admin` |
| `panels/admin/metrics/page.py` | `/admin/metrics/` | `next:page_admin_metrics` |
| `panels/demo/page.py` | `/demo/` | `next:page_demo` |

The bulk-toggle action is mounted at the framework's action URL. Tests use
`NextClient.post_action("bulk_toggle", {...})` instead of a hardcoded path.

## Gotchas

### `DFlag` needs the annotation at runtime

Any module that declares a parameter `flag: DFlag[Flag]` **must not** start
with `from __future__ import annotations`. PEP 563 would string-ify the
annotation and `get_origin(...)` in `FlagProvider.can_handle` returns
`None` on strings. The two component modules in this example skip that
import on purpose.

### `{% component %}` props are literal strings

`{% component "feature_guard" flag_name="beta_checkout" %}` passes the
literal `"beta_checkout"`. Inside a loop, use the parent variable binding
instead: `flag_name=flag.name` resolves to the loop-iteration value
because Django evaluates the expression during template rendering. The
admin template uses this pattern.

### `manage.py check` enforces the "one body source" rule

A page with both `render()` and `template.djx` emits `next.W043`. If a
page has none of (render / template attribute / registered loader that
matches), `next.E012` fails. Every page in this example declares exactly
one body source — the `page.py` modules all ship `template.djx` siblings
and no `template` attribute.

### Receivers are imported in `apps.ready()`, not at module level

`FlagsConfig.ready()` imports `flags.providers` and `flags.receivers`. If
you move those imports to the top of `flags/__init__.py`, Django will try
to load them before the app registry is populated and `@receiver(post_save,
sender=Flag)` will crash looking up `Flag`.

## Tests

[`tests/test_e2e.py`](tests/test_e2e.py) and
[`tests/test_unit.py`](tests/test_unit.py) cover every module in `flags/`
at **100%**. `make test-examples` enforces `--cov-fail-under=100`, so a
regression anywhere in this example breaks the build.

What is tested:

- Home page partitions flags into "enabled" and "disabled" columns.
- Admin renders a bulk-toggle form with checkboxes + toggle_preview badges.
- POSTing the form toggles the right rows, redirects to `/admin/`, and
  invalidates the cache for every changed flag (but not unchanged ones).
- Demo page renders a `feature_guard` banner only when the underlying flag
  exists **and** is enabled. Missing flags and disabled flags produce zero
  bytes of output.
- Enabled flag with no description falls back to "No description provided."
- `page_rendered` receiver increments per-page counters visible on
  `/admin/metrics/`.
- `nav_link` active state flips between home / admin / metrics correctly.
- Unit: `Flag.__str__`, cache sentinel round-trip + invalidation, metrics
  aggregation + deduplicated index, `FlagProvider.resolve` raises
  `LookupError` when neither URL kwarg nor template prop is set.

## Further reading

- [next/components/renderers.py](../../next/components/renderers.py) —
  `CompositeComponentRenderer` and the render-function branch used by
  `feature_guard`.
- [next/deps/resolver.py](../../next/deps/resolver.py) — how DI providers
  are instantiated lazily and iterated per parameter.
- [next/deps/providers.py](../../next/deps/providers.py) —
  `RegisteredParameterProvider` base and auto-registration.
- [next/pages/signals.py](../../next/pages/signals.py) — `page_rendered`
  payload documentation.
- [next/forms/manager.py](../../next/forms/manager.py) — form-action
  registration, dispatch, and the CSRF + form-class contract.
- [next/signals.py](../../next/signals.py) — aggregate re-export covering
  every signal the framework emits, including `page_rendered`.
