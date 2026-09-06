# Observability dashboard

A self-hosted dashboard that watches the framework from the inside. Signals from eight framework subsystems land in nineteen receivers, accumulate in an in-process counter store, and surface in a table, a Chart.js bar chart, or a React sparkline. Nothing here monkey-patches the framework, every hook is a documented extension point.

The page tree is `dashboards/`. The components live next to the pages that use them. The `JS_CONTEXT_SERIALIZER` setting points at a custom class. Two pages declare their CDN dependencies through page-level `scripts = [...]` lists so the framework collects them, dedupes them, and emits them through `{% collect_scripts %}` ahead of every widget's co-located file. Two co-located widgets read `window.Next.context.<key>`, the sparkline through a per-decorator serializer override that wraps the payload in a versioned envelope, the bar chart flat through the global default. The entire frontend arrives from CDNs, so the dashboard runs without `npm`. The static collector picks up `.css`, `.js`, and `.jsx` files, the last through a custom `BabelJsxBackend` that emits `<script type="text/babel">` tags.

## What you will see

| URL | Description |
| --- | --- |
| `/` | Overview with four headline counters in a polling zone, a React sparkline, and a lazy-loaded busiest-pages widget. |
| `/stats/` | Live page with the Chart.js bar chart and the window filter. `?window=1m`, `?window=5m`, and `?window=1h` each re-aggregate over real minute buckets. |
| `/stats/pages/` | Per-page render counts pulled from the cumulative `pages.rendered` kind. |
| `/stats/components/` | Per-component render counts. |
| `/stats/forms/` | Action dispatch and validation-failure counts. |
| `/stats/static/` | Asset, dedup, and HTML-injection totals. |
| `POST` to `window_filter_form` | Re-aggregates under the chosen window. A partial apply from `/stats/` morphs the `live-totals` zone plus the `stats-window` heading label and emits the custom `metric-pulse` verb, otherwise it redirects with `?window=...`. |

The filter form uses the framework `{% form "window_filter_form" %}` tag. Submitting it fires `forms.action_dispatched` so the example exercises the full form path without adding a model write. From the live page the form targets the `live-totals` zone, so the apply rides the partial protocol and ships a project-defined verb beside the built-in morph.

The overview shows the other two zone modes. The stat-tile grid sits in `{% zone "overview-totals" poll="5s" %}`, so the client re-GETs the zone every five seconds and the counters refresh without a reload. Each poll tick re-renders the four tiles, so the components counter also counts the poll's own renders — visible proof the polling works. The busiest-pages widget below the sparkline is a `lazy="load"` zone: the first paint ships only its `{% placeholder %}` branch and the body arrives through one batched zone-GET as soon as the page loads. The `totals` provider is declared as `@context("totals", zone="overview-totals")`, so the lazy GET for the widget skips the four counter aggregations it would never read, while the full render still runs them.

## How to run

```bash
cd examples/observability
uv run python manage.py migrate
uv run python manage.py runserver        # http://127.0.0.1:8000/
uv run pytest
```

Click around `/`, `/stats/`, the four sub-pages. Apply different windows. Counters move on every render, the overview tiles re-poll on their own every five seconds, and the windowed view actually narrows to the chosen aggregation slice.

```bash
uv run python manage.py flush_metrics
```

Drains both the cumulative and the bucketed counters into the `obs.MetricSnapshot` table and empties the cache. The next render starts at zero.

Tailwind loads from a CDN in the shared `page_head` component pulled into `instrument/layout.djx`. Chart.js arrives through the framework static collector — the URL is declared in the page-level `scripts` list of `obs/dashboards/stats/page.py` and reaches the page via `{% collect_scripts %}`. Page-level scripts land in the injection order before every widget's co-located file, so the chart widget's `component.js` always finds `window.Chart` defined. React, ReactDOM, and Babel-standalone follow the same path from the `scripts` list in `obs/dashboards/page.py`, which keeps them ahead of the sparkline's own `component.jsx`. There is no build step.

Browser-side Babel is a full-page technique, so the sparkline is the one widget that renders only on a full page load — it sits outside every zone and its `component.jsx` never rides a patch envelope. The framework says as much through `next.W074`, which `config/settings.py` silences on purpose with a comment. The silencing is honest only while that invariant holds, so `TestSparklineStaysOutsideEveryZone` in the e2e suite re-GETs every zone the dashboard declares and fails if the sparkline mount ever shows up in a zone body.

Both chart widgets mount through `Next.partial.onMount` with a WeakMap keyed by the host element, so a partial morph that reconciles the host in place never creates a second Chart.js instance or React root. A delegated `next:removed` listener walks the detached subtree and destroys the chart or unmounts the root when the host actually leaves the document.

## Walking the code

### 1. Co-location tree

```
instrument/
└── layout.djx                <- root html shell, shared page_head brings Tailwind from CDN
obs/
├── apps.py                   <- imports receivers, registers the jsx kind and the metric-pulse verb
├── models.py                 <- MetricSnapshot persisted by flush_metrics
├── forms.py                  <- WindowFilterForm, partial apply emits metric-pulse
├── metrics.py                <- LocMemCache counter API, bucketed and cumulative
├── backends.py               <- CountingComponentsBackend, BabelJsxBackend
├── static_policies.py        <- InstrumentedDedup
├── serializers.py            <- PydanticJsContextSerializer + WrappedJsContextSerializer
├── receivers.py              <- one receiver per signal group, eight blocks
├── management/commands/flush_metrics.py
└── dashboards/
    ├── page.py               <- @context totals
    ├── template.djx          <- poll zone with the overview grid, React sparkline, lazy busiest-pages widget
    ├── _widgets/
    │   ├── counter_list/     <- list/table widget shared by every stats subpage
    │   ├── busiest_pages/    <- lazy-zone body, component-level top_by_kind lookup
    │   ├── stats_nav/        <- nav tabs rendered from a Python list
    │   ├── filter_window/    <- window filter form (template-only component)
    │   ├── render_chart/     <- Chart.js bar chart, chart.js cdn via stats/page.py
    │   └── sparkline/        <- React + JSX sparkline, full render only, react/babel cdn via page.py
    └── stats/
        ├── layout.djx        <- nested layout, tabs, filter form chrome
        ├── page.py           <- @context live_stats, live_zone, scripts=[chart.js cdn]
        ├── template.djx      <- live-totals zone
        ├── template.js       <- defineOp("metric-pulse") flash handler, co-located
        ├── template.css      <- metric-pulse keyframe, co-located
        ├── pages/
        ├── components/
        ├── forms/
        └── static/
```

The `_widgets/` directory sits directly under `dashboards/` so the overview page and every nested page see the same set. `stat_card`, `page_header`, `app_shell`, and `nav_link` come from the shared kit in [`../_shared/_components/`](../_shared/_components/) instead, wired through `COMPONENT_BACKENDS[0]["DIRS"]`. Co-located CSS, JS, and JSX files are picked up by the static collector automatically. The dedup policy filters duplicates so a CDN script referenced by four components still ships once.

[obs/dashboards/](obs/dashboards/) is the page tree.

### 2. The custom components backend

`CountingComponentsBackend` extends `FileComponentsBackend` and records one event per successful name resolution. The counts land in the same store as the signal-fed ones, under the `components.lookup` kind, so `flush_metrics` persists them beside everything else. A lookup is not a render, which is why `/stats/components/` keeps reading the `components.rendered` counters the `component_rendered` signal feeds.

```python
class CountingComponentsBackend(FileComponentsBackend):
    def get_component(self, name: str, template_path: Path):
        info = super().get_component(name, template_path)
        if info is not None:
            incr("components.lookup", name)
        return info
```

Wired through `COMPONENT_BACKENDS` in [config/settings.py](config/settings.py).

### 3. The custom static backend and JSX kind

`BabelJsxBackend` extends `StaticFilesBackend`. The framework asset registry is type-agnostic, so `apps.py` registers a new `jsx` kind that points at the new renderer:

```python
default_kinds.register(
    "jsx", extension=".jsx", slot="scripts", renderer="render_babel_script_tag"
)
```

`render_babel_script_tag` returns a `<script type="text/babel">` tag. Babel-standalone parses the tag in the browser and executes the JSX, no npm required. The custom backend installs through the same `STATIC_BACKENDS` slot the framework's default backend uses, and pairs with `InstrumentedDedup` through the `OPTIONS` block:

```python
"STATIC_BACKENDS": [
    {
        "BACKEND": "obs.backends.BabelJsxBackend",
        "OPTIONS": {
            "DEDUP_STRATEGY": "obs.static_policies.InstrumentedDedup",
        },
    },
],
```

[obs/backends.py](obs/backends.py) and [obs/static_policies.py](obs/static_policies.py).

### 4. The pluggable JS context serializer at three levels

```python
NEXT_FRAMEWORK = {
    ...,
    "JS_CONTEXT_SERIALIZER": "obs.serializers.PydanticJsContextSerializer",
}
```

The global default produces flat JSON for every key reaching `window.Next.context`. Two callables override it with `WrappedJsContextSerializer`, which wraps the payload in `{"v": 1, "data": ...}`. The first override sits on a page-level `@context` in `obs/dashboards/stats/page.py`:

```python
@context(
    "live_stats",
    inherit_context=True,
    serialize=True,
    serializer=WrappedJsContextSerializer(),
)
def live_stats(window: str = "5m") -> dict[str, Any]: ...
```

The second sits on a `@component.context` in `_widgets/sparkline/component.py`:

```python
@component.context(
    "totals_chart", serialize=True, serializer=WrappedJsContextSerializer()
)
def totals_chart(totals: dict[str, int]) -> dict[str, Any]: ...
```

The framework records each override at the static collector level. At inject time the rendered HTML carries `"live_stats":{"v":1,"data":{...}}` and `"totals_chart":{"v":1,"data":{...}}`, while the sibling `render_rates` key from `_widgets/render_chart/component.py` stays flat through the global default. Three demonstrations, one HTML response, no per-key code branching.

The same `Next._init(...)` payload carries the keys the framework owns outright. `$csrf` travels on every render, `$dev` only while `DEBUG` is on, and both are off limits to your own `@context` keys — a collision is reported as `next.W075` and the registered value never reaches `window.Next.context`. `TestDevFlagChannel` in the e2e suite pins both halves of the `$dev` contract.

### 5. The receivers and the counter keys

[obs/receivers.py](obs/receivers.py) wires the framework signals of eight subsystems. Every receiver delegates to `metrics.incr(kind, key)`, which bumps both the cumulative counter and the current minute bucket. The handlers stay thin because the example is a map between signal names and metric keys.

A counter is addressed by a `kind` and a `key`. When the interesting part of an event is its subject, the kind names the family and the key carries the subject — a page path, a component name, an action name. When the event is a bare occurrence, the kind is the group and the key is the signal name.

| Signal group | Receivers | Counter kind | Counter key |
| --- | --- | --- | --- |
| conf | `settings_reloaded` | `conf` | `settings_reloaded` |
| deps | `provider_registered` | `deps` | `provider_registered` |
| pages | `template_loaded`, `context_registered`, `page_rendered` | `pages.template`, `pages.context`, `pages.rendered`, `pages.duration_ms_total` | page path |
| urls | `route_registered` | `urls.route` | URL path |
| urls | `router_reloaded` | `urls` | `router_reloaded` |
| components | `component_registered`, `components_registered`, `component_rendered` | `components.registered`, `components.rendered` | component name |
| components | `component_backend_loaded` | `components` | `backend_loaded` |
| forms | `action_registered`, `action_dispatched`, `form_validation_failed` | `forms.action_registered`, `forms.action_dispatched`, `forms.validation_failed` | action name |
| static | `asset_registered`, `backend_loaded`, `collector_finalized`, `html_injected` | `static` | `asset_registered`, `backend_loaded`, `collector_finalized`, `html_injected`, `injected_bytes_total` |
| server | `watch_specs_ready` | `server` | `watch_specs_ready` |

The page signals carry an absolute path, so `page_key` in [obs/receivers.py](obs/receivers.py) rewrites it relative to `BASE_DIR` before it becomes a counter key. Without that step `/stats/pages/` would publish the machine's home directory in every row.

`components_registered` is the bulk twin of `component_registered`, so its receiver loops over the payload and reuses the same kind. `page_rendered` feeds two counters: one render count and one accumulated `duration_ms_total`, floored to at least 1 so a sub-millisecond render still moves the total.

Every group has at least one receiver. `TestSignalGroupsCovered` proves it by walking the dashboard and asserting that every signal in the table fires at least once. Three of them never fire on a plain render, so the test provokes a settings reload, a provider definition, and a watch-spec resolution inside the recorder window.

### 6. The filter form, time-bucketing, and `action_dispatched`

`WindowFilterForm` carries one `ChoiceField`. Subclassing `next.forms.Form` in [obs/forms.py](obs/forms.py) auto-registers the action as `window_filter_form`. Its `on_valid` reads `is_partial_request`. A non-partial apply returns a `HttpResponseRedirect` with `?window=...` so subsequent full renders inherit the new window through the `@context("window", inherit_context=True)` callable in `obs/dashboards/stats/page.py`. The `window` callable reads the posted value first, so a partial apply re-aggregates without a context override the provider chain would ignore.

Behind the form, `metrics.incr` writes both a cumulative counter and a minute-floor bucket key. `metrics.read_window(kind, minutes)` sums every bucket whose timestamp is inside `[now - minutes, now]`. Bucket keys expire after `BUCKET_TTL_SECONDS = 3700`, just past the widest window the dashboard offers, so an hour-long view is always complete while the store stays bounded between flushes. The read path is otherwise side-effect free: it drops index entries whose bucket already expired but never evicts a live bucket, so a `read_window(kind, 5)` call cannot starve the next `read_window(kind, 60)`. The `live_stats` page-level context calls `read_window` so the chosen window narrows the aggregation in real time. The four cumulative sub-pages (`/stats/pages/`, `/stats/components/`, `/stats/forms/`, `/stats/static/`) keep using `read_kind` for the lifetime totals.

The form component lives under [`_widgets/filter_window/`](obs/dashboards/_widgets/filter_window/). It uses `{% form "window_filter_form" %}` so submission goes through the framework dispatcher and `forms.action_dispatched` fires end to end, not only in tests. The template spells the block out twice behind an `{% if live_zone %}`, because the tag resolves `zone=` and writes the attribute either way, so an empty value would still stamp `data-next-target=""` on a page that declares no zone. Inside the block the template renders the bound field as `{{ form.window }}` — the styled `Select` widget declared on the form — and `WindowFilterForm.get_initial(request)` seeds it with the window currently on the query string, so the select always shows the active choice.

### 7. A project-defined patch verb, `metric-pulse`

The live page on `/stats/` wraps its totals in a `{% zone "live-totals" %}` and passes the index-only `live_zone` context to the filter form, so the form there carries `data-next-target="live-totals"` and applies as a partial. `ObsConfig.ready` calls `register_patch_op("metric-pulse")`, which clears the `next.E066` check and earns the generic `op()` channel on the builder. A partial apply returns

```python
Patches(request).morph(zone="live-totals").morph(zone="stats-window").op(
    "metric-pulse", window=chosen, selector="[data-metric-pulse-target]"
).response()
```

so the envelope carries the re-aggregated zone beside the custom verb with a payload the server authored. The `Window:` label sits in its own `{% zone "stats-window" %}` inside [`stats/layout.djx`](obs/dashboards/stats/layout.djx), which is what the second morph addresses. A zone may live anywhere in the composed template, layout chain included, and morphing the label alongside the cards keeps the heading from naming a window the numbers no longer cover. The client side is supplied by a co-located [`stats/template.js`](obs/dashboards/stats/template.js) that calls `window.Next.partial.defineOp("metric-pulse", ...)` to flash the refreshed numbers, paired with a [`stats/template.css`](obs/dashboards/stats/template.css) keyframe. Both load only on the live page because they are co-located with its template. The verb is an enhancement, the no-JavaScript path still redirects with `?window=...`.

### 8. The flush command

```python
def handle(self, *args, **options):
    rows = flush()
    if not rows:
        self.stdout.write("nothing to flush")
        return
    MetricSnapshot.objects.bulk_create(
        [MetricSnapshot(kind=k, key=key, value=v) for k, key, v in rows]
    )
    self.stdout.write(self.style.SUCCESS(f"flushed {len(rows)} counters"))
```

The command drains every counter (cumulative and bucketed) and clears the index in a single pass. Calling it twice in a row is safe: the second call sees an empty store and exits immediately.

[obs/management/commands/flush_metrics.py](obs/management/commands/flush_metrics.py).

## Out of scope

The dashboard renders every counter without pagination, so a long-lived process with thousands of distinct page paths will eventually need a top-N filter. The cumulative counters never expire on their own either, they only go away with `flush_metrics`. Fine for an example, worth calling out before adopting the pattern in production.

## Further reading

- Aggregated signal catalogue at [docs/content/ref/signals.rst](../../docs/content/ref/signals.rst).
- JS context serialization and the per-decorator serializer override at [docs/content/topics/static-assets/js-context.rst](../../docs/content/topics/static-assets/js-context.rst).
