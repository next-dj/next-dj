# Live polls

A polling app where the results on every open tab refresh the moment someone votes. The framework SSE bridge streams patch envelopes out of an in-process broker. Each event is a `refresh` patch, so every tab re-fetches the results zone with its own cookies through the page view, and no foreign HTML ever travels on the stream. A locally bundled Vue 3 island sits on top of the server-rendered chart. The example demonstrates how a streaming endpoint, a signal-driven fan-out, and a Vite-bundled Vue layer compose into one feature without any ad-hoc plumbing in the framework core.

## What you will see

| URL | Description |
| --- | --- |
| `/` | Redirects to `/polls/` so the bare site root is never empty. |
| `/polls/` | Server-rendered list of polls. Each card shows choice count and total votes. |
| `/polls/<id>/` | Vote page with the live chart, a button-per-choice form, and a `data-next-sse` element. |
| `/polls/<id>/stream/` | The patch event stream. Each poll change emits a `next-patches` event carrying a `refresh` of the `poll-results` zone. |
| `POST vote_form` | Atomically increments a choice via `F("votes") + 1`, then morphs the `poll-results` zone and pushes the fresh counts to `window.Next.context.live_results`. A signal receiver publishes the change to the broker with the request id. |

Two demo polls live in [`polls/demo.py`](polls/demo.py) and load through `manage.py seed_demo` ("Tabs or spaces?" and "Vim or Emacs?", two choices each) so the index page is never empty once seeded.

## How the live update works

The vote page wraps its results in a zone and connects the stream with one element.

```jinja
{% zone "poll-results" %}
  {% component "poll_chart" %}
{% endzone %}
<div data-next-sse="/polls/{{ poll.pk }}/stream/"></div>
```

The runtime opens one `EventSource` on the `data-next-sse` URL and routes each `next-patches` event into the same apply pipeline an HTTP partial response uses. There is no hand-written `EventSource` code.

A vote posts with `X-Next-Request-Id`. A valid partial vote answers the voter's own tab with two ops on one envelope, a morph of the `poll-results` zone with the fresh server-rendered bars and a `context` patch that pushes the new snapshot into `window.Next.context.live_results`. The `live_results` name is a page-level `serialize=True` provider on `[int:id]/page.py`, so `Patches(request).context(live_results=...)` resolves it against the origin page the vote posted from. The Vue island reads the pushed snapshot on `context-updated` and rebinds without re-reading the DOM. A signal receiver then publishes the change to the broker carrying the request id. The stream page yields one `refresh` envelope per change, stamped with the request id as the echo. A bad choice never reaches the handler, so the dispatcher auto-morphs the `poll-results` zone in place because the vote form lives inside it, and the voter keeps the rest of the page. Without the runtime the vote falls back to a redirect to the poll page.

```python
def patch_source(request: HttpRequest, poll_id: int) -> Iterator[Patches]:
    for change in broker.changes(poll_id):
        yield Patches(request, echo_of=change.request_id).refresh(zone="poll-results")
```

Every subscriber receives the same envelope. The voter's own tab finds the request id in its echo ring buffer and drops the event, its POST already brought the fresh zone. Every other tab executes the `refresh` and re-fetches `poll-results` with its own cookies through the poll page view, so authorization is re-checked per subscriber and the server-rendered bars come back current.

The fan-out is built on `refresh` rather than a context patch on purpose. A context patch carries a serialize provider's value read from the origin page of the request that builds it, and a stream source has no page-render origin to read from. A stream that needs fresh data drives a `refresh`, and the re-fetched zone delivers the new state through its own render. The vote handler, by contrast, runs on a real page-render origin, so it can pair its zone morph with a `context` patch that hands the voter's own tab the snapshot directly. So two update paths coexist, a `context` patch on the voter's own response and a `refresh` for every other tab, and the chart updates by re-rendering the server-side bars in the `poll-results` zone either way.

## How to run

Local development (HMR for Vue, autoreload for Django):

```bash
cd examples/live-polls
uv run python manage.py migrate
uv run python manage.py seed_demo
npm install
npm run dev                            # terminal A: http://localhost:5173
uv run python manage.py runserver     # terminal B: http://127.0.0.1:8000/polls/
```

`seed_demo` writes the two demo polls, and migrations carry schema only. Editing `component.vue` then hot-reloads in the browser without restarts, and the `@vite/client` script loads through the `collector_finalized` signal on every page that carries Vue assets. The Django reloader picks up Python edits on its own.

[`config/settings.py`](config/settings.py) picks the asset mode without asking the operator for a flag, in this order:

1. A non-empty `VITE_DEV_ORIGIN` environment variable wins, whatever it names.
2. Otherwise, if `polls/static/polls/dist/.vite/manifest.json` is absent, the origin defaults to `http://localhost:5173`. A checkout that has never been built is a checkout being developed, so `runserver` plus `npm run dev` needs no env-var ceremony.
3. Otherwise the origin stays empty and `ViteManifestBackend` resolves through the built manifest.
4. Under `pytest` the origin is forced to `http://test-vite.invalid`, so the static collector stops short of a manifest the test suite never builds and no test reaches the network.

Without `npm run dev` running, rule 2 still points the `<script type="module">` tags at `localhost:5173` and the browser fails to fetch them. The page itself is unaffected: the server-rendered bars, the vote buttons, and the `{% form %}` post-and-redirect path all come from Django, and only the live chart stays frozen until the dev server or a build is there.

A typed route segment such as `[int:id]` puts a colon in the directory name, and the Vite dev server refuses to serve a path holding one before it consults `server.fs.allow`. The poll page and its chart widget live under exactly such a segment, so both [`vite.config.ts`](vite.config.ts) and [`vitest.config.ts`](vitest.config.ts) carry `server: { fs: { strict: false } }`. Only the dev server applies that guard, so the production build and Django static serving stay untouched.

Production-shaped run (no Vite dev server):

```bash
npm run build                          # writes hashed bundles plus the manifest
uv run python manage.py runserver
```

Building flips rule 2 into rule 3, so the same `runserver` command now serves hashed bundles through Django staticfiles. `ViteManifestBackend` raises rather than degrades on every failure in that mode: no manifest on disk, or a manifest with no entry for a discovered `.vue` file, each raise a `RuntimeError` naming the command that fixes it. A raw `.vue` file is unrenderable without compilation, so serving one unbuilt would produce a silently broken page instead of a loud one. The backend also runs with `DEDUP_STRATEGY: "next.static.collector.HashContentDedup"`, which keys assets by the sha256 of their content rather than by URL, so two hashed filenames holding the same bytes collapse to a single tag after a build.

To go back to the dev server after a build, set the origin explicitly rather than deleting the output:

```bash
VITE_DEV_ORIGIN=http://localhost:5173 uv run python manage.py runserver
```

The `runserver` command is a single-process toy in both modes. The SSE endpoint blocks one Django dev thread per open subscriber, which is fine for a demo or `pytest` and unsafe for any real workload. The source is sync, so `PatchEventStream` sends no heartbeat, a documented limitation under WSGI. A production deployment runs an ASGI server with an async `changes` generator and an `asyncio` wake primitive, and passes an async source for heartbeat support.

To peek at the stream from a second terminal:

```bash
curl -N http://127.0.0.1:8000/polls/1/stream/
```

The `-N` flag disables curl output buffering so frames appear as the server flushes them. Vote in the browser, watch the `next-patches` events arrive.

Tests run on two stacks. `uv run pytest` exercises the page modules, the broker, the receiver-driven fan-out, and one envelope off the actual `PatchEventStream`. `npm test` runs Vitest over both co-located suites: `component.test.js` mounts the chart SFC under `@vue/test-utils`, and `page.test.js` imports the mount entry against a stubbed `window.Next` to check that it registers `onMount`, re-reads the DOM snapshot on a second pass, and ignores a `context-updated` event that does not name `live_results`.

## Walking the code

### 1. Co-location structure

```
studio/
└── layout.djx                        <- project-level HTML envelope, page_head, collect_scripts
polls/screens/
├── page.py                           <- redirects / to /polls/
└── polls/
    ├── layout.djx                    <- section wrapper
    ├── page.py                       <- index callables, @context active_polls_count inherit
    ├── template.djx                  <- index template using poll_card
    ├── _widgets/poll_card/           <- index list composite, no Python and no Vue
    │   ├── component.djx
    │   └── component.css
    └── [int:id]/
        ├── layout.djx                <- nested layout, poll question header, back link
        ├── page.py                   <- @context poll inherit, live_results serialize
        ├── page.vue                  <- mount entry, registers Next.partial.onMount
        ├── page.test.js              <- Vitest cover for the mount entry
        ├── template.djx              <- poll-results zone + data-next-sse element
        ├── stream/page.py            <- PatchEventStream over broker.changes(poll.pk)
        └── _widgets/poll_chart/      <- detail composite, owns the Vue layer
            ├── component.py          <- @component.context("results", serialize=True)
            ├── component.djx         <- SSR bars, data-poll-chart-data block, vote form
            ├── component.vue         <- chart SFC driven by a snapshot prop
            ├── component.test.js     <- Vitest cover for the SFC
            └── component.css
```

Two page roots, as in every example. [`studio/`](studio/) is the project-level root in `PAGE_BACKENDS["DIRS"]` and owns the single outer `layout.djx`. The routable pages come from the app through `APP_DIRS = True` and `PAGES_DIR = "screens"`. The framework discovers every co-located `.djx`, `.css`, and `.vue` file next to the page or component that owns it, with no manifest of asset paths to maintain.

### 2. Two calls in `apps.py`

```python
class PollsConfig(AppConfig):
    def ready(self) -> None:
        default_kinds.register(
            "vue", extension=".vue", slot="scripts", renderer="render_module_tag"
        )
        default_stems.register("template", "page")

        from polls import providers, signals  # noqa: F401, PLC0415
```

The `vue` kind binds the `.vue` extension to the `scripts` slot and reuses the framework built-in `render_module_tag`. Reusing a built-in renderer is what earns the kind a client insertion verb: every asset in a patch envelope carries a `load` field, and the `.vue` files ride out as `load: "module"` while the co-located `component.css` rides as `load: "link"`. A kind registered with a renderer of your own carries no verb and its assets reach the browser only on a full render, which the framework reports as `next.W074`. The `signals` import wires both the Vite dev-asset injector and the `action_dispatched` listener that drives the broker fan-out.

### 3. The stream through the page escape hatch

```python
def render(request: HttpRequest, poll: DPoll[Poll]) -> PatchEventStream:
    return PatchEventStream(request, patch_source(request, poll.pk))
```

The page module returns a `PatchEventStream` directly. The framework escape hatch in `next/pages/manager.py` returns any `HttpResponseBase` subclass verbatim, so the layout chain and the static collector are bypassed for the streaming endpoint. `PatchEventStream` sets `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no` on construction so a proxy or `GZipMiddleware` does not eat the flush, and emits a leading `retry` hint from the `SSE.RETRY_MS` option.

The endpoint stays sync because the broker waits on `threading.Condition`. An ASGI deployment swaps the wake primitive for an `asyncio.Condition` and passes an async source for heartbeat support without touching the page or the signal layer.

### 4. Broker on `threading.Condition` and LocMemCache

```python
class PollBroker:
    def publish(self, snapshot, request_id=None):
        store_snapshot(snapshot)
        condition = self._conditions[snapshot.poll_id]
        with condition:
            self._revisions[snapshot.poll_id] += 1
            self._request_ids[snapshot.poll_id] = request_id
            condition.notify_all()

    def changes(self, poll_id):
        condition = self._conditions[poll_id]
        last_revision = self._revisions[poll_id]
        while True:
            current_revision = self._wait_for_new_revision(
                poll_id, condition, last_revision
            )
            if current_revision == last_revision:
                continue
            last_revision = current_revision
            payload = read_snapshot(poll_id)
            if payload is None:
                continue
            yield Change(snapshot=payload, request_id=self._request_ids.get(poll_id))
```

The cache holds the latest snapshot per poll. Each `publish` bumps a monotonic revision, records the mutation's request id, and `notify_all` wakes every subscriber. The broker yields `Change` value objects, the snapshot plus the request id, not wire bytes. The framework `PatchEventStream` owns the SSE framing, so the broker stays a plain pub/sub of domain events.

Each subscriber captures its own `last_revision` _before_ yielding so a publish that lands while the consumer is still holding a frame wakes it on the next `next()` instead of being absorbed silently. A wake timeout loops without yielding. The sync source under WSGI sends no keepalive, the documented limitation the framework stream notes. The pattern is single-process by design. A multi-process deployment swaps the broker for Redis Pub/Sub or Postgres `LISTEN`/`NOTIFY` without touching the page or the signal layer.

A naive `threading.Event` plus `clear()` looks attractive here but loses events under fan-out: the first subscriber to clear the flag hides the wake from the others. The condition + revision pair is the canonical fix and costs the same number of lines.

### 5. Signal-driven fan-out carrying the request id

```python
@receiver(action_dispatched)
def broadcast_vote(action_name="", form=None, request=None, **kwargs):
    if action_name != VOTE_ACTION_NAME or form is None:
        return
    poll = form.cleaned_data.get("poll")
    if poll is None:
        return
    request_id = request.headers.get(REQUEST_ID) if request is not None else None
    broker.publish(build_snapshot(poll), request_id=request_id)
```

The `action_dispatched` signal carries the bound form post-validation plus the request, so the receiver knows which poll changed and reads the mutation's `X-Next-Request-Id`. Threading that id to `broker.publish` lets the stream stamp it as the envelope echo, so the voter's own tab drops the fan-out. The receiver is the single publish point for the broker. Concentrating the publish here keeps the write path observable through one signal hook.

### 6. Vue layer driven by `Next.partial.onMount`

```python
@component.context("results", serialize=True)
def results(poll: Poll) -> dict[str, object]:
    return {"poll_id": poll.pk, "total_votes": ..., "choices": [...]}
```

`serialize=True` injects this dict into `window.Next.context.results` at page load through the `_init` payload. Voting stays server-side through the `{% form %}` tag in the component template, so the payload carries no vote URL or CSRF token. `page.vue` registers a `Next.partial.onMount("[data-poll-chart]", ...)` handler that the runtime runs over the initial DOM and over the morphed zone after every `refresh`. Each pass reads the fresh per-choice counts from the `data-poll-chart-data` block the server embeds in the zone and pushes the snapshot into the Vue instance through its `applySnapshot` method, so the chart tracks the same `refresh` that re-renders the bars, across tabs. The visible bars live in a `data-next-keep` container the Vue app owns, so the zone morph never fights Vue for those nodes. `page.vue` also subscribes to `context-updated`, which the voter's own response fires through its `context` patch. The event payload carries `{ context, changed }`, so the listener acts only when `changed` includes `live_results` and pushes that snapshot straight into the live instance without re-reading the DOM. A delegated `next:removed` listener completes the island's life cycle, walking the detached subtree and calling `app.unmount()` for every chart it finds, so navigating away from the page never leaks a Vue root. The SSR bars in `component.djx` are the no-JavaScript fallback, so the page degrades to plain server-rendered bars when scripting is off.

Two serialize providers feed one JS object here, the component-level `results` and the page-level `live_results`. `JS_CONTEXT_POLICY: "next.static.collector.DeepMergePolicy"` in [`config/settings.py`](config/settings.py) is what lets them coexist: the policy merges nested dicts key by key instead of letting the later writer replace the whole object, so a `context` patch that carries only `live_results` leaves `results` intact.

### 7. Inherit context across the layout chain

`polls/page.py` declares `active_polls_count` with `inherit_context=True` so descendant pages, including the detail page and its child stream endpoint, share the same value without a second query. The root layout reads it for the header badge. The `[int:id]/page.py` adds `poll` with `inherit_context=True`. The nested `[int:id]/layout.djx` and the inner template both consume `poll` directly, demonstrating context flow without a context processor.

### 8. DI through `DPoll[Poll]`

```python
class PollProvider(RegisteredParameterProvider):
    def can_handle(self, param, _context):
        return get_origin(param.annotation) is DPoll

    def resolve(self, param, context):
        (model_cls,) = get_args(param.annotation)
        pk = context.url_kwargs.get("id")
        if pk is None:
            request = getattr(context, "request", None)
            if request is not None:
                pk = request.POST.get("poll")
        if pk is None:
            return None
        try:
            return model_cls.objects.get(pk=pk)
        except model_cls.DoesNotExist as exc:
            raise Http404 from exc
```

`DPoll[Poll]` resolves the URL kwarg `id` for page rendering and falls back to the POST `poll` field, the hidden input `VoteForm` already carries, when the dispatcher hands over a form-action request that has no URL kwargs of its own. The stream endpoint and the page-level `poll` callable both consume the same provider, so the model fetch lives in one place. `resolve` returns `None` rather than raising when neither source names a poll, which lets a caller with a default keep working, and it maps a missing row to `Http404` so a stale poll id answers `404` instead of `500`.

Page and component modules that use `DPoll[Poll]` do not import `from __future__ import annotations`. The DI resolver compares annotations by identity and lazy strings would silently break the match.

### 9. Two composites at two scopes

`poll_card` is the index list composite. It has no `component.py` and no Vue, only markup and a stylesheet, and it sits in `polls/_widgets/` at the section root because the index template is what renders it. `poll_chart` is the detail composite that owns the Vue layer, and it sits in `polls/[int:id]/_widgets/` because only the poll page renders it.

Component visibility follows the directory tree. A component is in scope for every template at or below its scope root, so the detail template can call `poll_card` but the index template cannot call `poll_chart`. Placing a composite at the narrowest scope that uses it keeps the name from leaking into pages that have no data to feed it.

## Gotchas

### The asset-version guard needs an explicit version

The Vite build is served through plain staticfiles rather than a hashed manifest storage, so the asset-version guard has nothing to derive a stamp from and the example pins `VERSION` by hand. The [examples README](../README.md#conventions-every-example-follows) covers that convention and the `next.W069` check behind it.

## Further reading

- [`polls/apps.py`](polls/apps.py) — `PollsConfig.ready()` with the two registry calls.
- [`polls/signals.py`](polls/signals.py) — `broadcast_vote` receiver plus the dev-mode Vite injector.
- [`polls/broker.py`](polls/broker.py) — `PollBroker`, `Snapshot`, and the `Change` value object.
- [`polls/backends.py`](polls/backends.py) — `ViteManifestBackend` dev/prod URL routing.
- [`polls/screens/polls/[int:id]/stream/page.py`](polls/screens/polls/[int:id]/stream/page.py) — the `PatchEventStream` page module.
- [`polls/screens/polls/[int:id]/_widgets/poll_chart/component.vue`](polls/screens/polls/[int:id]/_widgets/poll_chart/component.vue) — the chart Vue SFC.
- [`next/partial/sse.py`](../../next/partial/sse.py) — `PatchEventStream`, the politeness headers, and the heartbeat contract.
- [`next/partial/patches.py`](../../next/partial/patches.py) — the `Patches` builder and the `refresh` verb.
- [`next/forms/signals.py`](../../next/forms/signals.py) — `action_dispatched` payload contract used by the receiver.
- [`next/static/signals.py`](../../next/static/signals.py) — `collector_finalized` signal that drives the Vite dev preamble.
- [`next/components/context.py`](../../next/components/context.py) — `@component.context` and the `serialize=True` flag.
- [`docs/content/ref/system-checks.rst`](../../docs/content/ref/system-checks.rst) — `next.W074` for a kind with no insertion verb and `next.W069` for the asset-version guard.
