# Live polls

A polling app where the chart on every open tab updates the moment
someone votes. Server-Sent Events stream snapshots out of an
in-process broker. A locally bundled Vue 3 component subscribes
through a single `EventSource` and reacts to each frame. The example
demonstrates how a streaming endpoint, a signal-driven fan-out, and a
Vite-bundled Vue layer compose into one cohesive feature without any
ad-hoc plumbing in the framework core.

## What you will see

| URL | Description |
|-----|-------------|
| `/` | Redirects to `/polls/` so the bare site root is never empty. |
| `/polls/` | Server-rendered list of polls. Each card shows choice count and total votes. |
| `/polls/<id>/` | Vote page with the live chart and a button-per-choice form. Vue takes over the chart on mount. |
| `/polls/<id>/stream/` | Server-Sent Events endpoint. First frame is the cached snapshot, then `update` frames flow on every vote. |
| `POST polls:vote` | Atomically increments a choice via `F("votes") + 1`, publishes a fresh snapshot to the broker, redirects to the poll page. |

Two demo polls seed via a data migration (`tabs-or-spaces` and
`vim-or-emacs`) so the index page is never empty on a fresh database.

## How to run

Local development (HMR for Vue, autoreload for Django):

```bash
cd examples/live-polls
uv run python manage.py migrate
npm install
npm run dev                            # terminal A: http://localhost:5173
uv run python manage.py runserver     # terminal B: http://127.0.0.1:8000/polls/
```

`migrate` seeds two demo polls. With `DEBUG=True` Django defaults to
the Vite dev server at `http://localhost:5173`, so editing
`component.vue` hot-reloads in the browser without restarts. The
`@vite/client` script loads through the `collector_finalized` signal
on every page that carries Vue assets. The Django reloader picks up
Python edits on its own.

Production-shaped run (no Vite dev server):

```bash
DJANGO_DEBUG=0 npm run build           # writes hashed bundles + manifest
DJANGO_DEBUG=0 uv run python manage.py runserver
```

With `DEBUG=False` (or `VITE_DEV_ORIGIN=` empty) the
`ViteManifestBackend` reads `dist/.vite/manifest.json` and delegates
URL resolution to Django staticfiles. If the manifest is missing the
backend raises a clear error pointing at `npm run build`. The kanban
example falls back to staticfiles instead because raw `.jsx` is at
least loadable as plain JavaScript. A raw `.vue` file is unrenderable
without compilation, so live-polls refuses to serve a broken bundle
and tells the operator how to fix the deployment instead.

The `runserver` command is a single-process toy in both modes. The
SSE endpoint blocks one Django dev thread per open subscriber, which
is fine for a demo or `pytest` and unsafe for any real workload. A
production deployment runs an ASGI server with an async `subscribe`
generator and an `asyncio` wake primitive.

Override the Vite origin on either side with
`VITE_DEV_ORIGIN=http://host:port` when the dev server runs on a
non-default address.

To peek at the SSE stream from a second terminal:

```bash
curl -N http://127.0.0.1:8000/polls/1/stream/
```

The `-N` flag disables curl output buffering so frames appear as the
server flushes them. Vote in the browser, watch the curl output.

Tests run on two stacks. `uv run pytest` exercises the page modules,
the broker, the receiver-driven fan-out, and one frame off the actual
`StreamingHttpResponse`. `npm test` runs the Vitest suite that mounts
`poll_chart/component.vue` under `@vue/test-utils` and verifies the
reactive update path against a stub `EventSource`.

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
    │   ├── page.vue                  <- mount entry, calls createApp(PollChart).mount(...)
    │   └── template.djx              <- renders {% component "poll_chart" %}
    │   └── stream/page.py            <- StreamingHttpResponse over broker.subscribe(poll.pk)
    └── _widgets/
        ├── poll_card/                <- index list composite (no Vue)
        │   ├── component.py
        │   ├── component.djx
        │   └── component.css
        └── poll_chart/               <- detail composite, owns the Vue layer
            ├── component.py          <- @component.context("results", serialize=True)
            ├── component.djx         <- SSR chart bars + vote form
            ├── component.vue         <- live chart SFC, reads window.Next.context.results
            └── component.css
```

The directory walks the full feature surface of next.dj end to end.
The framework discovers every co-located `.djx`, `.css`, and `.vue`
asset automatically. The `_widgets` directory is a sibling of the
detail page so both the index and detail templates can reach into it
through the same `{% component "name" %}` call.

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

The `vue` kind binds the `.vue` extension to the `scripts` slot and
reuses the framework built-in `render_module_tag`. No custom
renderer is needed because Vite emits standard ES modules. The
`signals` import wires both the Vite dev-asset injector and the
`action_dispatched` listener that drives the broker fan-out.

### 3. SSE through the page escape hatch

```python
def render(poll: DPoll[Poll]) -> StreamingHttpResponse:
    return StreamingHttpResponse(
        broker.subscribe(poll.pk),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

The page module returns a `StreamingHttpResponse` directly. The
framework escape hatch in `next/pages/manager.py` returns any
`HttpResponseBase` subclass verbatim, which means the layout chain
and the static collector are bypassed for streaming endpoints. No
template, no asset injection, no buffering.

The endpoint stays sync because the broker waits on
`threading.Condition` and the example targets `runserver` and
`pytest`. An ASGI deployment swaps the wake primitive for an
`asyncio.Condition` (or one `asyncio.Queue` per subscriber) and
yields from an async generator without touching the page or the
signal layer.

### 4. Broker on `threading.Condition` and LocMemCache

```python
class PollBroker:
    def __init__(self) -> None:
        self._conditions: dict[int, threading.Condition] = defaultdict(threading.Condition)
        self._revisions: dict[int, int] = defaultdict(int)

    def publish(self, snapshot: Snapshot) -> None:
        store_snapshot(snapshot)
        condition = self._conditions[snapshot.poll_id]
        with condition:
            self._revisions[snapshot.poll_id] += 1
            condition.notify_all()

    def subscribe(self, poll_id: int) -> Iterator[bytes]:
        condition = self._conditions[poll_id]
        last_revision = self._revisions[poll_id]
        cached = read_snapshot(poll_id)
        if cached is not None:
            yield format_event(cached, event="snapshot")
        while True:
            with condition:
                condition.wait_for(
                    lambda b=last_revision: self._revisions[poll_id] != b,
                    timeout=KEEPALIVE_SECONDS,
                )
                current_revision = self._revisions[poll_id]
            if current_revision == last_revision:
                yield format_keepalive()
                continue
            last_revision = current_revision
            payload = read_snapshot(poll_id)
            if payload is None:
                continue
            yield format_event(payload, event="update")
```

The cache holds the latest snapshot per poll. Each `publish` bumps a
monotonic revision and `notify_all` wakes every subscriber. Each
subscriber captures its own `last_revision` *before* yielding the
initial snapshot so a publish that lands while the consumer is still
holding the snapshot frame still wakes the subscriber on the next
`next()` instead of being absorbed silently. A 15-second timeout on
`wait_for` produces an SSE comment frame (`: keepalive\n\n`) so
proxies and the browser keep an idle connection open. The pattern is
single-process by design. A multi-process deployment swaps the
broker for Redis Pub/Sub or Postgres `LISTEN`/`NOTIFY` without
touching the page or the signal layer.

A naive `threading.Event` plus `clear()` looks attractive here but
loses events under fan-out: the first subscriber to clear the flag
hides the wake from the others. The condition + revision pair is
the canonical fix and costs the same number of lines.

### 5. Signal-driven fan-out using the bound form

```python
@receiver(action_dispatched)
def broadcast_vote(action_name="", form=None, **_):
    if action_name != VOTE_ACTION_NAME or form is None:
        return
    poll = form.cleaned_data.get("poll")
    if poll is None:
        return
    broker.publish(build_snapshot(poll))
```

The `action_dispatched` signal carries the bound form post-validation
plus the resolved URL kwargs. Without those fields the receiver could
not tell which poll changed without reissuing the query. The receiver
is the *single* publish point for the broker. The vote handler runs
the atomic `UPDATE` and returns the redirect, then the dispatcher
fires `action_dispatched` and the receiver builds the fresh snapshot
and calls `broker.publish`. Every open SSE subscriber wakes within
milliseconds. Concentrating the publish in the receiver keeps the
write path observable through one signal hook and avoids the double
cache write a handler-side `store_snapshot` would cause.

### 6. Vue layer reads `window.Next.context.results`

```python
@component.context("results", serialize=True)
def results(poll: Poll, request: HttpRequest) -> dict[str, object]:
    return {
        "poll_id": poll.pk,
        "stream_url": f"/polls/{poll.pk}/stream/",
        "vote_url": form_action_manager.get_action_url("polls:vote"),
        "csrf": get_token(request),
        "choices": [...],
        "total_votes": ...,
    }
```

`serialize=True` injects this dict into `window.Next.context.results`.
The Vue `<script setup>` block reads it on mount, opens a single
`EventSource(stream_url)`, and binds `snapshot` and `update`
listeners that swap a reactive `choices` ref. The chart renders bar
widths from a `computed` percentage so the SFC has zero direct DOM
work. `page.vue` mounts the SFC into the `[data-poll-chart-app]`
hook the SSR template provides, so the page degrades to plain
server-rendered bars when JavaScript is disabled.

### 7. Inherit context across the layout chain

`polls/page.py` declares `active_polls_count` with
`inherit_context=True` so descendant pages, including the detail
page and its child stream endpoint, share the same value without a
second query. The root layout reads it for the header badge. The
`[int:id]/page.py` adds `poll` with `inherit_context=True`. The
nested `[int:id]/layout.djx` and the inner template both consume
`poll` directly, demonstrating context flow without a context
processor.

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

`DPoll[Poll]` resolves the URL kwarg `id` for page rendering and
falls back to a POST `poll_id` field when the dispatcher hands a
form-action request without resolved URL kwargs. The vote handler,
the stream endpoint, and the page-level `poll` callable all consume
the same provider, so the model fetch lives in one place.

Page and component modules that use `DPoll[Poll]` do not import
`from __future__ import annotations`. The DI resolver compares
annotations by identity and lazy strings would silently break the
match.

### 9. Two composites at the same widgets directory

`poll_card` is the index list composite with no Vue. `poll_chart`
is the detail composite that mounts the Vue layer. Both live under
one `_widgets` directory at the section root, which lets either
template reach into either composite through `{% component "name" %}`
without import gymnastics. The framework discovers both directories
during component registration.

## Further reading

- [`polls/apps.py`](polls/apps.py) — `PollsConfig.ready()` with the two registry calls.
- [`polls/signals.py`](polls/signals.py) — `broadcast_vote` receiver plus the dev-mode Vite injector.
- [`polls/broker.py`](polls/broker.py) — `PollBroker`, `Snapshot`, and SSE byte formatting.
- [`polls/backends.py`](polls/backends.py) — `ViteManifestBackend` dev/prod URL routing.
- [`polls/screens/polls/[int:id]/stream/page.py`](polls/screens/polls/[int:id]/stream/page.py) — SSE page module.
- [`polls/screens/polls/_widgets/poll_chart/component.vue`](polls/screens/polls/_widgets/poll_chart/component.vue) — live-update Vue SFC.
- [`vite.config.ts`](vite.config.ts) — glob multi-entry build that discovers every `.vue` file.
- [`next/pages/manager.py`](../../next/pages/manager.py) — `_call_render_function` escape hatch that returns `HttpResponseBase` verbatim.
- [`next/forms/signals.py`](../../next/forms/signals.py) — `action_dispatched` payload contract used by the receiver.
- [`next/forms/dispatch.py`](../../next/forms/dispatch.py) — dispatcher that attaches `form` and `url_kwargs` to the signal.
- [`next/static/signals.py`](../../next/static/signals.py) — `collector_finalized` signal that drives the Vite dev preamble.
- [`next/components/context.py`](../../next/components/context.py) — `@component.context` and the `serialize=True` flag.
