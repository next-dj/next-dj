# Observability dashboard

A self-hosted dashboard that watches the framework from the inside. Signals from nine framework subsystems land in eighteen receivers, accumulate in an in-process counter store, and surface in a table, a Chart.js bar chart, or a React sparkline. Five more receivers turn the realtime signals into Prometheus series behind a `/metrics/` endpoint. Nothing here monkey-patches the framework, every hook is a documented extension point.

The page tree is `obs/pages/`. The components live next to the pages that use them. The `JS_CONTEXT_SERIALIZER` setting points at a custom class. Two pages declare their CDN dependencies through page-level `scripts = [...]` lists so the framework collects them, dedupes them, and emits them through `{% collect_scripts %}` ahead of every widget's co-located file. One of those lists also holds a staticfiles name beside a vendor URL, and an opt-in profile runs the project on `ManifestStaticFilesStorage` so the name picks up a content hash the hardcoded path never would. Two co-located widgets read `window.Next.context.<key>`, the sparkline through a per-decorator serializer override that wraps the payload in a versioned envelope, the bar chart flat through the global default. The entire frontend arrives from CDNs, so the dashboard runs without `npm`. The static collector picks up `.css`, `.js`, and `.jsx` files, the last through a custom `BabelJsxBackend` that emits `<script type="text/babel">` tags.

## What you will see

| URL | Description |
| --- | --- |
| `/` | Overview with four headline counters in a polling zone, a React sparkline, and a lazy-loaded busiest-pages widget. |
| `/stats/` | Live page with the Chart.js bar chart and the window filter. `?window=1m`, `?window=5m`, and `?window=1h` each re-aggregate over real minute buckets. |
| `/stats/pages/` | Per-page render counts pulled from the cumulative `pages.rendered` kind. |
| `/stats/components/` | Per-component render counts. |
| `/stats/forms/` | Action dispatch and validation-failure counts. |
| `/stats/static/` | Asset, dedup, and HTML-injection totals. |
| `/realtime/` | Live streams per transport, publications, upstream nudges, and unsubscribes by reason, read back from the Prometheus registry. The page holds its own topic stream, so opening it moves the numbers it shows. |
| `/realtime/stream/` | The `TopicStream` of the `obs:pulse` topic the realtime page subscribes to, over a WebSocket under ASGI and over SSE otherwise. |
| `/metrics/` | The Prometheus exposition. Open under `DEBUG`, a 404 otherwise, and a bearer token once `OBS_METRICS_TOKEN` is set. |
| `POST` to `window_filter_form` | Re-aggregates under the chosen window. A partial apply from `/stats/` morphs the `live-totals` zone plus the `stats-window` heading label and emits the custom `metric-pulse` verb, otherwise it redirects with `?window=...`. |

The filter form uses the framework `{% form "window_filter_form" %}` tag. Submitting it fires `forms.action_dispatched` so the example exercises the full form path without adding a model write. From the live page the form targets the `live-totals` zone, so the apply rides the partial protocol and ships a project-defined verb beside the built-in morph.

The overview shows the other two zone modes. The stat-tile grid sits in `{% zone "overview-totals" poll="5s" %}`, so the client re-GETs the zone every five seconds and the counters refresh without a reload. Each poll tick re-renders the four tiles, so the components counter also counts the poll's own renders, which shows that the polling works. The busiest-pages widget below the sparkline is a `lazy="load"` zone: the first paint ships only its `{% placeholder %}` branch and the body arrives through one batched zone-GET as soon as the page loads. The `totals` provider is declared as `@context("totals", zone="overview-totals")`, so the lazy GET for the widget skips the four counter aggregations it would never read, while the full render still runs them.

## How to run

```bash
cd examples/observability
uv run python manage.py migrate
uv run python manage.py runserver        # http://127.0.0.1:8000/
uv run pytest
```

`next` sits above `django.contrib.staticfiles` in `INSTALLED_APPS`, so `runserver` is the next.dj command that serves `ASGI_APPLICATION` on uvicorn and mounts the WebSocket. Open `/realtime/` in two tabs, press **Nudge** in one, and both tabs count the upstream event. `curl http://127.0.0.1:8000/metrics/` prints the same numbers in the Prometheus text format.

Click around `/`, `/stats/`, the four sub-pages. Apply different windows. Counters move on every render, the overview tiles re-poll on their own every five seconds, and the windowed view actually narrows to the chosen aggregation slice.

```bash
uv run python manage.py flush_metrics
```

Drains both the cumulative and the bucketed counters into the `obs.MetricSnapshot` table and empties the cache. The next render starts at zero.

```bash
OBS_STATIC_MANIFEST=1 uv run python manage.py collectstatic --noinput
OBS_STATIC_MANIFEST=1 uv run python manage.py runserver
```

Runs the same project on `ManifestStaticFilesStorage`, the way a deployment serves it. Every asset URL then carries a content hash, and a render that asks for a file the manifest does not hold raises `StaticAssetNotFoundError` naming that file, so the profile is opt-in and `collectstatic` comes first. Section 4 walks what changes in the rendered HTML.

Tailwind loads from a CDN in the shared `page_head` component pulled into `instrument/layout.djx`. Chart.js is loaded through the framework static collector. The URL is declared in the page-level `scripts` list of `obs/pages/stats/page.py` and reaches the page via `{% collect_scripts %}`. Page-level scripts land in the injection order before every widget's co-located file, so the chart widget's `component.js` always finds `window.Chart` defined. React, ReactDOM, and Babel-standalone follow the same path from the `scripts` list in `obs/pages/page.py`, which keeps them ahead of the sparkline's own `component.jsx`. There is no build step.

Browser-side Babel is a full-page technique, so the sparkline is the one widget that renders only on a full page load. It sits outside every zone and its `component.jsx` is never sent in a patch envelope. The framework says as much through `next.W074`, which `config/settings.py` silences on purpose with a comment. The silencing is honest only while that invariant holds, so `TestSparklineStaysOutsideEveryZone` in the e2e suite re-GETs every zone the dashboard declares and fails if the sparkline mount ever shows up in a zone body.

Both chart widgets mount through `Next.partial.onMount` with a WeakMap keyed by the host element, so a partial morph that reconciles the host in place never creates a second Chart.js instance or React root. A delegated `next:removed` listener walks the detached subtree and destroys the chart or unmounts the root when the host actually leaves the document.

## Walking the code

### 1. Co-location tree

```
instrument/
└── layout.djx                <- root html shell, shared page_head brings Tailwind from CDN
static/
└── dashboards/js/chart_theme.js   <- project asset named by stats/page.py, hashed under the manifest profile
obs/
├── apps.py                   <- registers the jsx kind and the metric-pulse verb
├── models.py                 <- MetricSnapshot persisted by flush_metrics
├── forms.py                  <- WindowFilterForm, partial apply emits metric-pulse
├── metrics.py                <- LocMemCache counter API, bucketed and cumulative
├── backends.py               <- CountingComponentsBackend, BabelJsxBackend
├── static_policies.py        <- InstrumentedDedup
├── serializers.py            <- PydanticJsContextSerializer + WrappedJsContextSerializer
├── signals.py                <- eighteen counter receivers, four Prometheus receivers
├── exporter.py               <- Prometheus series, topic families, the scrape guard
├── realtime.py               <- obs:pulse topic, its authorizer, the `nudge` upstream handler
├── management/commands/flush_metrics.py
└── pages/
    ├── page.py               <- @context totals
    ├── template.djx          <- poll zone with the overview grid, React sparkline, lazy busiest-pages widget
    ├── _components/
    │   ├── counter_list/     <- list/table widget shared by every stats subpage
    │   ├── busiest_pages/    <- lazy-zone body, component-level top_by_kind lookup
    │   ├── stats_nav/        <- nav tabs rendered from a Python list
    │   ├── filter_window/    <- window filter form (template-only component)
    │   ├── render_chart/     <- Chart.js bar chart, chart.js cdn via stats/page.py
    │   └── sparkline/        <- React + JSX sparkline, full render only, react/babel cdn via page.py
    ├── realtime/
    │   ├── page.py           <- @context realtime, bound to the realtime-totals zone
    │   ├── template.djx      <- totals zone, pulse form, Nudge button, data-next-stream
    │   ├── template.js       <- Nudge sends the upstream event, co-located
    │   └── stream/page.py    <- TopicStream on obs:pulse
    ├── metrics/page.py       <- generate_latest behind check_scrape
    └── stats/
        ├── layout.djx        <- nested layout, tabs, filter form chrome
        ├── page.py           <- @context live_stats, live_zone, scripts=[chart.js cdn, chart_theme name]
        ├── template.djx      <- live-totals zone
        ├── template.js       <- defineOp("metric-pulse") flash handler, co-located
        ├── template.css      <- metric-pulse keyframe, co-located
        ├── pages/
        ├── components/
        ├── forms/
        └── static/
```

The `_components/` directory sits directly under `pages/` so the overview page and every nested page see the same set. `stat_card`, `page_header`, `app_shell`, and `nav_link` come from the shared kit in [`../_shared/_components/`](../_shared/_components/) instead, wired through `COMPONENT_BACKENDS[0]["DIRS"]`. Co-located CSS, JS, and JSX files are picked up by the static collector automatically. The dedup policy filters duplicates so a CDN script referenced by four components still ships once.

[obs/pages/](obs/pages/) is the page tree. The nested `stats/pages/` is an ordinary segment that serves `/stats/pages/`, because only the app-level `pages/` is a page root.

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

### 4. Staticfiles names, vendor URLs, and the hashed manifest

The Chart.js page declares two scripts in one list in [`obs/pages/stats/page.py`](obs/pages/stats/page.py):

```python
scripts = [
    "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js",
    "dashboards/js/chart_theme.js",
]
```

The first entry carries a scheme and a host, so it is a URL and the pipeline emits it as written. The second has no scheme, no host, no query and no leading slash, so it is a staticfiles name and Django storage answers for it. Both are declared the same way, in the same list, and the shape of the value is what decides. [`static/dashboards/js/chart_theme.js`](static/dashboards/js/chart_theme.js) copies the design tokens onto `Chart.defaults`, and it sits after the vendor URL because page-level scripts keep their declared order and it needs `window.Chart`.

Every other example serves its assets off disk, where a name and a hardcoded `/static/...` path render the same string and the difference looks academic. This one ships the production profile that makes the difference visible:

```bash
OBS_STATIC_MANIFEST=1 uv run python manage.py collectstatic --noinput
OBS_STATIC_MANIFEST=1 uv run python manage.py runserver
```

The environment variable puts `STORAGES["staticfiles"]` on `ManifestStaticFilesStorage`, which is the shape a deployment uses and not one a dev server wants, since every URL then has to be in a manifest `collectstatic` has already written. The mapping itself lives in [`config/storages.py`](config/storages.py), so the settings branch and the test that exercises it read the same one. Under that profile the same page renders

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script src="/static/dashboards/js/chart_theme.<hash>.js"></script>
<script src="/static/next/components/render_chart.<hash>.js"></script>
```

The name picked up a content hash, the vendor URL did not, and the widget's co-located file picked one up too, because `next.static.NextStaticFilesFinder` exposes co-located assets to `collectstatic` under the same `next/` namespace the pipeline resolves them through. Writing `/static/dashboards/js/chart_theme.js` by hand in the same list produces that exact string instead. `collectstatic` copies the unhashed file next to the hashed one, so nothing breaks loudly. It is simply a URL that stays the same while the file behind it changes, which is the failure mode a long cache header turns into a support ticket. A name is what buys the hash, and the hash is what makes an asset URL safe to cache for a year.

That manifest is also where the asset version of a partial response comes from, so this is the one example in the catalog that names no version anywhere. Under the profile the stamp follows the manifest hash and a `collectstatic` moves it, off the profile it falls back to a constant. The other examples serve their assets straight off disk with no manifest to read, so they name a release tag by hand.

`TestManifestStorage` in [`tests/test_integration.py`](tests/test_integration.py) runs `collectstatic` into a temporary root under that profile and asserts the rendered page against it, so the hashing path is exercised by `make test-examples` rather than described here and left to rot. It also asserts that the collected tree holds no Python module, which is what lets the command above run without ignore flags.

### 5. The pluggable JS context serializer at three levels

```python
NEXT_FRAMEWORK = {
    ...,
    "JS_CONTEXT_SERIALIZER": "obs.serializers.PydanticJsContextSerializer",
}
```

The global default produces flat JSON for every key reaching `window.Next.context`. Two callables override it with `WrappedJsContextSerializer`, which wraps the payload in `{"v": 1, "data": ...}`. The first override sits on a page-level `@context` in `obs/pages/stats/page.py`:

```python
@context(
    "live_stats",
    inherit_context=True,
    serialize=True,
    serializer=WrappedJsContextSerializer(),
)
def live_stats(window: str = "5m") -> dict[str, Any]: ...
```

The second sits on a `@component.context` in `_components/sparkline/component.py`:

```python
@component.context(
    "totals_chart", serialize=True, serializer=WrappedJsContextSerializer()
)
def totals_chart(totals: dict[str, int]) -> dict[str, Any]: ...
```

The framework records each override at the static collector level. At inject time the rendered HTML carries `"live_stats":{"v":1,"data":{...}}` and `"totals_chart":{"v":1,"data":{...}}`, while the sibling `render_rates` key from `_components/render_chart/component.py` stays flat through the global default. Three demonstrations, one HTML response, no per-key code branching.

The same `Next._init(...)` payload carries the keys the framework owns outright. `$csrf` travels on every render, `$dev` only while `DEBUG` is on, and both are off limits to your own `@context` keys. A collision is reported as `next.W075` and the registered value never reaches `window.Next.context`. `TestDevFlagChannel` in the e2e suite pins both halves of the `$dev` contract.

### 6. The receivers and the counter keys

[obs/signals.py](obs/signals.py) wires the framework signals of nine groups. Every receiver delegates to `metrics.incr(kind, key)`, which bumps both the cumulative counter and the current minute bucket. The handlers stay thin because the example is a map between signal names and metric keys.

A counter is addressed by a `kind` and a `key`. When the interesting part of an event is its subject, the kind names the family and the key carries the subject: a page path, a component name, or an action name. When the event is a bare occurrence, the kind is the group and the key is the signal name.

| Signal group | Receivers | Counter kind | Counter key |
| --- | --- | --- | --- |
| conf | `settings_reloaded` | `conf` | `settings_reloaded` |
| deps | `provider_registered` | `deps` | `provider_registered` |
| pages | `template_loaded`, `context_registered`, `page_rendered` | `pages.template`, `pages.context`, `pages.rendered`, `pages.duration_ms_total` | page path |
| urls | `route_registered` | `urls.route` | URL path |
| urls | `router_reloaded` | `urls` | `router_reloaded` |
| components | `component_registered`, `components_registered`, `component_rendered` | `components.registered`, `components.rendered` | component name |
| forms | `action_registered`, `action_dispatched`, `form_validation_failed` | `forms.action_registered`, `forms.action_dispatched`, `forms.validation_failed` | action name |
| static | `asset_registered`, `collector_finalized`, `html_injected` | `static` | `asset_registered`, `collector_finalized`, `html_injected`, `injected_bytes_total` |
| server | `watch_specs_ready` | `server` | `watch_specs_ready` |
| backends | `component_backend_loaded`, `form_backend_loaded`, `partial_backend_loaded`, `router_backend_loaded`, `static_backend_loaded`, `wizard_backend_loaded` | `backends` | backend class name |

The page signals carry an absolute path, so `page_key` in [obs/signals.py](obs/signals.py) rewrites it relative to `BASE_DIR` before it becomes a counter key. Without that step `/stats/pages/` would publish the machine's home directory in every row.

`components_registered` is the bulk twin of `component_registered`, so its receiver loops over the payload and reuses the same kind. The framework imports `obs/signals.py` before it discovers components, so `components.registered` already counts every shared kit component when the process starts. `/stats/components/` reads `components.rendered` instead, so a fresh process shows its empty message there until a page renders. `page_rendered` feeds two counters: one render count and one accumulated `duration_ms_total`, floored to at least 1 so a sub-millisecond render still moves the total.

The `backends` group is one receiver for six signals. Every settings-driven backend family (components, form actions, the form wizard, the file router, static assets, and partial rendering) announces a built backend through the same `config` and `instance` payload, so `on_backend_loaded` takes the tuple of all six and keys the counter by `type(instance).__name__`. Nothing in the handler knows which family sent it, which is the point: a new backend family joins the dashboard by being added to `BACKEND_LOADED_SIGNALS`.

Every group has at least one receiver. `TestSignalGroupsCovered` proves it by walking the dashboard and asserting that every group in the table increments at least once. Three signals never fire on a plain render, so the test provokes a settings reload, a provider definition, and a watch-spec resolution inside the recorder window. The settings reload is also what re-arms the `backends` group: a manager builds its backends once and caches them until the settings version moves.

### 7. The filter form, time-bucketing, and `action_dispatched`

`WindowFilterForm` carries one `ChoiceField`. Subclassing `next.forms.Form` in [obs/forms.py](obs/forms.py) auto-registers the action as `window_filter_form`. Its `on_valid` reads `is_partial_request`. A non-partial apply returns a `HttpResponseRedirect` with `?window=...` so subsequent full renders inherit the new window through the `@context("window", inherit_context=True)` callable in `obs/pages/stats/page.py`. The `window` callable reads the posted value first, so a partial apply re-aggregates without a context override the provider chain would ignore.

Behind the form, `metrics.incr` writes both a cumulative counter and a minute-floor bucket key. `metrics.read_window(kind, minutes)` sums every bucket whose timestamp is inside `[now - minutes, now]`. Bucket keys expire after `BUCKET_TTL_SECONDS = 3700`, just past the widest window the dashboard offers, so an hour-long view is always complete while the store stays bounded between flushes. The read path is otherwise side-effect free: it drops index entries whose bucket already expired but never evicts a live bucket, so a `read_window(kind, 5)` call cannot starve the next `read_window(kind, 60)`. The `live_stats` page-level context calls `read_window` so the chosen window narrows the aggregation in real time. The four cumulative sub-pages (`/stats/pages/`, `/stats/components/`, `/stats/forms/`, `/stats/static/`) keep using `read_kind` for the lifetime totals.

The form component lives under [`_components/filter_window/`](obs/pages/_components/filter_window/). It uses `{% form "window_filter_form" %}` so submission goes through the framework dispatcher and `forms.action_dispatched` fires end to end, not only in tests. The template spells the block out twice behind an `{% if live_zone %}`, because the tag resolves `zone=` and writes the attribute either way, so an empty value would still stamp `data-next-target=""` on a page that declares no zone. Inside the block the template renders the bound field as `{{ form.window }}`, the styled `Select` widget declared on the form, and `WindowFilterForm.get_initial(request)` seeds it with the window currently on the query string, so the select always shows the active choice.

### 8. A project-defined patch verb, `metric-pulse`

The live page on `/stats/` wraps its totals in a `{% zone "live-totals" %}` and passes the index-only `live_zone` context to the filter form, so the form there carries `data-next-target="live-totals"` and applies as a partial. `ObsConfig.ready` calls `register_patch_op("metric-pulse")`, which makes the verb available through the generic `op()` channel on the builder. An unregistered verb is refused at build time with `UnknownPatchOpError`. `register_patch_op` raises `BuiltinPatchOpError` for a name that shadows a built-in verb, and the system check `next.E090` reports a name that is no valid verb token. A partial apply returns

```python
Patches(request).morph(zone="live-totals").morph(zone="stats-window").op(
    "metric-pulse", window=chosen, selector="[data-metric-pulse-target]"
).response()
```

so the envelope carries the re-aggregated zone beside the custom verb with a payload the server authored. The `Window:` label sits in its own `{% zone "stats-window" %}` inside [`stats/layout.djx`](obs/pages/stats/layout.djx), which is what the second morph addresses. A zone may live anywhere in the composed template, layout chain included, and morphing the label alongside the cards keeps the heading from naming a window the numbers no longer cover. The client side is supplied by a co-located [`stats/template.js`](obs/pages/stats/template.js) that calls `window.Next.partial.defineOp("metric-pulse", ...)` to flash the refreshed numbers, paired with a [`stats/template.css`](obs/pages/stats/template.css) keyframe. Both load only on the live page because they are co-located with its template. The verb is an enhancement, the no-JavaScript path still redirects with `?window=...`.

### 9. The flush command

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

### 10. Titles and a `noindex` dashboard

[`instrument/layout.djx`](instrument/layout.djx) calls the shared `page_head` component without a title, and the builtin `{% metadata %}` tag inside it folds `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` from [`config/settings.py`](config/settings.py) with what the page tree declares. The root [`pages/page.py`](obs/pages/page.py) declares a dict with no title in it:

```python
metadata: MetadataDict = {"robots": {"index": False}}
```

A dict is inherited by every page below it, so the whole dashboard renders `<meta name="robots" content="noindex">` from that one line. Render counts, dispatch totals and asset statistics describe one running process, and none of it belongs in a search index. The overview renders the settings `default` because the root names no title, and each stats page adds its own with a one-line dict, `Live stats` on `/stats/` and `Page renders`, `Component renders`, `Form actions` and `Static pipeline` below it. One parametrized integration test pins all six titles and the robots tag.

### 11. Realtime streams in Prometheus

The realtime signals describe every stream from the moment it holds a broker subscription to the moment it lets go, so a metrics exporter needs nothing but receivers. [obs/exporter.py](obs/exporter.py) declares seven `prometheus_client` series in the default registry, and [obs/signals.py](obs/signals.py) moves them.

| Series | Type | Labels | Fed by |
| --- | --- | --- | --- |
| `next_realtime_streams_open` | Gauge | `transport` | `stream_subscribed` adds one, `stream_unsubscribed` takes it back |
| `next_realtime_stream_unsubscribes_total` | Counter | `transport`, `reason` | `stream_unsubscribed`, `reason` is the `UnsubscribeReason` value |
| `next_realtime_stream_duration_seconds` | Histogram | `transport` | `stream_unsubscribed`, its `duration` on the monotonic clock |
| `next_realtime_messages_published_total` | Counter | `family` | `message_published` for a numbered message |
| `next_realtime_presence_snapshots_total` | Counter | `family` | `message_published` with `ephemeral=True` |
| `next_realtime_upstream_events_total` | Counter | `name` | `upstream_received` |
| `next_realtime_upstream_rejected_total` | Counter | `reason` | `upstream_rejected`, `reason` is the `UpstreamError` value |

```python
@receiver(stream_unsubscribed)
def on_stream_unsubscribed(
    transport: str, duration: float, reason: str, **kwargs
) -> None:
    """Release the live stream, count why it ended and observe how long it lived."""
    STREAMS_OPEN.labels(transport=transport).dec()
    STREAM_UNSUBSCRIBES.labels(transport=transport, reason=str(reason)).inc()
    STREAM_DURATION.labels(transport=transport).observe(duration)
```

`transport` is `sse` or `ws`. `ObsConfig.ready` calls `seed_series`, which creates every child of the bounded labels up front, so each transport and each reason exports a zero before the first stream closes and `rate()` has a series to work on.

The stream signals go out with `send_robust`, so a receiver that raises is logged on `next.realtime.signals` and the stream stays open. `TestSseStream` connects a raising receiver beside these and asserts that the stream still delivers and the gauge still moves. `message_published`, `upstream_received` and `upstream_rejected` go out with `send_robust` too, so a receiver that raises is logged and the publication or the `ack` goes on. These receivers only touch a label and a number, nothing that can fail.

`/realtime/` is the page the numbers are shown on. Its [`stream/page.py`](obs/pages/realtime/stream/page.py) returns `TopicStream(request, "obs:pulse", authorize=can_watch)`, and the importable `can_watch` is what lets the stream negotiate a WebSocket. **Publish a pulse** posts `PulseForm`, which publishes `refresh(zone="realtime-totals")` on the topic and redirects back. **Nudge** sends the `nudge` upstream event through `Next.partial.stream.send`, and the handler in [obs/realtime.py](obs/realtime.py) publishes the same refresh, so every open tab re-reads the totals. Any visitor may nudge, and one `nudge` makes every open tab refetch the zone, an amplification only `REALTIME['UPSTREAM']['RATE']` bounds per connection. Over SSE there is no upstream channel and the button does nothing.

#### Cardinality

Every distinct label value is its own time series in Prometheus, kept and indexed for the whole retention period. A label set by the full topic would grow one series per poll, per board, or per user, and a busy site would take its Prometheus down with it. The `family` label is therefore the part of the topic before the first colon, and only when it is listed in `TOPIC_FAMILIES`. Every other topic counts under `other`. `obs:pulse` and `obs:item-17` both count as `obs`, `poll:17` and a colon-less `user17` count as `other`, so the label carries at most `len(TOPIC_FAMILIES) + 1` values whatever the application publishes. `TestTopicCardinality` publishes ninety topics and finds two series. Add a family to the set when it deserves its own line on a dashboard.

Presence snapshots also go through `message_published`, flagged `ephemeral=True`. They have their own `presence_snapshots` series instead of being dropped, because a crowd that joins and leaves fires one snapshot per change and would otherwise inflate the publication rate, while the snapshot rate is still worth a panel of its own.

The `name` label of the upstream counter stays bounded for a different reason. `upstream_received` fires only for an event that matched a registered `@realtime.on` handler, so the label carries at most one value per handler. An event the page invents gets a failed `ack` and no series there. `upstream_rejected` reports it instead, and its `reason` label is the closed `UpstreamError` enum, so the counter has seven series whatever a client sends. The `topic` and `name` the signal carries are what the client sent, unchecked, so the receiver never turns them into labels.

#### The `/metrics/` endpoint

[`metrics/page.py`](obs/pages/metrics/page.py) answers with `generate_latest(REGISTRY)` and `CONTENT_TYPE_LATEST`, so the output also holds the process and garbage-collector series `prometheus_client` registers by default. `check_scrape` keeps it closed outside development.

- Without `OBS_METRICS_TOKEN` the endpoint answers under `DEBUG` and is a 404 otherwise.
- With `OBS_METRICS_TOKEN` set in the environment it answers only `Authorization: Bearer <token>`, the scheme matched without regard to case as RFC 9110 asks and the token compared in constant time, and refuses anything else with 403.

```yaml
scrape_configs:
  - job_name: observability
    metrics_path: /metrics/
    authorization:
      credentials_file: /etc/prometheus/obs-token
    static_configs:
      - targets: ["obs.internal:8000"]
```

In production the endpoint is better kept off the public internet altogether: a reverse proxy that serves `/metrics/` only to the monitoring network, or a separate internal port, with the token as a second line. The series name no user, but they tell an outsider how many people are connected and when.

#### PromQL

```promql
# Live streams per transport, across every scraped process
sum by (transport) (next_realtime_streams_open)

# Unsubscribes per second by reason over five minutes
sum by (reason) (rate(next_realtime_stream_unsubscribes_total[5m]))

# Share of streams that ended in an error
sum(rate(next_realtime_stream_unsubscribes_total{reason="error"}[5m]))
  / sum(rate(next_realtime_stream_unsubscribes_total[5m]))

# Median stream length per transport
histogram_quantile(0.5, sum by (le, transport) (rate(next_realtime_stream_duration_seconds_bucket[1h])))

# Publications per second by topic family
sum by (family) (rate(next_realtime_messages_published_total[1m]))
```

A rising `revoked` rate means sessions or grants are failing their rechecks, a rising `error` rate points at the broker, and `server` covers protocol violations and rate limits on a WebSocket.

#### Several worker processes

The code here is deliberately single-process. Every uvicorn or gunicorn worker keeps its own registry in memory, so a scrape through a load balancer reads whichever worker answered. Two ways out exist.

- Scrape each worker on its own address, which Prometheus sums with `sum by (...)` as above.
- Run `prometheus_client` in multiprocess mode. Point `PROMETHEUS_MULTIPROC_DIR` at an empty directory before the workers start, wipe it on every deploy, build a fresh `CollectorRegistry` with `multiprocess.MultiProcessCollector` in the view instead of the default `REGISTRY`, and call `multiprocess.mark_process_dead(pid)` from the server's child-exit hook.

Multiprocess mode changes the Gauge. Each worker writes its own file, and the gauge needs a `multiprocess_mode` that says how to fold them, `livesum` for the open-streams gauge. The live modes drop a worker only when `mark_process_dead` removes its file, so a worker that dies past the exit hook keeps its last count in the sum until the directory is wiped. Process and GC metrics are not part of a multiprocess registry.

A receiver connected while a stream is open never sees that stream close, as the signals reference spells out. These receivers connect at startup, before the first stream, so the gauge never drops below zero.

## Out of scope

The dashboard renders every counter without pagination, so a long-lived process with thousands of distinct page paths will eventually need a top-N filter. The cumulative counters never expire on their own either, they only go away with `flush_metrics`. Fine for an example, worth calling out before adopting the pattern in production.

## Further reading

- Aggregated signal catalogue at [docs/content/ref/signals.rst](../../docs/content/ref/signals.rst), with the stream lifecycle and `UnsubscribeReason`.
- Realtime topics, WebSockets, and upstream events at [docs/content/topics/realtime.rst](../../docs/content/topics/realtime.rst).
- JS context serialization and the per-decorator serializer override at [docs/content/topics/static-assets/js-context.rst](../../docs/content/topics/static-assets/js-context.rst).
- The metadata chain that folds the dicts of section 10 at [next/pages/metadata/](../../next/pages/metadata/).
