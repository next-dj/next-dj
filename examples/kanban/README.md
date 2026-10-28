# Kanban

A drag-and-drop Kanban board powered by co-located React components and a Vite build pipeline. The example demonstrates that adding a brand-new asset type (`.jsx`) to next.dj requires no changes to the framework core. A single custom `StaticFilesBackend` subclass for URL resolution, two registry calls in `AppConfig.ready()`, and one signal receiver are the entire integration layer. Rendering uses the framework built-in `render_module_tag`, so the example registers no custom renderers. Server rendering keeps every page useful without JavaScript, and the React layer adds native HTML5 drag-and-drop and optimistic card creation on top. The board is live: every open tab subscribes to a `board:<id>` topic, sees a move made in another tab without a reload, shows an avatar for everyone watching it, guests included, and marks the card someone else is dragging.

## What you will see

| URL | Description |
| --- | --- |
| `/` | List of active boards. Archived boards are hidden. A `Peek at columns` link on each card opens the column summary as a modal over the list. |
| `/board/<id>/` | Every column, card, and "New card" form rendered by the server, with React mounting drag-and-drop, optimistic create, the watcher avatars and the dragging marks over the same markup. |
| `/board/<id>/stream/` | The `TopicStream` of `board:<id>`, negotiated as a WebSocket under ASGI and as SSE otherwise, with presence listing each signed-in watcher by name and initials and each guest by a short tag. |
| `/sign-in/` | Sign in as a demo member, so the board shows your name instead of a guest tag. Signing in is optional, every page works for a guest. |
| `/board/<id>/settings/` | Forms to rename a board, archive it, or add a column with a WIP limit. |
| `POST move_card_form` | Move a card across columns. The card arrives through `DCard[Card]` and the target column is a `ModelChoiceField` that `get_form_kwargs` narrows to that card's board, so a foreign column never validates. `kanban.services.move_card` renumbers card positions inside an atomic transaction. The action opts into `transport = "any"`, so the board sends it through `Next.partial.submit` over its open WebSocket, and a committed move tells the other tabs to refresh the board. |
| `POST create_card_form` | Create a card at the tail of a column under `select_for_update`. Rejected when the column is at its WIP limit. Redirects to `/board/<id>/?created=<card_id>`, which names the new row for the React layer. |
| `POST create_column_form` | Append a column with an optional `wip_limit` to the board whose settings page posted the form. |
| `POST rename_board_form` | Update the board title. |
| `POST archive_board_form` | Toggle the archived flag. Archived boards drop out of the index. |
| `POST sign_in_form` / `POST sign_out_form` | Start and end the session of a demo member. Both stay on plain HTTP, since a login rotates the session. |

Three demo boards and two demo members live in `kanban/demo.py` and load through `manage.py seed_demo`. The members are `ada` (Ada Lovelace) and `grace` (Grace Hopper), both with the password `kanban`. Two are active (`engineering-roadmap` and `marketing-launch`) and one is archived (`old-experiments`). Several seeded columns carry a `wip_limit`, so the limit badge and the rejected-create path have data behind them on a fresh database.

## How to run

```bash
cd examples/kanban
uv run python manage.py migrate
uv run python manage.py seed_demo
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest
```

`seed_demo` writes the three demo boards and the two members. Migrations carry schema only, so a database without that command is empty and the index shows its empty state. Open the index to land on the board list.

The React layer loads from the Vite dev server, so start it next to Django:

```bash
npm install
npm run dev                            # http://localhost:5173
```

Django resolves every `.jsx` asset to a dev-server URL, and the React Refresh preamble plus the `@vite/client` HMR script load through the `collector_finalized` signal. `VITE_ORIGIN` overrides that origin for the asset URLs and the HMR client.

Without the dev server running, those `<script type="module">` tags point at a port nobody is listening on and the browser drops them. The board keeps working: the columns, the cards, and the per-column "New card" form all come from the server, and only drag-and-drop and the optimistic create are missing. Section 6 covers that fallback in full.

A typed route segment such as `[int:board]` puts a colon in the directory name, and the Vite dev server refuses to serve a path holding one before it consults `server.fs.allow`. Co-locating assets under a typed segment therefore needs `server: { fs: { strict: false } }`, which both `vite.config.ts` and `vitest.config.ts` carry. Only the dev server applies that guard, so the production build and Django static serving stay untouched.

For a production-shaped build, hand the backend an empty origin so it resolves through the manifest instead of the dev server:

```bash
npm run build                          # writes hashed files into kanban/static/kanban/dist/
VITE_ORIGIN= uv run python manage.py runserver
```

The origin decides the mode on its own rather than following the presence of a build, so a stale `dist/` never silently changes how `runserver` behaves.

The backend reads `dist/.vite/manifest.json` and delegates URL resolution to Django staticfiles. If the manifest file is missing, the backend logs a single warning and falls back to staticfiles so dev workflows stay unblocked.

The Vite output is served through plain staticfiles rather than a hashed manifest storage, so the asset-version guard has nothing to derive a stamp from and the example names a release tag by hand. Bumping it after a rebuild is what tells an open tab its bundle is stale. The [examples README](../README.md#conventions-every-example-follows) covers the convention.

`manage.py runserver` is the command of next.dj here, because `next` sits above `django.contrib.staticfiles` in `INSTALLED_APPS` and Django runs the command of the app listed first. With uvicorn installed, as in the dev dependencies, it serves the application `ASGI_APPLICATION` names, [`config/asgi.py`](config/asgi.py), which wraps Django with `next.realtime.application`, so the board negotiates a WebSocket with SSE as the fallback. A production-shaped server runs the same module:

```bash
uv run uvicorn config.asgi:application --ws-max-size 65536
```

To watch the board live, open the same board in two browsers, or in a normal and a private window. Each guest shows as `Guest` plus four characters, and signing in as `ada` or `grace` puts a name on the avatar instead. Section 12 walks through what happens.

The React side has its own suite. `npm test` runs Vitest over the four co-located `*.test.jsx` files next to the sources they cover, under jsdom with `@testing-library/react`.

## Walking the code

### 1. Co-location structure

Every route and component owns its asset files in the same directory:

```
pages/board/[int:board]/
├── page.py           <- @context callables building the board payload
├── page.jsx          <- named export Board plus the React island mount/unmount pair
├── page.test.jsx     <- Vitest + RTL tests for Board
├── template.djx      <- the board zone with the island root and the stream element
├── layout.djx        <- board header + Board/Settings nav
├── settings/
│   └── template.djx  <- rename, add-column, and archive forms, a virtual route with no page.py
├── stream/
│   └── page.py       <- TopicStream for board:<id> with presence
└── _components/
    ├── card/
    │   ├── component.py
    │   ├── component.djx
    │   ├── component.jsx       <- export function Card({ id, title, excerpt, pending })
    │   └── component.test.jsx
    ├── column/
    │   ├── component.py
    │   ├── component.djx       <- column markup plus the create_card form
    │   ├── component.jsx       <- export function Column({ column, onDrop, onCreate })
    │   ├── component.css
    │   └── component.test.jsx
    ├── presence/
    │   ├── component.jsx       <- export function Presence({ count, members })
    │   └── component.test.jsx
    └── preview/
        └── component.djx       <- "Move complete" SSR fallback
```

The outer HTML envelope is not in this tree. It lives in [`cockpit/layout.djx`](cockpit/layout.djx), the project-level page root listed in `PAGE_BACKENDS["DIRS"]`, while the routable pages come from the app through `APP_DIRS = True` and `PAGES_DIR = "pages"`.

`layout.djx` renders the Board and Settings tabs with the shared `nav_link` component and feeds it `url_kwargs=board_url_kwargs`, an inherited `@context` that returns `{"board": board.pk}`. The tabs therefore reverse `next:page_board_int_board` and `next:page_board_int_board_settings` by name, and the call site never spells out the `[int:board]` converter or a literal `/board/<id>/` path.

### 2. Two calls in `apps.py`

```python
class KanbanConfig(AppConfig):
    def ready(self) -> None:
        default_kinds.register(
            "jsx", extension=".jsx", slot="scripts", renderer="render_module_tag"
        )
        default_stems.register("template", "page")
```

`render_module_tag` is the framework's built-in renderer for ES module assets, so jsx files reuse it without a custom renderer. Nothing else lives in `ready()`. The framework imports the `deps.py`, `forms.py`, `signals.py` and `realtime.py` of every installed app at startup, so [`kanban/deps.py`](kanban/deps.py) registers the `scope` dependency and the `DCard` provider, [`kanban/signals.py`](kanban/signals.py) wires the dev-mode `collector_finalized` receiver, the board broadcast and the drag release of a closed socket, and [`kanban/realtime.py`](kanban/realtime.py) registers the `dragging` handler, without an import anywhere. No other integration point in the framework is touched.

### 3. `ViteManifestBackend`

The custom backend resolves URLs only and delegates rendering to the built-in `render_module_tag`:

```python
class ViteManifestBackend(StaticFilesBackend):
    def register_file(self, source_path, logical_name, kind):
        if kind != "jsx":
            return super().register_file(source_path, logical_name, kind)
        if self._dev_origin:
            return self._build_dev_url(source_path)
        if self._manifest_path:
            return self._resolve_from_manifest(source_path, logical_name)
        return super().register_file(source_path, logical_name, kind)
```

Three resolution modes: a Vite dev-server URL when `DEV_ORIGIN` is set, a hashed manifest entry in production, or unmodified Django staticfiles when neither applies. Missing manifest emits a single warning and falls back to staticfiles.

### 4. React Refresh preamble and `@vite/client` via a guarded signal

```python
def inject_vite_dev_assets(sender: object, **kwargs) -> None:
    origin = _dev_origin()
    if not origin or not _has_module_assets(sender):
        return
    preamble_url = (
        "data:text/javascript;base64,"
        + base64.b64encode(preamble_code.encode()).decode()
    )
    sender.add(StaticAsset(url=preamble_url, kind="module"), prepend=True)
    sender.add(StaticAsset(url=f"{origin}/@vite/client", kind="module"), prepend=True)


if settings.DEBUG:
    collector_finalized.connect(inject_vite_dev_assets)
```

`sender` is the collector instance. Three guards stack up so no dev plumbing ever escapes into a built page. The `connect` call itself only runs under `DEBUG`. `_dev_origin()` reads `DEV_ORIGIN` back out of the static backend's `OPTIONS`, so `VITE_ORIGIN=` empties the origin and the receiver returns before adding anything, exactly as it flips the backend into manifest mode. `_has_module_assets` keeps the preamble off pages that carry no jsx at all, such as the board index.

Both assets go in as URL-form module scripts rather than inline bodies. `@vitejs/plugin-react` requires its refresh preamble to execute before any jsx module loads, and the collector force-appends inline assets, so an inline preamble would land after the modules it has to precede. Encoding the preamble source into a `data:text/javascript;base64,` URL keeps it a single self-contained module script that obeys `prepend=True`. Prepends stack, so the final order is preamble, `@vite/client`, page modules.

### 5. React receives data from `window.Next.context.board`

`page.py` flags the board payload serialisable and builds it through [`kanban/snapshots.py`](kanban/snapshots.py) in one `prefetch_related` chain:

```python
@context("board", inherit_context=True, serialize=True)
def board_payload(board: DObject[Board], request: HttpRequest) -> dict[str, object]:
    return board_snapshot(board, request)


def board_snapshot(board, request):
    cols = board.columns.prefetch_related("cards").order_by("position")
    return {
        "id": board.pk,
        "topic": board_topic(board.pk),
        "csrf": get_token(request),
        "move_card_url": form_action_manager.get_action_url("move_card_form"),
        "create_card_url": form_action_manager.get_action_url("create_card_form"),
        "columns": [{..., "wip_limit": col.wip_limit, "cards": [
            {"id": c.id, "title": c.title, "position": c.position, "excerpt": c.excerpt}
            for c in col.cards.all()
        ]} for col in cols],
    }
```

`page.jsx` reads it at mount time, never owns URL constants, CSRF tokens or topic names, and re-uses the same payload to drive optimistic updates. The move action answers with the same snapshot, which is why it lives outside the page module.

The React root follows the framework-island contract. `Next.partial.onMount("#kanban-board", ...)` mounts the board when the element appears, a `WeakMap` guard keeps repeated `onMount` passes from creating a second root on a live element, and a delegated `next:removed` listener unmounts the root when the mount point detaches, whether the event fires on the element itself or on an ancestor whose subtree contains it.

`DeepMergePolicy` is configured as the JS-context policy in [`config/settings.py`](config/settings.py), so the layout-level `active_boards_count` from `pages/page.py` and the page-level `board` payload merge into one `window.Next.context` object instead of the later writer replacing the earlier one. The same backend runs `HashContentDedup`, which keys assets by the sha256 of their content, so two hashed bundle filenames holding identical bytes collapse to one tag after a build.

### 6. The same board with and without JavaScript

`template.djx` renders the whole board server-side: `{% component "column" %}` per column, `{% component "card" %}` per card, and a `{% form "create_card_form" %}` block in every column footer. Without JavaScript the page stays a working board: columns, cards, and one "Add" button per column that posts, redirects, and re-renders. React then mounts into `#kanban-board` and replaces that markup with the same layout driven by `window.Next.context.board`, down to the "New card" input, so the swap is invisible. The root carries `data-next-keep`, so a morph of the surrounding `board` zone in section 12 never walks into the React tree.

### 7. Optimistic move and create with rollback

`Board` keeps `columns` in `useState`. A drop applies the move locally through a pure `applyMoveLocally(columns, cardId, targetColumnId, targetPosition)` helper, so the UI never waits on the network, and then awaits the outcome of the action:

```javascript
const { status } = await partial.submit({
  action: ctx.move_card_url,
  data: { card_id: cardId, target_column: targetColumnId, target_position: targetPosition },
  zone: "board",
  transport: "any",
  key: `card:${cardId}`,
});
```

`Next.partial.submit` sends the action through the same pipeline as a `{% form %}` on the page, CSRF, origin field and request id included, and resolves with an `Outcome` instead of leaving the island to guess from events. `ok` keeps the move. `invalid` and `error` put back the columns the server last sent and raise an inline error banner. An invalid reply morphs the `board` zone and carries its serialisable context with it, so by the time the promise resolves `context-updated` has already handed the island the board as it stands after the refusal. A card someone deleted in the meantime therefore disappears instead of coming back. The `key` asks the runtime for latest-wins on that queue, and without one submits run first in, first out and none is dropped. A second drop of the same card while the first is in flight queues behind it, and a third replaces the queued one, which then resolves `dropped` and changes nothing, because the reply of the newer move brings the board along. Drops of two different cards carry two keys, so neither waits for nor replaces the other and both reach the server. Until its outcome arrives a move stays in a `movesInFlight` map, and every `context-updated` replays those moves over the fresh board, so the reply to the first of two parallel moves, or another tab's refresh, never snaps the second card back while it is still on its way. A plain post without the runtime still redirects with `?moved=<card_id>`, which drives the SSR `preview` composite.

Creating a card has the same shape plus one extra problem. The action answers with a redirect and no body, so the client holds no id for the row it just drew. `CreateCardForm.on_valid()` therefore redirects to `/board/<id>/?created=<card_id>`, mirroring the `?moved=` convention, and the island asks `submit` for `navigate: false`, so the page stays in place and the redirect target arrives in `Outcome.location`. Until it does, the optimistic card lives under a client-side `pending-<n>` key, renders with `data-kanban-card-pending`, and is not draggable, because the server would not recognise that key. A confirmed id replaces the placeholder key in place. A post the server rejects, most often on a WIP limit, drops that one pending card and raises the error banner, so the client never keeps a card it cannot name. A `dropped` outcome means the runtime withdrew the post itself, so the pending card goes without a banner, and if the post did land the next refresh of the board brings the card back. The create path removes the pending card instead of restoring a snapshot the way the move path does, so a drag that landed while the post was in flight survives the rollback.

```javascript
const outcome = await partial.submit({
  action: ctx.create_card_url,
  data: { column: columnId, title: text },
  navigate: false,
});
```

`ok` with a `location` that carries `created` names the card. Any other outcome drops the pending card and the banner names the reason. An `invalid` outcome carries the form errors in `Outcome.errors`, so the banner reads `Column has reached its WIP limit.` instead of a generic rejection. A `400` from the race check of section 8 resolves as `error`, and its plain-text body arrives in `Outcome.error.body`, so the banner shows that same sentence instead of a network error. Only a failure that names no reason falls back to the network banner for `error` and the rejection banner otherwise. CSRF and the request id come from the pipeline, so the island holds no token of its own.

### 8. WIP-limit invariant in two layers

`CreateCardForm` binds its column through `ModelChoiceField`, so an unknown id fails on the field instead of in a hand-written lookup, and `clean()` performs a best-effort count check so common posts surface a friendly error before the save runs. The authoritative check sits inside `CreateCardForm.on_valid()` under `Column.objects.select_for_update()` and returns a plain-text `HttpResponseBadRequest` with the same `WIP_LIMIT_REACHED` sentence if a concurrent post fills the slot between form validation and insertion. All seven form classes live in the app-level [`kanban/forms.py`](kanban/forms.py) picked up by autodiscover, because every one of them is shared across pages or components. The React layer reflects the limit live through the WIP badge that turns red when `cards.length` exceeds `wip_limit`, and a rejected create rolls its optimistic card back out.

### 9. Every board through one scope

Every board a page or a settings form loads by URL arrives as `DObject[Board]` from `next.deps`. The parameter is named after the `[int:board]` segment, so `board: DObject[Board]` reads the pk out of the `board` URL kwarg and loads the row through `Board.objects.visible_to(scope)`, and a pk that query does not return answers `404`. That is why the directory is `[int:board]` rather than `[int:id]`, because the kwarg name is the parameter name and a parameter called `id` would shadow the builtin. `manage.py check` reports a `DObject` whose route captures no kwarg of its name as `next.W126`.

`scope` is a named dependency the project registers itself, in [`kanban/deps.py`](kanban/deps.py):

```python
@resolver.dependency("scope")
def scope(request: HttpRequest) -> AbstractBaseUser | AnonymousUser:
    return request.user
```

The demo has no accounts, so `BoardQuerySet.visible_to` in [`kanban/models.py`](kanban/models.py) ignores the visitor and returns every board, archived ones included. That method is the one place a project with accounts narrows boards by membership, and every board page, settings form and card action follows it without another edit. The index and its counts read through it as well, `Board.objects.visible_to(user).active()` and `.archived()` with the visitor taken as `Depends("scope")`, because every read of a page tree goes through `visible_to(...)` or a function of `services.py`. A project that declares a `DObject` and registers no `scope` fails `manage.py check` with `next.E157`, and a model whose default manager has no `visible_to` with `next.E153`.

`RenameBoardForm` and `ArchiveBoardForm` are `ModelForm`s over the board the operator is already looking at. Both inherit `get_initial` from `BoardSettingsForm`, a base marked `Meta.abstract = True` so it registers no action of its own, and that `get_initial` takes `board: DObject[Board]` and returns it, so the dispatcher binds the instance from the `board` kwarg of the posted origin URL and neither form carries a hidden id. `Meta.instance_from_url` would load the same row with a plain `get_object_or_404`, past `visible_to`, which is why both forms ask the resolver instead. `CreateColumnForm` creates a row under the board rather than editing one, so its `on_valid` takes `board: DObject[Board]` directly and the form carries no board field at all.

The React island sends both actions through `Next.partial.submit`, and `move_card_form` carries the origin of the board page, which `Next.partial.submit` adds, only so its reply can patch the client context, so neither action has a URL kwarg of its own to look a row up by and `DObject` does not apply. `MoveCardForm` names its card in POST `card_id`, which `CardProvider` resolves for `on_valid` as `card: DCard[Card]`, the key both sides read off the shared `CARD_PARAM` constant. The target column needs the card earlier, while the form validates, so `MoveCardForm.get_form_kwargs` hands the constructor the columns of the visible board the posted card sits on:

```python
@classmethod
def get_form_kwargs(cls, request, user=Depends("scope")):
    card_pk = request.POST.get(CARD_PARAM, "")
    visible = Board.objects.visible_to(user)
    is_pk = card_pk.isascii() and card_pk.isdigit()
    boards = visible.filter(columns__cards=card_pk) if is_pk else visible.none()
    targets = Column.objects.filter(board__in=boards)
    return {**super().get_form_kwargs(request), "targets": targets}
```

The dispatcher resolves `get_form_kwargs` through the same injection as every other form hook and binds the form as `MoveCardForm(data=..., files=..., **kwargs)`. `super().get_form_kwargs(request)` keeps the `initial` that `get_initial` supplies, and `__init__` narrows `target_column` to `targets`, so a column on another board never validates and the board re-renders with the form errors. A `card_id` that is no pk at all offers no target either, rather than reaching the database. `CreateCardForm.get_form_kwargs` does the same for the column a new card lands in and hands the constructor the columns of every board `visible_to` returns, so a project that narrows the method also refuses a card posted into a column of a board the visitor cannot see.

The `?moved=<card_id>` flag of section 7 reads its card the same way. The `moved_card` context takes `board: DObject[Board]` and looks the card up among that board's cards only, so a flag naming a card on another board renders no preview.

The provider behind `DObject` and `CardProvider` answer `static_can_handle` differently, and the difference is the point. The framework settles a `DObject[...]` parameter from the annotation alone, so it becomes the terminal of that parameter in the compiled injection plan and reads the model and its `visible_to` once per plan. `CardProvider` needs a POST `card_id` before it owns anything, so it returns `False` for a foreign annotation and `None` for `DCard[...]`, which keeps it a runtime candidate whose `can_handle` runs per request, and the compiler never asks it for a filler.

Modules that use these markers never start with `from __future__ import annotations` and import both the marker and the model at runtime. The resolver does evaluate string hints through `get_type_hints`, but a single name it cannot evaluate, such as a marker or a model imported only under `if TYPE_CHECKING`, drops the whole callable back to its raw annotations, where `get_origin` sees a string and the parameter silently falls through to another provider.

### 10. One title for the board and its settings page

No template spells the `<title>`. [`cockpit/layout.djx`](cockpit/layout.djx) calls the shared `page_head` component, which renders the builtin `{% metadata %}` tag, and the tag folds `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` from [`config/settings.py`](config/settings.py) with whatever the page tree declares. The settings tier holds the template `{title} · {site_name}` and a `default`, `NEXT_FRAMEWORK["SITE"]["NAME"]` fills `{site_name}`, and the index renders that default because it names no title of its own. [`board/[int:board]/page.py`](kanban/pages/board/%5Bint%3Aboard%5D/page.py) adds the one dynamic tier:

```python
@page.metadata(inherit=True)
def board_meta(board: DObject[Board]) -> MetadataDict:
    return {"title": board.title}
```

`board` loads through the `visible_to` of section 9, so the board tab reads `Roadmap · next.dj Kanban`. A metadata callable runs for its own page only unless it says `inherit=True`, and this one has to say it. `board/<id>/settings/` is a virtual page, a `template.djx` with no `page.py` beside it, so it has nowhere to register a callable of its own. `inherit=True` puts the board callable into the metadata chain of every page below it, `DObject` reads the same `board` URL kwarg there, and the settings tab carries the board title instead of the site default. The integration tests pin the index, board and settings titles.

### 11. A column summary as an intercepting route

Each board card on the index carries two links to the same URL. The title link is plain and navigates to the board, and `Peek at columns` carries `data-next-layer="board-peek"`, so a click asks for `/board/10/` with the zone name and a modal opens over the list while the address bar shows the board URL. A reload, a shared link and the plain link render the real board with its React island, and Back closes the modal.

The modal is the directory [`(.)board/[int:board]`](kanban/pages/%28.%29board/%5Bint%3Aboard%5D/) at the root of the page tree. The scope of an intercept is the URL trail of the directory that holds it, empty here, and `(.)` appends `board/[int:board]` to it, so the target is the board page and the brackets spell it exactly. The intercept routes nothing, so `uv run python manage.py routes` lists it under its target with the zone it serves:

```
URL                  Name  File                                           Access  Intercept
/board/<int:board>/  -     kanban/pages/(.)board/[int:board]/page.py     -       board-peek
```

The intercept [`page.py`](kanban/pages/%28.%29board/%5Bint%3Aboard%5D/page.py) resolves the board through `DObject[Board]` from the `board` kwarg of the target URL, loads its columns with their cards, and titles the tab `Peek · Engineering roadmap · next.dj Kanban` until the modal closes. The [`template.djx`](kanban/pages/%28.%29board/%5Bint%3Aboard%5D/template.djx) wraps one row per column in `{% zone "board-peek" %}` with the card count and the WIP limit, and ends in a plain link to the board. The board page, its React island and its payload stay untouched, since the zone response never renders them.

`TestBoardPeek` pins the server side and [`tests/e2e/test_peek.py`](tests/e2e/test_peek.py) drives the modal in a browser, the URL and title on open and close, Back, Escape, the link to the board and the plain title link.

### 12. A live board

Every open board is a subscriber of one topic. The template ends with `<div data-next-stream="/board/{{ board_object.pk }}/stream/" hidden>`, and [`stream/page.py`](kanban/pages/board/%5Bint%3Aboard%5D/stream/page.py) answers that URL with the stream:

```python
def render(request: HttpRequest, board: DObject[Board]) -> TopicStream:
    return TopicStream(
        request,
        board_topic(board.pk),
        authorize=can_watch_board,
        presence="kanban.realtime.identify_member",
    )
```

`DObject[Board]` loads the board through `visible_to` as every board page does, so an unknown board answers `404` before a subscription opens. `can_watch_board` repeats that check by topic name, because the socket and the SSE stream call it again once per heartbeat, and it is importable, which is what lets the page issue a WebSocket ticket at all.

**Who is watching.** `presence` is a dotted path to `identify_member` in [`kanban/realtime.py`](kanban/realtime.py):

```python
def identify_member(user, _topic, subject):
    key = subject[:MEMBER_KEY_LENGTH]
    if user.is_authenticated:
        return {**public_profile(user), "key": key}
    if not subject:
        return None
    return {"name": f"Guest {subject[:GUEST_TAG_LENGTH]}", "initials": "G", "key": key}
```

A signed-in member is `{"name": "Ada Lovelace", "initials": "AL", "key": "<12 hex>"}`. A guest is named after the first `GUEST_TAG_LENGTH` (four) characters of `subject`, the opaque digest the framework derives for the connection's session, so the tag stays the same across a reload and both transports of one browser and reveals nothing about the session itself. The WebSocket negotiation creates a session for a guest that has none, so a guest on a socket always gets a tag, while an SSE stream without a session cookie has an empty subject and counts its watcher unnamed. Members with equal mappings count once, so the `key`, the first twelve characters of the same digest, keeps two people who share a display name, or two guests whose tags collide, apart. The UI never shows it, and it is a keyed digest rather than the user's primary key, so it tells a watcher nothing but that two avatars are two accounts. The mapping is everything the other watchers learn, no username, e-mail or primary key. The broker publishes each change of attendance as the `next:presence` event, and `Board` subscribes to it with `Next.partial.stream.on(topic, "next:presence", ...)`. The `Presence` component draws one avatar per member and adds the unnamed watchers the `count` holds beyond them, so two tabs of one browser show once and a watcher without a tag shows as `+1 guest`.

**A move, everywhere.** `MoveCardForm` sets `Meta.transport = "any"`, and the island asks for the same with `transport: "any"`, so the runtime sends the move through the WebSocket the board already holds, with the same CSRF check, session and guards as an HTTP post. Without a socket, under WSGI or before it opens, the same submission goes by `fetch`. `on_valid` answers a runtime request with `Patches(request).context(board=...)`, so the sender's island adopts the board the server now holds. The `action_dispatched` receiver in [`kanban/signals.py`](kanban/signals.py) then publishes after the commit:

```python
publish(board_topic(column.board_id), Patches(request).refresh(zone="board"))
```

`Patches(request)` stamps the id of the submitting request, so the sender's tab drops its own echo. Every other tab GETs the `board` zone with its own cookies, the morph leaves the `data-next-keep` root alone, and the zone reply carries the page's serialisable context, so `context-updated` hands the island the fresh `board` and it re-renders the columns, keeping any card it is still creating. A created card publishes the same refresh. A move the form rejects publishes nothing.

**Who holds a card.** Dragging is not worth a database write, so it travels as an upstream event. `Card` reports `dragstart` and `dragend` to `Board`, which calls `Next.partial.stream.send(topic, "dragging", {card, active}, {ack: false})`. A drop into another column moves the card under another column key, React unmounts the node the drag started on, and its `dragend` fires on a detached node React no longer listens to. `moveCard` therefore sends `active: false` itself, and `Board` keeps the set of cards it announced, so the `dragend` of a drop inside one column does not send a second end. `Column` ignores a drop whose `dataTransfer` carries no card, such as a file or a text selection dragged in from outside, so no move with an empty `card_id` reaches the server. The handler lives in `kanban/realtime.py`:

```python
@on("board:{id}", "dragging")
def dragging(upstream: Upstream) -> None:
    ...
    cards = Card.objects.filter(column__board_id=upstream.params["id"])
    if not cards.filter(pk=card).exists():
        return
    by = identify_member(upstream.user, upstream.topic, upstream.subject) or GUEST
    detail = {"card": card, "active": active, "by": by, "from": upstream.connection}
    publish(
        upstream.topic, Patches(None).event("board:dragging", detail), on_commit=False
    )
```

It drops anything that is not an integer card and a boolean flag, and a card from another board, because `data` is untrusted input and the ticket only proves the visitor may watch this topic. `by` is the same mapping presence shows, so a guest dragging a card is the same `Guest` tag as its avatar. Every tab hears `board:dragging` through `stream.on`, marks the card with the initials and name of whoever holds it, and clears the mark on `active: false`.

A publication reaches the sending tab too. `from` is `upstream.connection`, the id the WebSocket `hello` gave that one socket, so the island drops its own echo with one comparison:

```javascript
stream.on(topic, "board:dragging", (detail) => {
  if (detail.from === stream.meta()?.connection) return;
  setDragging((current) => trackDragging(current, detail));
});
```

The id names a socket, not a person, so a second tab of the same member still sees the mark. `trackDragging` keeps the holders of each card keyed by that socket id, so one tab ending a drag never clears a mark another tab of the same member still holds. A tab closed in the middle of a drag never sends `active: false`, and presence cannot tell, because a member with a second tab open stays among the members. The `stream_unsubscribed` receiver in [`kanban/signals.py`](kanban/signals.py) covers that case:

```python
@receiver(stream_unsubscribed, sender=TopicSocket)
def release_socket_drags(
    connection: TopicSocket, topics: tuple[str, ...] = (), **kwargs
) -> None:
    detail = {"from": connection.connection_id}
    for topic in topics:
        if board_pk(topic) is not None:
            event = Patches(None).event("board:released", detail)
            publish(topic, event, on_commit=False)
```

Every tab hears `board:released` and drops the marks of that one socket, whoever else of the same member still watches. A `next:presence` event that no longer lists a holder drops that holder's marks too. Over SSE there is no upstream channel, `send` resolves to `false`, and the board simply shows no marks.

The integration tests cover the authorizer, the member and guest profiles, the stream with a signed-in and a guest watcher, the partial move reply with its context and its published refresh, the invalid reply that carries the board, and the handler both directly and through a real socket, where `from` equals the `connection` of the socket's `hello` and the closing socket publishes `board:released` under the same id. [`tests/e2e/test_realtime.py`](tests/e2e/test_realtime.py) serves the app under uvicorn through the shared `e2e_support.server.serve_realtime`, the one helper the three realtime examples share, and drives two browser contexts. Two guests see two `Guest` avatars without signing in. Signed in as Ada and Grace, both names show in both tabs, a card dragged in one tab lands in the other without a reload, without an HTTP post of the move and without a zone GET in the sender, a card picked up in one tab is marked in the other until it is dropped and never in the tab that holds it, the mark goes away when a drop lands the card in another column without a `dragend`, and it goes away when the tab that holds the card leaves the board, also while a second tab of the same member stays on it.

## Gotchas

### The asset-version guard needs an explicit version

Vite hashes the filenames it builds, so the asset URLs need no `v` parameter and the example sets no `STATIC_VERSION`. The partial guard still wants a stamp that moves, and with no `STATIC_VERSION` to derive from and no hashed manifest storage behind staticfiles it would resolve to a constant, so the example pins a release tag in `PARTIAL_BACKENDS` instead. Bumping it after a rebuild is what tells an open tab its bundle is stale. The [examples README](../README.md#conventions-every-example-follows) covers the convention.

### Two moves at once need an immediate SQLite transaction

Drops of two cards reach the server in parallel, and each `move_card` is a read followed by writes. SQLite refuses to upgrade a deferred transaction that a concurrent writer overtook, so `settings.py` sets `"OPTIONS": {"transaction_mode": "IMMEDIATE"}` and the second writer waits for the first instead of failing with `database is locked`. The test database is a file for the same reason, because the shared in-memory one answers `table is locked` at once.

### Sibling-form input is not preserved on a failed submit

The settings page hosts three independent `<form>` blocks. Each one posts to its own action URL, so a submit sends only that form's fields. If a user starts typing into "Add column", then submits "Rename board" and rename fails validation, the re-rendered page shows the rename errors but the "Add column" text is gone. The browser never transmitted the unsubmitted form's input, so the server has nothing to restore. This is inherent to the server-side post-and-rerender model and is best addressed client-side. Submitting a single form at a time is unaffected.

## Further reading

- [`kanban/apps.py`](kanban/apps.py): `KanbanConfig.ready()` with the two registry calls.
- [`kanban/signals.py`](kanban/signals.py): `inject_vite_dev_assets` receiver wired in `DEBUG` mode.
- [`kanban/backends.py`](kanban/backends.py): `ViteManifestBackend` dev/prod URL routing.
- [`kanban/deps.py`](kanban/deps.py): the `scope` dependency and the `DCard` marker with its `CardProvider`.
- [`kanban/realtime.py`](kanban/realtime.py): the board topic, its authorizer, the presence profile, and the `dragging` upstream handler.
- [`kanban/signals.py`](kanban/signals.py): the `action_dispatched` receiver that publishes the board refresh and the `stream_unsubscribed` receiver that releases a closed socket's drags.
- [`docs/content/topics/realtime.rst`](../../docs/content/topics/realtime.rst): topics, presence, upstream events, and form actions over WebSocket.
- [`vite.config.ts`](vite.config.ts): glob multi-entry build that discovers all co-located `.jsx` files.
- [`vitest.config.ts`](vitest.config.ts): Vitest setup targeting `kanban/**/*.test.{jsx,tsx}`.
- [`next/static/assets.py`](../../next/static/assets.py): the public `KindRegistry` API used by `apps.py`.
- [`next/static/defaults.py`](../../next/static/defaults.py): the framework bootstrap that registers `css`, `js`, and `module` through the same call.
- [`next/static/collector.py`](../../next/static/collector.py): `HashContentDedup`, `DeepMergePolicy`, and the slot-keyed buckets.
- [`next/static/signals.py`](../../next/static/signals.py): `collector_finalized` signal fired after collection completes.
- [`next/static/manager.py`](../../next/static/manager.py): placeholder-driven injection that dispatches per-asset renderers through `getattr` on the active backend.
- [`next/components/context.py`](../../next/components/context.py): `@component.context` and the `serialize=True` flag.
- [`next/forms/manager.py`](../../next/forms/manager.py): `form_action_manager.get_action_url(...)` used by the page to lift the move and create endpoint URLs into the React layer.
- [`next/deps/providers.py`](../../next/deps/providers.py): `RegisteredParameterProvider` ABC used by `CardProvider`.
- [`next/deps/objects.py`](../../next/deps/objects.py): `DObject` and the provider that loads its row through `visible_to(scope)`.
- [`next/pages/metadata/`](../../next/pages/metadata/): the metadata chain behind `@page.metadata(inherit=True)` in section 10.
- [`docs/content/ref/system-checks.rst`](../../docs/content/ref/system-checks.rst): `next.E153` to `next.E158` and `next.W126` for `DObject` and the `scope` dependency.
- [`next/intercept.py`](../../next/intercept.py) and [`next/partial/intercept.py`](../../next/partial/intercept.py): the intercept table and the zone selection of section 11.
