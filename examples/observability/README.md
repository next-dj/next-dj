# Observability dashboard

A self-hosted dashboard that watches the framework from the inside.
Every signal next.dj emits during a render flows through one of eight
receivers, lands in an in-process counter store, and surfaces in a
table or a Chart.js bar chart. The example is the most compact proof
that the public extension surface is enough to instrument an entire
request lifecycle without monkey-patching.

The page tree is `dashboards/`. The components live next to the pages
that use them. The `JS_CONTEXT_SERIALIZER` setting points at a
custom class. One co-located component declares its CDN dependency
through `scripts = [...]` so the framework collects it, dedupes it,
and emits it through `{% collect_scripts %}`. The same component
reads `window.Next.context.render_rates` through a per-decorator
serializer override that wins over the global one for one key only.
The entire frontend arrives from CDNs, so the dashboard runs without
`npm`.

## What you will see

| URL | Description |
|-----|-------------|
| `/` | Overview with four headline counters. |
| `/stats/` | Live page with the React sparkline and a filter form. |
| `/stats/?window=1h` | Same page, narrower aggregation window inherited by every nested view. |
| `/stats/pages/` | Per-page render counts pulled from the `pages.rendered` kind. |
| `/stats/components/` | Per-component render counts. |
| `/stats/forms/` | Action dispatch and validation-failure counts. |
| `/stats/static/` | Asset, dedup, and HTML-injection totals. |
| `POST obs:filter_window` | Persists the chosen window via querystring and redirects back. |

The filter form uses the framework `{% form @action %}` tag. Submitting
it fires `forms.action_dispatched` so the example exercises the full
form path without adding a model write.

## How to run

```bash
cd examples/observability
uv run python manage.py migrate
uv run python manage.py runserver        # http://127.0.0.1:8000/
```

Click around `/`, `/stats/`, the four sub-pages. Apply different
windows. Counters move on every render.

```bash
uv run python manage.py flush_metrics
```

Drains the in-process store into the `obs.MetricSnapshot` table and
empties the cache. The next render starts at zero.

Tailwind loads from a CDN in `obs/dashboards/layout.djx`. Chart.js
arrives through the framework static collector — the URL is declared
in `obs/dashboards/_widgets/line_chart/component.py` as
`scripts = [...]` and reaches the page via `{% collect_scripts %}`.
There is no build step.

## Walking the code

### 1. Co-location tree

```
obs/
├── apps.py                   <- imports receivers in ready()
├── models.py                 <- MetricSnapshot persisted by flush_metrics
├── forms.py                  <- WindowFilterForm
├── metrics.py                <- LocMemCache counter API
├── backends.py               <- CountingComponentsBackend
├── static_policies.py        <- InstrumentedDedup
├── serializers.py            <- PydanticJsContextSerializer subclass
├── receivers.py              <- one receiver per signal group, eight blocks
├── management/commands/flush_metrics.py
└── dashboards/
    ├── layout.djx            <- root html shell, Tailwind from CDN
    ├── page.py               <- @context totals
    ├── template.djx          <- overview grid
    ├── _widgets/
    │   ├── stat_card/        <- card composite reused on every page
    │   ├── line_chart/       <- Chart.js bars + scripts=[chart.js cdn]
    │   └── filter_window/    <- form composite
    └── stats/
        ├── layout.djx        <- nested layout, tabs, filter form chrome
        ├── page.py           <- @context live_stats with serializer=
        ├── template.djx
        ├── pages/
        ├── components/
        ├── forms/
        └── static/
```

The `_widgets/` directory sits directly under `dashboards/` so the
overview page and every nested page see the same set. Co-located CSS
and the JSX file are picked up by the static collector automatically.
The dedup policy filters duplicates so a stat card rendered four
times still ships its CSS once.

[obs/dashboards/](obs/dashboards/) is the page tree.

### 2. The custom components backend

`CountingComponentsBackend` extends `FileComponentsBackend` and
records one event per successful name resolution. The dashboard
reads those counters straight from the metrics store, no extra
collector, no second log channel.

```python
class CountingComponentsBackend(FileComponentsBackend):
    def get_component(self, name: str, template_path: Path):
        info = super().get_component(name, template_path)
        if info is not None:
            incr("components.lookup", name)
        return info
```

Wired through `DEFAULT_COMPONENT_BACKENDS` in
[config/settings.py](config/settings.py).

### 3. The custom dedup policy

`InstrumentedDedup` subclasses `UrlDedup` and bumps two counters per
asset: one for every key generation and one only when the key has
already been seen. The counters surface on `/stats/static/`.

The policy installs through the `OPTIONS` block of
`DEFAULT_STATIC_BACKENDS`:

```python
"DEFAULT_STATIC_BACKENDS": [
    {
        "BACKEND": "obs.backends.BabelJsxBackend",
        "OPTIONS": {
            "DEDUP_STRATEGY": "obs.static_policies.InstrumentedDedup",
        },
    },
],
```

[obs/static_policies.py](obs/static_policies.py).

### 4. The pluggable JS context serializer at two levels

```python
NEXT_FRAMEWORK = {
    ...,
    "JS_CONTEXT_SERIALIZER": "obs.serializers.PydanticJsContextSerializer",
}
```

A second override travels with one specific decorator. The
`live_stats` callable on `obs/dashboards/stats/page.py` declares its
own serializer instance:

```python
@context(
    "live_stats",
    inherit_context=True,
    serialize=True,
    serializer=PydanticJsContextSerializer(),
)
def live_stats(window: str = "5m") -> dict[str, Any]:
    ...
```

The framework records the override on the static collector. At inject
time the same key is encoded through the override while every other
key keeps using the global default. The `line_chart` component uses
the same override on its `@component.context("series", ...)` so the
dashboard demonstrates the override path on both decorators.

### 5. The eight receiver blocks

[obs/receivers.py](obs/receivers.py) wires one receiver per signal
group. Every receiver delegates to `metrics.incr`. The handlers stay
thin because the example is a map between signal names and metric
keys.

| Signal group | Sample receiver | Counter kind |
|--------------|-----------------|--------------|
| conf | `settings_reloaded` | `conf.settings_reloaded` |
| deps | `provider_registered` | `deps.provider_registered` |
| pages | `template_loaded`, `context_registered`, `page_rendered` | `pages.template`, `pages.context`, `pages.rendered`, `pages.duration_ms_total` |
| urls | `route_registered`, `router_reloaded` | `urls.route`, `urls.router_reloaded` |
| components | `component_registered`, `component_backend_loaded`, `component_rendered` | `components.registered`, `components.backend_loaded`, `components.rendered` |
| forms | `action_registered`, `action_dispatched`, `form_validation_failed` | `forms.action_registered`, `forms.action_dispatched`, `forms.validation_failed` |
| static | `asset_registered`, `backend_loaded`, `collector_finalized`, `html_injected` | `static.asset_registered`, `static.backend_loaded`, `static.collector_finalized`, `static.html_injected`, `static.injected_bytes_total` |
| server | `watch_specs_ready` | `server.watch_specs_ready` |

Every group has at least one receiver. The bundled signal-group test
proves it by walking the dashboard and asserting that every signal in
the table fires at least once during the walk.

### 6. The filter form and `action_dispatched`

`WindowFilterForm` carries one `ChoiceField`. The action handler in
`obs/dashboards/stats/page.py` redirects with `?window=...` and
returns a `HttpResponseRedirect` so subsequent renders inherit the new
window through the `@context("window", inherit_context=True)`
callable on the same page.

The form composite lives under
[`_widgets/filter_window/`](obs/dashboards/_widgets/filter_window/).
It uses `{% form @action="obs:filter_window" %}` so submission goes
through the framework dispatcher and `forms.action_dispatched` fires
end to end, not only in tests.

### 7. The flush command

```python
def handle(self, *_args, **_options):
    rows = flush()
    if not rows:
        self.stdout.write("nothing to flush")
        return
    MetricSnapshot.objects.bulk_create(
        [MetricSnapshot(kind=k, key=key, value=v) for k, key, v in rows]
    )
    self.stdout.write(self.style.SUCCESS(f"flushed {len(rows)} counters"))
```

The command drains every counter and clears the index in a single
pass. Calling it twice in a row is safe: the second call sees an
empty store and exits immediately.

[obs/management/commands/flush_metrics.py](obs/management/commands/flush_metrics.py).

## Further reading

- Aggregated signal catalogue at
  [docs/content/api/signals.rst](../../docs/content/api/signals.rst).
- Static-asset pipeline and the new per-decorator serializer override at
  [docs/content/guide/static-assets.rst](../../docs/content/guide/static-assets.rst).
