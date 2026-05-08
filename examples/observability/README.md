# Observability dashboard

A self-hosted dashboard that watches the framework from the inside.
Every signal next.dj emits during a render flows through one of eight
receivers, lands in an in-process counter store, and surfaces in a
table, a Chart.js bar chart, or a React sparkline. The example is the
most compact proof that the public extension surface is enough to
instrument an entire request lifecycle without monkey-patching.

The page tree is `dashboards/`. The components live next to the pages
that use them. The `JS_CONTEXT_SERIALIZER` setting points at a custom
class. One co-located component declares its CDN dependencies through
`scripts = [...]` so the framework collects them, dedupes them, and
emits them through `{% collect_scripts %}`. Two co-located components
read `window.Next.context.<key>` through a per-decorator serializer
override that wraps the payload in a versioned envelope, while a
sibling key on the same page stays flat through the global default.
The entire frontend arrives from CDNs, so the dashboard runs without
`npm`. The static collector picks up `.css`, `.js`, and `.jsx` files,
the last through a custom `BabelJsxBackend` that emits
`<script type="text/babel">` tags.

## What you will see

| URL | Description |
|-----|-------------|
| `/` | Overview with four headline counters and a React sparkline. |
| `/stats/` | Live page with the Chart.js bar chart and a filter form. |
| `/stats/?window=1h` | Same page, real time-bucketed aggregation across the last hour. |
| `/stats/pages/` | Per-page render counts pulled from the cumulative `pages.rendered` kind. |
| `/stats/components/` | Per-component render counts. |
| `/stats/forms/` | Action dispatch and validation-failure counts. |
| `/stats/static/` | Asset, dedup, and HTML-injection totals. |
| `POST obs:filter_window` | Persists the chosen window via querystring and redirects back. |

The filter form uses the framework `{% form @action %}` tag.
Submitting it fires `forms.action_dispatched` so the example exercises
the full form path without adding a model write.

## How to run

```bash
cd examples/observability
uv run python manage.py migrate
uv run python manage.py runserver        # http://127.0.0.1:8000/
```

Click around `/`, `/stats/`, the four sub-pages. Apply different
windows. Counters move on every render, and the windowed view actually
narrows to the chosen aggregation slice.

```bash
uv run python manage.py flush_metrics
```

Drains both the cumulative and the bucketed counters into the
`obs.MetricSnapshot` table and empties the cache. The next render
starts at zero.

Tailwind loads from a CDN in `obs/dashboards/layout.djx`. Chart.js
arrives through the framework static collector — the URL is declared
in `obs/dashboards/_widgets/render_chart/component.py` as
`scripts = [...]` and reaches the page via `{% collect_scripts %}`.
React, ReactDOM, and Babel-standalone follow the same path from
`obs/dashboards/_widgets/sparkline/component.py`. There is no build
step.

## Walking the code

### 1. Co-location tree

```
obs/
├── apps.py                   <- imports receivers and registers the jsx kind
├── models.py                 <- MetricSnapshot persisted by flush_metrics
├── forms.py                  <- WindowFilterForm
├── metrics.py                <- LocMemCache counter API, bucketed and cumulative
├── backends.py               <- CountingComponentsBackend, BabelJsxBackend
├── static_policies.py        <- InstrumentedDedup
├── serializers.py            <- PydanticJsContextSerializer + WrappedJsContextSerializer
├── receivers.py              <- one receiver per signal group, eight blocks
├── management/commands/flush_metrics.py
└── dashboards/
    ├── layout.djx            <- root html shell, Tailwind from CDN
    ├── page.py               <- @context totals
    ├── template.djx          <- overview grid + React sparkline
    ├── _widgets/
    │   ├── stat_card/        <- card composite reused on every page
    │   ├── counter_list/     <- list/table widget shared by every stats subpage
    │   ├── stats_nav/        <- nav tabs rendered from a Python list
    │   ├── filter_window/    <- form composite
    │   ├── render_chart/     <- Chart.js bar chart, scripts=[chart.js cdn]
    │   └── sparkline/        <- React + JSX sparkline, scripts=[react, babel cdn]
    └── stats/
        ├── layout.djx        <- nested layout, tabs, filter form chrome
        ├── page.py           <- @context live_stats with WrappedJsContextSerializer
        ├── template.djx
        ├── pages/
        ├── components/
        ├── forms/
        └── static/
```

The `_widgets/` directory sits directly under `dashboards/` so the
overview page and every nested page see the same set. Co-located CSS,
JS, and JSX files are picked up by the static collector automatically.
The dedup policy filters duplicates so a CDN script referenced by four
components still ships once.

[obs/dashboards/](obs/dashboards/) is the page tree.

### 2. The custom components backend

`CountingComponentsBackend` extends `FileComponentsBackend` and
records one event per successful name resolution. The dashboard reads
those counters straight from the metrics store, no extra collector,
no second log channel.

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

### 3. The custom static backend and JSX kind

`BabelJsxBackend` extends `StaticFilesBackend`. The framework asset
registry is type-agnostic, so `apps.py` registers a new `jsx` kind
that points at the new renderer:

```python
default_kinds.register(
    "jsx",
    extension=".jsx",
    slot="scripts",
    renderer="render_babel_script_tag",
)
```

`render_babel_script_tag` returns a `<script type="text/babel">` tag.
Babel-standalone parses the tag in the browser and executes the JSX,
no npm required. The custom backend installs through the same
`DEFAULT_STATIC_BACKENDS` slot the framework's default backend uses,
and pairs with `InstrumentedDedup` through the `OPTIONS` block:

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

[obs/backends.py](obs/backends.py) and
[obs/static_policies.py](obs/static_policies.py).

### 4. The pluggable JS context serializer at three levels

```python
NEXT_FRAMEWORK = {
    ...,
    "JS_CONTEXT_SERIALIZER": "obs.serializers.PydanticJsContextSerializer",
}
```

The global default produces flat JSON for every key reaching
`window.Next.context`. Two `@context` callables override it with
`WrappedJsContextSerializer`, which wraps the payload in
`{"v": 1, "data": ...}`. The first override sits on a page-level
callable in `obs/dashboards/stats/page.py`:

```python
@context(
    "live_stats",
    inherit_context=True,
    serialize=True,
    serializer=WrappedJsContextSerializer(),
)
def live_stats(window: str = "5m") -> dict[str, Any]:
    ...
```

The second sits on a component-level callable in
`_widgets/sparkline/component.py`:

```python
@component.context(
    "totals_chart",
    serialize=True,
    serializer=WrappedJsContextSerializer(),
)
def totals_chart(totals: dict[str, int]) -> dict[str, Any]:
    ...
```

The framework records each override at the static collector level.
At inject time the rendered HTML carries
`"live_stats":{"v":1,"data":{...}}` and
`"totals_chart":{"v":1,"data":{...}}`, while the sibling
`render_rates` key from `_widgets/render_chart/component.py` stays
flat through the global default. Three demonstrations, one HTML
response, no per-key code branching.

### 5. The eight receiver blocks

[obs/receivers.py](obs/receivers.py) wires one receiver per signal
group. Every receiver delegates to `metrics.incr`, which bumps both
the cumulative counter and the current minute bucket. The handlers
stay thin because the example is a map between signal names and
metric keys.

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

### 6. The filter form, time-bucketing, and `action_dispatched`

`WindowFilterForm` carries one `ChoiceField`. The action handler in
`obs/dashboards/stats/page.py` redirects with `?window=...` and
returns a `HttpResponseRedirect` so subsequent renders inherit the new
window through the `@context("window", inherit_context=True)`
callable on the same page.

Behind the form, `metrics.incr` writes both a cumulative counter and
a minute-floor bucket key. `metrics.read_window(kind, minutes)` sums
every bucket whose timestamp is inside `[now - minutes, now]`. The
`live_stats` page-level context calls `read_window` so the chosen
window narrows the aggregation in real time. The four cumulative
sub-pages (`/stats/pages/`, `/stats/components/`, `/stats/forms/`,
`/stats/static/`) keep using `read_kind` for the lifetime totals.

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

The command drains every counter (cumulative and bucketed) and clears
the index in a single pass. Calling it twice in a row is safe: the
second call sees an empty store and exits immediately.

[obs/management/commands/flush_metrics.py](obs/management/commands/flush_metrics.py).

## Out of scope

The dashboard renders every counter without pagination, so a long-
lived process with thousands of distinct page paths will eventually
need a top-N filter. The bucket index also grows unboundedly until
the next `flush_metrics` run, which is fine for an example but worth
calling out before adopting the pattern in production.

## Further reading

- Aggregated signal catalogue at
  [docs/content/api/signals.rst](../../docs/content/api/signals.rst).
- Static-asset pipeline and the per-decorator serializer override at
  [docs/content/guide/static-assets.rst](../../docs/content/guide/static-assets.rst).
