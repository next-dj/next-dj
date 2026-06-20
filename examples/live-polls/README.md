# Live polls

A polling app where the results on every open tab refresh the moment someone votes. The framework SSE bridge streams patch envelopes out of an in-process broker. Each event is a `refresh` patch, so every tab re-fetches the results zone with its own cookies through the page view, and no foreign HTML ever travels on the stream. A locally bundled Vue 3 island sits on top of the server-rendered chart. The example demonstrates how a streaming endpoint, a signal-driven fan-out, and a Vite-bundled Vue layer compose into one feature without any ad-hoc plumbing in the framework core.

## What you will see

| URL | Description |
| --- | --- |
| `/` | Redirects to `/polls/` so the bare site root is never empty. |
| `/polls/` | Server-rendered list of polls. Each card shows choice count and total votes. |
| `/polls/<id>/` | Vote page with the live chart, a button-per-choice form, and a `data-next-sse` element. |
| `/polls/<id>/stream/` | The patch event stream. Each poll change emits a `next-patches` event carrying a `refresh` of the `poll-results` zone. |
| `POST vote_form` | Atomically increments a choice via `F("votes") + 1`, publishes a fresh snapshot to the broker with the request id, and answers the partial. |

Two demo polls seed via a data migration (`tabs-or-spaces` and `vim-or-emacs`) so the index page is never empty on a fresh database.

## How the live update works

The vote page wraps its results in a zone and connects the stream with one element.

```jinja
{% zone "poll-results" %}
  {% component "poll_chart" %}
{% endzone %}
<div data-next-sse="/polls/{{ poll.pk }}/stream/"></div>
```

The runtime opens one `EventSource` on the `data-next-sse` URL and routes each `next-patches` event into the same apply pipeline an HTTP partial response uses. There is no hand-written `EventSource` code.

A vote posts with `X-Next-Request-Id`. The dispatcher answers the voter with a morph of the `poll-results` zone, then a signal receiver publishes the change to the broker carrying that request id. The stream page yields one `refresh` envelope per change, stamped with the request id as the echo.

```python
def patch_source(request: HttpRequest, poll_id: int) -> Iterator[Patches]:
    for change in broker.changes(poll_id):
        yield Patches(request, echo_of=change.request_id).refresh(zone="poll-results")
```

Every subscriber receives the same envelope. The voter's own tab finds the request id in its echo ring buffer and drops the event, its POST already brought the fresh zone. Every other tab executes the `refresh` and re-fetches `poll-results` with its own cookies through the poll page view, so authorization is re-checked per subscriber and the server-rendered bars come back current.

The fan-out is built on `refresh` rather than a context patch on purpose. A context patch carries a serialize provider's value read from the origin page of the request that builds it, and a stream source has no page-render origin to read from. A stream that needs fresh data drives a `refresh`, and the re-fetched zone delivers the new state through its own render. The chart updates by re-rendering the server-side bars in the `poll-results` zone.

## How to run

Local development (HMR for Vue, autoreload for Django):

```bash
cd examples/live-polls
uv run python manage.py migrate
npm install
npm run dev                            # terminal A: http://localhost:5173
uv run python manage.py runserver     # terminal B: http://127.0.0.1:8000/polls/
```

`migrate` seeds two demo polls. With `DEBUG=True` Django defaults to the Vite dev server at `http://localhost:5173`, so editing `component.vue` hot-reloads in the browser without restarts. The `@vite/client` script loads through the `collector_finalized` signal on every page that carries Vue assets. The Django reloader picks up Python edits on its own.

Production-shaped run (no Vite dev server):

```bash
DJANGO_DEBUG=0 npm run build           # writes hashed bundles + manifest
DJANGO_DEBUG=0 uv run python manage.py runserver
```

With `DEBUG=False` (or `VITE_DEV_ORIGIN=` empty) the `ViteManifestBackend` reads `dist/.vite/manifest.json` and delegates URL resolution to Django staticfiles. If the manifest is missing the backend raises a clear error pointing at `npm run build`. A raw `.vue` file is unrenderable without compilation, so live-polls refuses to serve a broken bundle and tells the operator how to fix the deployment instead.

The `runserver` command is a single-process toy in both modes. The SSE endpoint blocks one Django dev thread per open subscriber, which is fine for a demo or `pytest` and unsafe for any real workload. The source is sync, so `PatchEventStream` sends no heartbeat, a documented limitation under WSGI. A production deployment runs an ASGI server with an async `changes` generator and an `asyncio` wake primitive, and passes an async source for heartbeat support.

Override the Vite origin on either side with `VITE_DEV_ORIGIN=http://host:port` when the dev server runs on a non-default address.

To peek at the stream from a second terminal:

```bash
curl -N http://127.0.0.1:8000/polls/1/stream/
```

The `-N` flag disables curl output buffering so frames appear as the server flushes them. Vote in the browser, watch the `next-patches` events arrive.

Tests run on two stacks. `uv run pytest` exercises the page modules, the broker, the receiver-driven fan-out, and one envelope off the actual `PatchEventStream`. `npm test` runs the Vitest suite that mounts `poll_chart/component.vue` under `@vue/test-utils`.

## Walking the code

### 1. Co-location structure

```
polls/screens/
├── layout.djx                        <- root html shell, Tailwind, collect_styles, collect_scripts
└── polls/
    ├── layout.djx                    <- section wrapper
    ├── page.py                       <- index callables, @context active_polls_count inherit
    ├── template.djx                  <- index template using poll_card
    ├── [int:id]/
    │   ├── layout.djx                <- nested layout, poll question header, back link
    │   ├── page.py                   <- @context poll inherit, vote action
    │   ├── page.vue                  <- mount entry, registers Next.partial.onMount
    │   ├── template.djx              <- poll-results zone + data-next-sse element
    │   └── stream/page.py            <- PatchEventStream over broker.changes(poll.pk)
    └── _widgets/
        ├── poll_card/                <- index list composite (no Vue)
        │   ├── component.py
        │   ├── component.djx
        │   └── component.css
        └── poll_chart/               <- detail composite, owns the Vue layer
            ├── component.py          <- @component.context("results", serialize=True)
            ├── component.djx         <- SSR bars, data-poll-chart-data block, vote form
            ├── component.vue         <- live chart SFC, driven by a snapshot prop
            └── component.css
```

The directory walks the full feature surface of next.dj end to end. The framework discovers every co-located `.djx`, `.css`, and `.vue` asset automatically. The `_widgets` directory is a sibling of the detail page so both the index and detail templates can reach into it through the same `{% component "name" %}` call.

### 2. Two calls in `apps.py`

```python
class PollsConfig(AppConfig):
    def ready(self) -> None:
        default_kinds.register(
            "vue",
            extension=".vue",
            slot="scripts",
            renderer="render_module_tag",
        )
        default_stems.register("template", "page")

        from polls import providers, signals  # noqa: F401, PLC0415
```

The `vue` kind binds the `.vue` extension to the `scripts` slot and reuses the framework built-in `render_module_tag`. The `signals` import wires both the Vite dev-asset injector and the `action_dispatched` listener that drives the broker fan-out.

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
def broadcast_vote(action_name="", form=None, request=None, **_):
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
    return {
        "poll_id": poll.pk,
        "total_votes": ...,
        "choices": [...],
    }
```

`serialize=True` injects this dict into `window.Next.context.results` at page load through the `_init` payload. Voting stays server-side through the `{% form %}` tag in the component template, so the payload carries no vote URL or CSRF token. `page.vue` registers a `Next.partial.onMount("[data-poll-chart]", ...)` handler that the runtime runs over the initial DOM and over the morphed zone after every `refresh`. Each pass reads the fresh per-choice counts from the `data-poll-chart-data` block the server embeds in the zone and pushes the snapshot into the Vue instance through its `applySnapshot` method, so the chart tracks the same `refresh` that re-renders the bars, across tabs. The visible bars live in a `data-next-keep` container the Vue app owns, so the zone morph never fights Vue for those nodes. The stream sends a `refresh`, not a context patch, so the island never depends on `context-updated` after the first paint. The SSR bars in `component.djx` are the no-JavaScript fallback, so the page degrades to plain server-rendered bars when scripting is off.

### 7. Inherit context across the layout chain

`polls/page.py` declares `active_polls_count` with `inherit_context=True` so descendant pages, including the detail page and its child stream endpoint, share the same value without a second query. The root layout reads it for the header badge. The `[int:id]/page.py` adds `poll` with `inherit_context=True`. The nested `[int:id]/layout.djx` and the inner template both consume `poll` directly, demonstrating context flow without a context processor.

### 8. DI through `DPoll[Poll]`

```python
class PollProvider(RegisteredParameterProvider):
    def can_handle(self, param, _context):
        return get_origin(param.annotation) is DPoll

    def resolve(self, param, context):
        (model_cls,) = get_args(param.annotation)
        pk = context.url_kwargs.get("id") or context.request.POST.get("poll_id")
        if pk is None:
            return None
        try:
            return model_cls.objects.get(pk=pk)
        except model_cls.DoesNotExist as exc:
            raise Http404 from exc
```

`DPoll[Poll]` resolves the URL kwarg `id` for page rendering and falls back to a POST `poll_id` field when the dispatcher hands a form-action request without resolved URL kwargs. The vote handler, the stream endpoint, and the page-level `poll` callable all consume the same provider, so the model fetch lives in one place.

Page and component modules that use `DPoll[Poll]` do not import `from __future__ import annotations`. The DI resolver compares annotations by identity and lazy strings would silently break the match.

### 9. Two composites at the same widgets directory

`poll_card` is the index list composite with no Vue. `poll_chart` is the detail composite that mounts the Vue layer. Both live under one `_widgets` directory at the section root, which lets either template reach into either composite through `{% component "name" %}` without import gymnastics. The framework discovers both directories during component registration.

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
