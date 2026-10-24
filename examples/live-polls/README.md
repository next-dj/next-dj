# Live polls

A polling app where the results on every open tab refresh the moment someone votes. `TopicStream` fans out `refresh` patches, so each tab re-fetches the results zone with its own cookies through the page view, and no foreign HTML travels on the stream. It also publishes a `poll:votes` event that updates an accessible live announcement. A locally bundled Vue 3 island sits on top of the server-rendered chart. The example demonstrates how framework topic APIs, a signal-driven publish point, and a Vite-bundled Vue layer compose into one feature.

## What you will see

| URL | Description |
| --- | --- |
| `/` | Redirects to `/polls/` so the bare site root is never empty. |
| `/polls/` | Server-rendered list of polls. Each card shows choice count and total votes. |
| `/polls/<id>/` | Vote page with the live chart, a button-per-choice form, and a `data-next-sse` element. |
| `/polls/<id>/embed/` | The same live chart in a widget with its own bare document shell, linked as "Embed" from the vote page, where the link first opens the snippet to copy as a modal. |
| `/admin/polls/poll/` | Staff host console for opening or closing a poll and setting its deadline. |
| `/polls/<id>/stream/` | The topic stream. Each poll change emits a `next-patches` refresh of `poll-results` and a `poll:votes` event with the updated total. |
| `POST vote_form` | Atomically increments a choice via `F("votes") + 1`, then morphs the `poll-results` zone and pushes the fresh counts to `window.Next.context.live_results`. A signal receiver publishes the zone refresh and vote-count event after commit. |

Two demo polls live in [`polls/demo.py`](polls/demo.py) and load through `manage.py seed_demo` ("Tabs or spaces?" and "Vim or Emacs?", two choices each) so the index page is never empty once seeded.

## How the live update works

The vote page wraps its results in a zone and connects the stream with one element.

```jinja
{% zone "poll-results" %}
  {% component "poll_chart" %}
{% endzone %}
<div data-next-sse="/polls/{{ poll.pk }}/stream/"></div>
```

The runtime negotiates the configured transport from the guarded `data-next-sse` URL. When WebSockets are enabled and available, it opens the socket with a short-lived ticket issued by that page, and otherwise it opens an `EventSource` for SSE. Both transports route `next-patches` into the same apply pipeline as an HTTP partial response. There is no hand-written transport client code.

A vote posts with `X-Next-Request-Id`. A valid partial vote answers the voter's own tab with two ops on one envelope, a morph of the `poll-results` zone with the fresh server-rendered bars and a `context` patch that pushes the new snapshot into `window.Next.context.live_results`. The `live_results` name is a page-level `serialize=True` provider on `[int:id]/page.py`, so `Patches(request).context(live_results=...)` resolves it against the origin page the vote posted from. The Vue island reads the pushed snapshot on `context-updated` and rebinds without re-reading the DOM. A signal receiver publishes a `refresh` after the action transaction commits. The framework copies the originating request id from `Patches(request)`, so the voter's tab drops its echo. A bad choice never reaches the handler, so the dispatcher auto-morphs the `poll-results` zone in place because the vote form lives inside it, and the voter keeps the rest of the page. Without the runtime the vote falls back to a redirect to the poll page.

Every subscriber receives the refresh and vote-count event. The voter's own tab finds the request id in its echo ring buffer and drops both messages, because its POST already brought the fresh zone. Every other tab executes the `refresh` and re-fetches `poll-results` with its own cookies through the poll page view, so authorization is re-checked per subscriber and the server-rendered bars come back current. The vote-count event updates a screen-reader announcement and carries no chart state.

The fan-out uses `refresh` because each subscriber must load its own authorized HTML and state. The vote response can still pair a zone morph with a `context` patch because it belongs to the submitting request.

## Mechanism map

| App action | Framework path | Result |
| --- | --- | --- |
| Staff host saves `is_open` or `closes_at` in Django admin | `ModelAdmin.save_model` publishes `poll:phase` and refreshes `poll-results` with `on_commit=True` | Open tabs update their status, and the result zone is rendered again for each subscriber. |
| A visitor submits a choice | `VoteForm` validates the poll state, then increments with `F("votes") + 1` | The voter receives a zone morph and context snapshot, and the signal publishes a refresh and vote-count event to other tabs. |
| A visitor watches updates | `TopicStream` on the guarded stream page | Negotiation prefers a configured Channels WebSocket and falls back to SSE. Both use the guarded stream URL and recheck the topic authorizer once per heartbeat. |
| Staff delete a poll | `can_subscribe_topic` denies the topic at the next recheck | Each open stream for that poll ends with `next-end` and the tab stops reconnecting. |
| A tab misses messages or reconnects | Per-topic `(epoch, seq)` cursor and guarded zone resync | A gap or a new broker epoch re-renders current state with that tab's access and cookies. The broker never replays events. |
| A poll has a deadline | Server-side form validation plus `Next.partial.stream.now()` in the chart island | The browser shows an estimated server-clock countdown, and the server refuses a vote after close. |

Create a staff account with `uv run python manage.py createsuperuser`, then edit the poll at `/admin/polls/poll/`. Check `is_open` and optionally set `closes_at`. The deadline remains authoritative on the server even if a browser is stale or JavaScript is disabled.

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

The `runserver` command is a single-process toy in both modes. Under WSGI, each open stream occupies a worker while waiting for a broker message. A multi-worker deployment needs a shared channel layer and cache for `ChannelsLayerBroker`, or a shared backend such as `RedisBroker`. `TopicStream` selects the broker's sync or async subscription from the request type and emits a heartbeat frame every `NEXT_FRAMEWORK["REALTIME"]["HEARTBEAT"]` seconds, 25 by default. Keep that interval below the idle timeout of any proxy in front of the stream.

The heartbeat also paces access. On both transports the stream rechecks the session and `polls.realtime.can_subscribe_topic`, an importable authorizer that allows a topic only while its poll exists, at most once per heartbeat. Delete a poll in the admin and every open stream for it ends with a `next-end` frame at that recheck, so the tab detaches the stream instead of reconnecting.

The example keeps `TRANSPORTS` at `["sse"]` because `runserver` speaks WSGI. When the optional Channels package is installed, [`config/settings.py`](config/settings.py) adds `channels` to `INSTALLED_APPS` and [`config/asgi.py`](config/asgi.py) wraps the app with `next.contrib.channels.application`. Its WebSocket Origin allowlist comes from `ALLOWED_HOSTS` and ignores ports, so `localhost:8000` and `127.0.0.1:8000` pass with no extra setting. To switch to WebSockets, install `next-dj[channels]`, set `NEXT_FRAMEWORK["REALTIME"]["TRANSPORTS"]` to `["ws", "sse"]`, and serve `config.asgi:application` with an ASGI server such as Daphne. The socket opens with a short-lived session-bound ticket negotiated through the guarded stream page, and SSE stays the fallback. The E2E suite covers both the WSGI SSE path and a Channels WebSocket connection under Daphne.

To peek at the stream from a second terminal:

```bash
curl -N http://127.0.0.1:8000/polls/1/stream/
```

The `-N` flag disables curl output buffering so frames appear as the server flushes them. Vote in the browser, watch the `next-patches` events arrive.

Tests run on two stacks. `uv run pytest` exercises the page modules, topic fan-out, the stream response, and the `next-end` frame a deleted poll sends to its open stream. `npm test` runs Vitest over both co-located suites: `component.test.js` mounts the chart SFC under `@vue/test-utils`, and `page.test.js` imports the mount entry against a stubbed `window.Next` to check that it registers `onMount`, re-reads the DOM snapshot on a second pass, and ignores a `context-updated` event that does not name `live_results`.

## Walking the code

### 1. Co-location structure

```
studio/
└── layout.djx                        <- project-level HTML envelope, page_head, collect_scripts
polls/pages/
├── page.py                           <- redirects / to /polls/
└── polls/
    ├── layout.djx                    <- section wrapper
    ├── page.py                       <- index callables, @context active_polls_count inherit
    ├── template.djx                  <- index template using poll_card
    ├── _components/poll_card/        <- index list composite, no Python and no Vue
    │   ├── component.djx
    │   └── component.css
    └── [int:id]/
        ├── layout.djx                <- nested layout, poll question header, back link
        ├── (.)embed/                 <- intercept, the copyable snippet as a modal over the vote page
        │   ├── page.py
        │   └── template.djx
        ├── embed/                    <- root layout with its own shell, widget page
        │   ├── layout.djx
        │   ├── layout.css
        │   ├── page.py
        │   ├── page.vue              <- re-imports the parent mount entry
        │   └── template.djx
        ├── page.py                   <- @context poll inherit, live_results serialize
        ├── page.vue                  <- mount entry, registers Next.partial.onMount
        ├── page.test.js              <- Vitest cover for the mount entry
        ├── template.djx              <- poll-results zone + data-next-sse element
        ├── stream/page.py            <- TopicStream for poll:<id>
        └── _components/poll_chart/   <- detail composite, owns the Vue layer
            ├── component.py          <- @component.context("results", serialize=True)
            ├── component.djx         <- SSR bars, data-poll-chart-data block, vote form
            ├── component.vue         <- chart SFC driven by a snapshot prop
            ├── component.test.js     <- Vitest cover for the SFC
            └── component.css
```

Two page roots, as in every example. [`studio/`](studio/) is the project-level root in `PAGE_BACKENDS["DIRS"]` and owns the single outer `layout.djx`. The routable pages come from the app through `APP_DIRS = True` and `PAGES_DIR = "pages"`. The framework discovers every co-located `.djx`, `.css`, and `.vue` file next to the page or component that owns it, with no manifest of asset paths to maintain.

### 2. Two calls in `apps.py`

```python
class PollsConfig(AppConfig):
    def ready(self) -> None:
        default_kinds.register(
            "vue", extension=".vue", slot="scripts", renderer="render_module_tag"
        )
        default_stems.register("template", "page")
```

The `vue` kind binds the `.vue` extension to the `scripts` slot and reuses the framework built-in `render_module_tag`. Reusing a built-in renderer is what earns the kind a client insertion verb: every asset in a patch envelope carries a `load` field, and the `.vue` files ride out as `load: "module"` while the co-located `component.css` rides as `load: "link"`. A kind registered with a renderer of your own carries no verb and its assets reach the browser only on a full render, which the framework reports as `next.W074`. Nothing else lives in `ready()`. The framework imports the `deps.py` and `signals.py` of every installed app at startup, so [`polls/deps.py`](polls/deps.py) registers the `DPoll` provider and [`polls/signals.py`](polls/signals.py) wires the Vite dev-asset injector and the `action_dispatched` listener that publishes through the framework realtime API.

### 3. The authorized topic stream

```python
from django.http import HttpRequest
from polls.deps import DPoll
from polls.models import Poll
from polls.realtime import can_subscribe_topic
from next.realtime import TopicStream


def render(request: HttpRequest, poll: DPoll[Poll]) -> TopicStream:
    return TopicStream(request, f"poll:{poll.pk}", authorize=can_subscribe_topic)
```

The page's normal guards and DI run before the subscription opens. The authorizer runs at subscription and again once per heartbeat on either transport, and a denial ends the stream with `next-end`. `TopicStream` handles transport negotiation, SSE framing, cache and proxy headers, retry hints, heartbeat frames, and stream cleanup. It selects `subscribe` under WSGI or `asubscribe` under ASGI.

### 4. Signal-driven publish

```python
from django.dispatch import receiver

from next.forms.signals import action_dispatched
from next.partial import Patches
from next.realtime import publish


@receiver(action_dispatched)
def broadcast_vote(action_name="", form=None, request=None, **kwargs):
    if action_name != VOTE_ACTION_NAME or form is None:
        return
    poll = form.cleaned_data.get("poll")
    if poll is None:
        return
    if request is not None:
        publish(f"poll:{poll.pk}", Patches(request).refresh(zone="poll-results"))
```

The `action_dispatched` signal carries the bound form and request. `publish` waits for the transaction to commit, and the builder supplies the echo id automatically. The app does not read request headers or implement broker queues.

### 6. Vue layer driven by `Next.partial.onMount`

```python
@component.context("results", serialize=True)
def results(poll: Poll) -> dict[str, object]:
    return {"poll_id": poll.pk, "total_votes": ..., "choices": [...]}
```

`serialize=True` injects this dict into `window.Next.context.results` at page load through the `_init` payload. Voting stays server-side through the `{% form %}` tag in the component template, so the payload carries no vote URL or CSRF token. `page.vue` registers a `Next.partial.onMount("[data-poll-chart]", ...)` handler that the runtime runs over the initial DOM and over the morphed zone after every `refresh`. Each pass reads the fresh per-choice counts from the `data-poll-chart-data` block the server embeds in the zone and pushes the snapshot into the Vue instance through its `applySnapshot` method, so the chart tracks the same `refresh` that re-renders the bars, across tabs. The visible bars live in a `data-next-keep` container the Vue app owns, so the zone morph never fights Vue for those nodes. `page.vue` also subscribes to `context-updated`, which the voter's own response fires through its `context` patch. The event payload carries `{ context, changed }`, so the listener acts only when `changed` includes `live_results` and pushes that snapshot straight into the live instance without re-reading the DOM. A delegated `next:removed` listener completes the island's life cycle, walking the detached subtree and calling `app.unmount()` for every chart it finds, so navigating away from the page never leaks a Vue root. The SSR bars in `component.djx` are the no-JavaScript fallback, so the page degrades to plain server-rendered bars when scripting is off.

Two serialize providers feed one JS object here, the component-level `results` and the page-level `live_results`. `JS_CONTEXT_POLICY: "next.static.collector.DeepMergePolicy"` in [`config/settings.py`](config/settings.py) is what lets them coexist: the policy merges nested dicts key by key instead of letting the later writer replace the whole object, so a `context` patch that carries only `live_results` leaves `results` intact.

### 7. Inherit context across the layout chain

`polls/page.py` declares `active_polls_count` with `inherit_context=True` so descendant pages, including the detail page and its child stream endpoint, share the same value without a second query. Both index callables ask [`polls/services.py`](polls/services.py), `poll_index()` over the `with_totals()` method of `PollQuerySet` and `poll_count()`, since a poll has no owner to narrow it by and a page tree reads only through `visible_to(...)` or a function of `services.py`. The root layout reads it for the header badge. The `[int:id]/page.py` adds `poll` with `inherit_context=True`. The nested `[int:id]/layout.djx` and the inner template both consume `poll` directly, demonstrating context flow without a context processor.

### 8. DI through `DPoll[Poll]`

```python
def _by_url_or_post(model_cls, context):
    pk = context.url_kwargs.get("id")
    if pk is None and context.request is not None:
        pk = context.request.POST.get("poll")
    if pk is None:
        return None
    try:
        return model_cls.objects.get(pk=pk)
    except model_cls.DoesNotExist as exc:
        raise Http404 from exc


class PollProvider(RegisteredParameterProvider):
    def can_handle(self, param, _context):
        return self.static_can_handle(param)

    def static_can_handle(self, param):
        return get_origin(param.annotation) is DPoll

    def resolve(self, param, context):
        (model_cls,) = get_args(param.annotation)
        return _by_url_or_post(model_cls, context)

    def compile_resolve(self, param):
        (model_cls,) = get_args(param.annotation)
        return partial(_by_url_or_post, model_cls)
```

`DPoll[Poll]` resolves the URL kwarg `id` for page rendering and falls back to the POST `poll` field, the hidden input `VoteForm` already carries, when the dispatcher hands over a form-action request that has no URL kwargs of its own. The stream endpoint and the page-level `poll` callable both consume the same provider, so the model fetch lives in one place. The lookup returns `None` rather than raising when neither source names a poll, which lets a caller with a default keep working, and it maps a missing row to `Http404` so a stale poll id answers `404` instead of `500`.

`static_can_handle` reads the verdict off the annotation, so the plan compiler hands the parameter to `PollProvider` once per callable rather than polling every provider on each request, and `compile_resolve` unpacks the model out of `DPoll[Poll]` once per plan so a vote pays for the query alone. Both the filler and `resolve` call `_by_url_or_post`, so the two paths cannot drift apart.

Page and component modules that use `DPoll[Poll]` never start with `from __future__ import annotations` and import both names at runtime. The resolver does evaluate string hints through `get_type_hints`, but a single name it cannot evaluate, such as a marker or a model imported only under `if TYPE_CHECKING`, drops the whole callable back to its raw annotations, where `get_origin` sees a string and the parameter silently falls through to another provider.

### 9. Two composites at two scopes

`poll_card` is the index list composite. It has no `component.py` and no Vue, only markup and a stylesheet, and it sits in `polls/_components/` at the section root because the index template is what renders it. `poll_chart` is the detail composite that owns the Vue layer, and it sits in `polls/[int:id]/_components/` because only the poll page renders it.

Component visibility follows the directory tree. A component is in scope for every template at or below its scope root, so the detail template can call `poll_card` but the index template cannot call `poll_chart`. Placing a composite at the narrowest scope that uses it keeps the name from leaking into pages that have no data to feed it.

### 10. A vote tab titled after its question

[`studio/layout.djx`](studio/layout.djx) calls the shared `page_head` component without a title, and the builtin `{% metadata %}` tag inside it folds `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` from [`config/settings.py`](config/settings.py) with what the page tree declares. [`polls/page.py`](polls/pages/polls/page.py) titles the index with the one-line `metadata: MetadataDict = {"title": "Polls"}`, and [`[int:id]/page.py`](polls/pages/polls/[int:id]/page.py) registers a callable beside the `poll` context of section 7:

```python
@page.metadata
def poll_meta(poll: Poll) -> MetadataDict:
    return {"title": poll.question}
```

The parameter is named after the context key, so the callable receives the row that context resolved through `DPoll[Poll]` and the tab reads `Tabs or spaces? · next.dj polls`. The index dict still reaches the vote page as a parent, and the callable's title overrides it. The bare root is a redirect and the stream endpoint hands its response back verbatim from `render()`, so neither renders a head. Every poll is public, so no page carries a robots directive. One parametrized integration test pins both titles.

### 11. An embeddable widget with its own document

[`embed/layout.djx`](polls/pages/polls/[int:id]/embed/layout.djx) opens with `{% layout root %}`. That marker makes it the root of its chain, so neither `[int:id]/layout.djx`, `polls/layout.djx` nor the `studio/` envelope wraps the page. The layout writes its own `<html>`, pulls `page_head` for `{% metadata %}` and `{% collect_styles %}`, and ends with `{% collect_scripts %}`. Its `layout.css` is served and the studio chain adds nothing.

What a cut layout used to provide is still there where it comes from `page.py`. The `poll` context is `inherit_context`, so the shell prints the question. The page declares its own `@page.metadata` for the tab title and a `live_results` provider, because the vote handler patches that key on the origin page and the parent's provider does not reach a page of its own. The template repeats the `poll-results` zone and the `data-next-sse` element, and `page.vue` imports the parent mount entry, so a vote on the poll page repaints the widget over the same stream. A cross-site `<iframe>` also needs the `X-Frame-Options` header lifted, which this example leaves at Django's default.

### 12. The embed snippet as an intercepting route

The "Embed" link of the vote page carries `data-next-layer="embed-code"`. A click asks for `/polls/1/embed/` with the zone name, and a modal with the iframe snippet opens over the live vote page while the address bar shows the widget URL. The chart keeps counting behind the modal, a reload or a shared link renders the real widget of section 11, and Back closes the snippet. The modal ends in a plain "Preview the widget" link, which navigates to the widget.

The modal is the directory [`(.)embed`](polls/pages/polls/%5Bint%3Aid%5D/%28.%29embed/) beside `embed/`. The scope of an intercept is the URL trail of the directory that holds it, `polls/[int:id]`, and `(.)` appends `embed` to it, so the target is the widget page. The intercept routes nothing, so `uv run python manage.py routes` lists it right under its target with the zone it serves:

```
URL                   Name                          File                                         Access  Intercept
/polls/<int:id>/embed/  next:page_polls_int_id_embed  polls/pages/polls/[int:id]/embed/page.py     -
/polls/<int:id>/embed/  -                             polls/pages/polls/[int:id]/(.)embed/page.py  -       embed-code
```

The intercept [`page.py`](polls/pages/polls/%5Bint%3Aid%5D/%28.%29embed/page.py) builds the snippet from the `poll` context it inherits from `[int:id]/page.py` and `request.build_absolute_uri`, and titles the tab `Embed · Tabs or spaces? · next.dj polls`. The [`template.djx`](polls/pages/polls/%5Bint%3Aid%5D/%28.%29embed/template.djx) wraps a read-only textarea in `{% zone "embed-code" %}` with no layout around it. The widget page is untouched and carries no `embed-code` zone, so the intercept answers that zone name alone.

`TestEmbedCode` pins the server side and the e2e tests in [`tests/e2e/test_polls.py`](tests/e2e/test_polls.py) open the snippet, close it with Back and Escape, and follow its link to the widget.

## Gotchas

### The asset-version guard needs an explicit version

Vite hashes the filenames it builds, so the asset URLs need no `v` parameter and the example sets no `STATIC_VERSION`. The partial guard still wants a stamp that moves, and with no `STATIC_VERSION` to derive from and no hashed manifest storage behind staticfiles it would resolve to a constant, so the example pins a release tag in `PARTIAL_BACKENDS` instead. Bumping it after a rebuild is what tells an open tab its bundle is stale. The [examples README](../README.md#conventions-every-example-follows) covers the convention.

### A page that always redirects refuses submissions it would own

[`polls/pages/page.py`](polls/pages/page.py) is a `render()` that redirects the bare root to `/polls/` unconditionally. Form dispatch authorizes the posted origin page by replaying its `render()`, so any submission naming `/` as its origin would come back as that redirect instead of being handled. Nothing here does: `/` never renders HTML, and the only `{% form %}` of the example sits in `poll_chart` under `polls/[int:id]/_components/`, so the poll page is the only origin it can carry. The refusal is honest rather than accidental, because a page that redirects every visitor serves nobody and the authorization call asks it as a GET of `/` exactly as a browser would. A redirect meant to let some requests through stays conditional and is reproduced faithfully, query string and method included.

## Further reading

- [`polls/apps.py`](polls/apps.py): `PollsConfig.ready()` with the two registry calls.
- [`polls/signals.py`](polls/signals.py): `broadcast_vote` receiver plus the dev-mode Vite injector.
- [`polls/snapshots.py`](polls/snapshots.py): the snapshot builder used by page rendering.
- [`polls/backends.py`](polls/backends.py): `ViteManifestBackend` dev/prod URL routing.
- [`polls/deps.py`](polls/deps.py): the `DPoll` marker and the `PollProvider` that resolves it.
- [`polls/pages/polls/[int:id]/stream/page.py`](polls/pages/polls/%5Bint%3Aid%5D/stream/page.py): the `TopicStream` page module.
- [`polls/pages/polls/[int:id]/_components/poll_chart/component.vue`](polls/pages/polls/[int:id]/_components/poll_chart/component.vue): the chart Vue SFC.
- [`next/partial/sse.py`](../../next/partial/sse.py): the SSE framing, cache headers, and heartbeat contract.
- [`next/partial/patches.py`](../../next/partial/patches.py): the `Patches` builder and the `refresh` verb.
- [`next/forms/signals.py`](../../next/forms/signals.py): `action_dispatched` payload contract used by the receiver.
- [`next/static/signals.py`](../../next/static/signals.py): `collector_finalized` signal that drives the Vite dev preamble.
- [`next/components/context.py`](../../next/components/context.py): `@component.context` and the `serialize=True` flag.
- [`next/pages/metadata/`](../../next/pages/metadata/): the metadata chain behind the index dict and the `@page.metadata` callable of section 10.
- [`docs/content/ref/system-checks.rst`](../../docs/content/ref/system-checks.rst): `next.W074` for a kind with no insertion verb.
- [`next/intercept.py`](../../next/intercept.py) and [`next/partial/intercept.py`](../../next/partial/intercept.py): the intercept table and the zone selection of section 12.
