# Kanban

A drag-and-drop Kanban board powered by co-located React components and a Vite build pipeline. The example demonstrates that adding a brand-new asset type (`.jsx`) to next.dj requires no changes to the framework core. A single custom `StaticFilesBackend` subclass for URL resolution, two registry calls in `AppConfig.ready()`, and one signal receiver are the entire integration layer. Rendering uses the framework built-in `render_module_tag`, so the example registers no custom renderers. Server rendering keeps every page useful without JavaScript, and the React layer adds native HTML5 drag-and-drop and optimistic card creation on top.

## What you will see

| URL | Description |
| --- | --- |
| `/` | List of active boards. Archived boards are hidden. |
| `/board/<id>/` | Every column, card, and "New card" form rendered by the server, with React mounting drag-and-drop and optimistic create over the same markup. |
| `/board/<id>/settings/` | Forms to rename a board, archive it, or add a column with a WIP limit. |
| `POST move_card_form` | Move a card across columns. Renumbers card positions inside an atomic transaction. |
| `POST create_card_form` | Create a card at the tail of a column under `select_for_update`. Rejected when the column is at its WIP limit. Redirects to `/board/<id>/?created=<card_id>`, which names the new row for the React layer. |
| `POST create_column_form` | Append a column to the board with an optional `wip_limit`. |
| `POST rename_board_form` | Update the board title. |
| `POST archive_board_form` | Toggle the archived flag. Archived boards drop out of the index. |

Three demo boards live in `kanban/demo.py` and load through `manage.py seed_demo`. Two are active (`engineering-roadmap` and `marketing-launch`) and one is archived (`old-experiments`). Several seeded columns carry a `wip_limit`, so the limit badge and the rejected-create path have data behind them on a fresh database.

## How to run

```bash
cd examples/kanban
uv run python manage.py migrate
uv run python manage.py seed_demo
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest
```

`seed_demo` writes the three demo boards. Migrations carry schema only, so a database without that command is empty and the index shows its empty state. Open the index to land on the board list.

The React layer loads from the Vite dev server, so start it next to Django:

```bash
npm install
npm run dev                            # http://localhost:5173
```

Django resolves every `.jsx` asset to a dev-server URL, and the React Refresh preamble plus the `@vite/client` HMR script load through the `collector_finalized` signal. `VITE_ORIGIN` overrides that origin for the asset URLs and the HMR client.

Without the dev server running, those `<script type="module">` tags point at a port nobody is listening on and the browser drops them. The board keeps working: the columns, the cards, and the per-column "New card" form all come from the server, and only drag-and-drop and the optimistic create are missing. Section 6 covers that fallback in full.

A typed route segment such as `[int:id]` puts a colon in the directory name, and the Vite dev server refuses to serve a path holding one before it consults `server.fs.allow`. Co-locating assets under a typed segment therefore needs `server: { fs: { strict: false } }`, which both `vite.config.ts` and `vitest.config.ts` carry. Only the dev server applies that guard, so the production build and Django static serving stay untouched.

For a production-shaped build, hand the backend an empty origin so it resolves through the manifest instead of the dev server:

```bash
npm run build                          # writes hashed files into kanban/static/kanban/dist/
VITE_ORIGIN= uv run python manage.py runserver
```

The origin decides the mode on its own rather than following the presence of a build, so a stale `dist/` never silently changes how `runserver` behaves.

The backend reads `dist/.vite/manifest.json` and delegates URL resolution to Django staticfiles. If the manifest file is missing, the backend logs a single warning and falls back to staticfiles so dev workflows stay unblocked.

The Vite output is served through plain staticfiles rather than a hashed manifest storage, so the asset-version guard has nothing to derive a stamp from and the example pins `VERSION` by hand. The [examples README](../README.md#conventions-every-example-follows) covers that convention and the `next.W069` check behind it.

The React side has its own suite. `npm test` runs Vitest over the three co-located `*.test.jsx` files next to the sources they cover, under jsdom with `@testing-library/react`.

## Walking the code

### 1. Co-location structure

Every route and component owns its asset files in the same directory:

```
boards/board/[int:id]/
├── page.py           <- @context callables building the board payload
├── page.jsx          <- named export Board plus the React island mount/unmount pair
├── page.test.jsx     <- Vitest + RTL tests for Board
├── template.djx      <- full server render of the columns inside <div id="kanban-board">
├── layout.djx        <- board header + Board/Settings nav
├── settings/
│   └── template.djx  <- rename, add-column, and archive forms, a virtual route with no page.py
└── _pieces/
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
    └── preview/
        └── component.djx       <- "Move complete" SSR fallback
```

The outer HTML envelope is not in this tree. It lives in [`cockpit/layout.djx`](cockpit/layout.djx), the project-level page root listed in `PAGE_BACKENDS["DIRS"]`, while the routable pages come from the app through `APP_DIRS = True` and `PAGES_DIR = "boards"`.

`layout.djx` renders the Board and Settings tabs with the shared `nav_link` component and feeds it `url_kwargs=board_url_kwargs`, an inherited `@context` that returns `{"id": board.pk}`. The tabs therefore reverse `next:page_board_int_id` and `next:page_board_int_id_settings` by name, and the call site never spells out the `[int:id]` converter or a literal `/board/<id>/` path.

### 2. Two calls in `apps.py`

```python
class KanbanConfig(AppConfig):
    def ready(self) -> None:
        default_kinds.register(
            "jsx", extension=".jsx", slot="scripts", renderer="render_module_tag"
        )
        default_stems.register("template", "page")

        from kanban import providers, signals  # noqa: F401, PLC0415
```

`render_module_tag` is the framework's built-in renderer for ES module assets, so jsx files reuse it without a custom renderer. The `signals` import wires the dev-mode `collector_finalized` receiver. No other integration point in the framework is touched.

### 3. `ViteManifestBackend`

The custom backend resolves URLs only — rendering is delegated to the built-in `render_module_tag`:

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

`page.py` builds the board payload in one `prefetch_related` chain and flags it serialisable:

```python
@context("board", inherit_context=True, serialize=True)
def board_payload(active_board: DBoard[Board], request: HttpRequest) -> dict:
    cols = active_board.columns.prefetch_related(
        Prefetch("cards", queryset=Card.objects.order_by("position"))
    ).order_by("position")
    return {
        "id": active_board.pk,
        "csrf": get_token(request),
        "move_card_url": form_action_manager.get_action_url("move_card_form"),
        "create_card_url": form_action_manager.get_action_url("create_card_form"),
        "columns": [{..., "wip_limit": col.wip_limit, "cards": [
            {"id": c.id, "title": c.title, "position": c.position, "excerpt": c.excerpt}
            for c in col.cards.all()
        ]} for col in cols],
    }
```

`page.jsx` reads it at mount time, never owns URL constants or CSRF tokens, and re-uses the same payload to drive optimistic updates.

The React root follows the framework-island contract. `Next.partial.onMount("#kanban-board", ...)` mounts the board when the element appears, a `WeakMap` guard keeps repeated `onMount` passes from creating a second root on a live element, and a delegated `next:removed` listener unmounts the root when the mount point detaches, whether the event fires on the element itself or on an ancestor whose subtree contains it.

`DeepMergePolicy` is configured as the JS-context policy in [`config/settings.py`](config/settings.py), so the layout-level `active_boards_count` from `boards/page.py` and the page-level `board` payload merge into one `window.Next.context` object instead of the later writer replacing the earlier one. The same backend runs `HashContentDedup`, which keys assets by the sha256 of their content, so two hashed bundle filenames holding identical bytes collapse to one tag after a build.

### 6. The same board with and without JavaScript

`template.djx` renders the whole board server-side — `{% component "column" %}` per column, `{% component "card" %}` per card, and a `{% form "create_card_form" %}` block in every column footer. Without JavaScript the page stays a working board: columns, cards, and one "Add" button per column that posts, redirects, and re-renders. React then mounts into `#kanban-board` and replaces that markup with the same layout driven by `window.Next.context.board`, down to the "New card" input, so the swap is invisible.

### 7. Optimistic move and create with rollback

`Board` keeps `columns` in `useState`. A drop applies the move locally through a pure `applyMoveLocally(columns, cardId, targetColumnId, targetPosition)` helper before the `fetch` resolves, so the UI never waits on the network. On `!response.ok` or a thrown fetch the previous snapshot is restored and an inline error banner appears. The `?moved=<card_id>` query parameter still drives the SSR `preview` composite as a graceful fallback when JavaScript is unavailable.

Creating a card has the same shape plus one extra problem. The action answers with a redirect and no body, so the client holds no id for the row it just drew. `CreateCardForm.on_valid()` therefore redirects to `/board/<id>/?created=<card_id>`, mirroring the `?moved=` convention, and `fetch` follows the redirect so the id arrives in `response.url`. Until it does, the optimistic card lives under a client-side `pending-<n>` key, renders with `data-kanban-card-pending`, and is not draggable, because the server would not recognise that key. A confirmed id replaces the placeholder key in place. A post the server rejects, most often on a WIP limit, drops that one pending card and raises the error banner, so the client never keeps a card it cannot name. The create path removes the pending card instead of restoring a snapshot the way the move path does, so a drag that landed while the post was in flight survives the rollback.

### 8. WIP-limit invariant in two layers

`CreateCardForm.clean()` performs a best-effort count check so common posts surface a friendly error before the save runs. The authoritative check sits inside `CreateCardForm.on_valid()` under `Column.objects.select_for_update()` and returns `HttpResponseBadRequest` if a concurrent post fills the slot between form validation and insertion. All five form classes live in the app-level [`kanban/forms.py`](kanban/forms.py) picked up by autodiscover, because every one of them is shared across pages or components. The React layer reflects the limit live through the WIP badge that turns red when `cards.length` exceeds `wip_limit`, and a rejected create rolls its optimistic card back out.

### 9. Binding a form to a row, two ways

`RenameBoardForm` and `ArchiveBoardForm` are `ModelForm`s over the board the operator is already looking at, so they declare `Meta.instance_from_url = {"id": "pk"}`. The framework's `ModelForm.get_initial` turns that mapping into `get_object_or_404(Board, pk=url_kwargs["id"])`, so neither form carries a hidden id and neither handler runs a query. A key naming a field the model does not have is caught at `manage.py check` time as `next.E048`, and the same `Meta` key on a non-`ModelForm` as `next.E049`.

`CreateColumnForm` cannot use that route. It creates a row under the board rather than editing one, so there is no instance to load. It carries `board_id` as a hidden field instead and its `on_valid` takes `board: DBoard[Board]`, which `BoardProvider` resolves from `url_kwargs["id"]` when a page renders and from POST `board_id` when the dispatcher handles the action. `CardProvider` mirrors the pattern for `DCard[Card]` against POST `card_id`.

The two providers answer `static_can_handle` differently, and the difference is the point. `BoardProvider` settles a `DBoard[...]` parameter from the annotation alone, so it returns `True`, becomes the terminal of that parameter in the compiled injection plan, and implements `compile_resolve` to read the model out of the annotation once per plan instead of once per resolve. `CardProvider` also needs a POST `card_id` before it owns anything, so it returns `False` for a foreign annotation and `None` for `DCard[...]`, which keeps it a runtime candidate whose `can_handle` runs per request — the compiler never asks it for a filler. `BoardProvider` routes both `resolve` and its compiled filler through one module-level fetch helper, so the two paths cannot drift apart.

Modules that use these markers never start with `from __future__ import annotations` and import both the marker and the model at runtime. The resolver does evaluate string hints through `get_type_hints`, but a single name it cannot evaluate — a marker or a model imported only under `if TYPE_CHECKING` — drops the whole callable back to its raw annotations, where `get_origin` sees a string and the parameter silently falls through to another provider.

## Gotchas

### Sibling-form input is not preserved on a failed submit

The settings page hosts three independent `<form>` blocks. Each one posts to its own action URL, so a submit sends only that form's fields. If a user starts typing into "Add column", then submits "Rename board" and rename fails validation, the re-rendered page shows the rename errors but the "Add column" text is gone. The browser never transmitted the unsubmitted form's input, so the server has nothing to restore. This is inherent to the server-side post-and-rerender model and is best addressed client-side. Submitting a single form at a time is unaffected.

## Further reading

- [`kanban/apps.py`](kanban/apps.py) — `KanbanConfig.ready()` with the two registry calls.
- [`kanban/signals.py`](kanban/signals.py) — `inject_vite_dev_assets` receiver wired in `DEBUG` mode.
- [`kanban/backends.py`](kanban/backends.py) — `ViteManifestBackend` dev/prod URL routing.
- [`vite.config.ts`](vite.config.ts) — glob multi-entry build that discovers all co-located `.jsx` files.
- [`vitest.config.ts`](vitest.config.ts) — Vitest setup targeting `kanban/**/*.test.{jsx,tsx}`.
- [`next/static/assets.py`](../../next/static/assets.py) — the public `KindRegistry` API used by `apps.py`.
- [`next/static/defaults.py`](../../next/static/defaults.py) — the framework bootstrap that registers `css`, `js`, and `module` through the same call.
- [`next/static/collector.py`](../../next/static/collector.py) — `HashContentDedup`, `DeepMergePolicy`, and the slot-keyed buckets.
- [`next/static/signals.py`](../../next/static/signals.py) — `collector_finalized` signal fired after collection completes.
- [`next/static/manager.py`](../../next/static/manager.py) — placeholder-driven injection that dispatches per-asset renderers through `getattr` on the active backend.
- [`next/components/context.py`](../../next/components/context.py) — `@component.context` and the `serialize=True` flag.
- [`next/forms/manager.py`](../../next/forms/manager.py) — `form_action_manager.get_action_url(...)` used by the page to lift the move and create endpoint URLs into the React layer.
- [`next/deps/providers.py`](../../next/deps/providers.py) — `RegisteredParameterProvider` ABC used by `BoardProvider`/`CardProvider`.
- [`docs/content/ref/system-checks.rst`](../../docs/content/ref/system-checks.rst) — `next.E048` / `next.E049` for `Meta.instance_from_url` and `next.W069` for the asset-version guard.
