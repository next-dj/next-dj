# Kanban

A drag-and-drop Kanban board powered by co-located React components and
a Vite build pipeline. The example demonstrates that adding a brand-new
asset type (`.jsx`) to next.dj requires no changes to the framework
core. A single custom `StaticFilesBackend` subclass for URL resolution,
two registry calls in `AppConfig.ready()`, and one signal receiver are
the entire integration layer. Rendering uses the framework built-in
`render_module_tag`, so the example registers no custom renderers.
Server rendering keeps every page useful without JavaScript, and the
React layer adds native HTML5 drag-and-drop with optimistic updates on
top.

## What you will see

| URL | Description |
|-----|-------------|
| `/` | List of active boards. Archived boards are hidden. |
| `/board/<id>/` | Server-rendered columns and cards plus React-mounted drag-and-drop with optimistic move. |
| `/board/<id>/settings/` | Forms to rename a board, archive it, or add a column with a WIP limit. |
| `POST kanban:move_card` | Move a card across columns. Renumbers card positions inside an atomic transaction. |
| `POST kanban:create_card` | Create a card at the tail of a column under `select_for_update`. Rejected when the column is at its WIP limit. |
| `POST kanban:create_column` | Append a column to the board with an optional `wip_limit`. |
| `POST kanban:rename_board` | Update the board title. |
| `POST kanban:archive_board` | Toggle the archived flag. Archived boards drop out of the index. |

Three demo boards seed via a data migration. Two are active
(`engineering-roadmap` and `marketing-launch`) and one is archived
(`old-experiments`).

## How to run

```bash
cd examples/kanban
uv run python manage.py migrate
uv run python manage.py runserver     # http://127.0.0.1:8000/
```

The first `migrate` run seeds three demo boards. Open the index to land
on the board list.

For the React layer to hot-reload, start the Vite dev server:

```bash
npm install
npm run dev                            # http://localhost:5173
```

With the dev server running, Django resolves every `.jsx` asset to the
Vite dev-server URL and the React Refresh preamble plus the
`@vite/client` HMR script load through the `collector_finalized` signal.
Override the Vite origin with the `VITE_ORIGIN` environment variable.

For a production-shaped build:

```bash
npm run build                          # writes hashed files into kanban/static/kanban/dist/
uv run python manage.py runserver
```

The backend reads `dist/.vite/manifest.json` and delegates URL
resolution to Django staticfiles. If the manifest file is missing, the
backend logs a single warning and falls back to staticfiles so dev
workflows stay unblocked.

## Walking the code

### 1. Co-location structure

Every route and component owns its asset files in the same directory:

```
boards/board/[int:id]/
├── page.py           <- @context callables and @action handlers (move_card, create_card)
├── page.jsx          <- named export Board, mounts ReactDOM
├── page.test.jsx     <- Vitest + RTL tests for Board
├── template.djx      <- server skeleton with <div id="kanban-board">
├── layout.djx        <- board header + Board/Settings nav
├── settings/
│   ├── page.py       <- settings @context + create_column / rename_board / archive_board
│   └── template.djx
└── _pieces/
    ├── card/
    │   ├── component.py
    │   ├── component.djx
    │   ├── component.jsx       <- export function Card({ id, title, excerpt })
    │   ├── component.css
    │   └── component.test.jsx
    ├── column/
    │   ├── component.py
    │   ├── component.djx
    │   ├── component.jsx       <- export function Column({ column, onDrop })
    │   ├── component.css
    │   └── component.test.jsx
    └── preview/
        └── component.djx       <- "Move complete" SSR fallback
```

### 2. Two calls in `apps.py`

```python
class KanbanConfig(AppConfig):
    def ready(self) -> None:
        default_kinds.register(
            "jsx",
            extension=".jsx",
            slot="scripts",
            renderer="render_module_tag",
        )
        default_stems.register("template", "page")

        from kanban import providers, signals  # noqa: F401
```

`render_module_tag` is the framework's built-in renderer for ES module
assets, so jsx files reuse it without a custom renderer. The `signals`
import wires the dev-mode `collector_finalized` receiver. No other
integration point in the framework is touched.

### 3. `ViteManifestBackend`

The custom backend resolves URLs only — rendering is delegated to the
built-in `render_module_tag`:

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

Three resolution modes: a Vite dev-server URL when `DEV_ORIGIN` is set,
a hashed manifest entry in production, or unmodified Django staticfiles
when neither applies. Missing manifest emits a single warning and falls
back to staticfiles.

### 4. `@vite/client` via guarded signal

```python
def inject_vite_dev_assets(sender, **kwargs):
    if not any(a.kind == "jsx" for a in sender.assets_in_slot("scripts")):
        return
    origin = os.environ.get("VITE_ORIGIN", "http://localhost:5173")
    sender.add(StaticAsset(url="", kind="js", inline=preamble), prepend=True)
    sender.add(
        StaticAsset(url="", kind="js", inline=f'<script type="module" src="{origin}/@vite/client"></script>'),
        prepend=True,
    )
```

`sender` is the collector instance. The `assets_in_slot` guard keeps
dev plumbing off pages with no jsx scripts, including the index page.

### 5. React receives data from `window.Next.context.board`

`page.py` builds the board payload in one `prefetch_related` chain and
flags it serialisable:

```python
@context("board", inherit_context=True, serialize=True)
def board_payload(active_board: DBoard[Board], request: HttpRequest) -> dict:
    cols = active_board.columns.prefetch_related(
        Prefetch("cards", queryset=Card.objects.order_by("position"))
    ).order_by("position")
    return {
        "id": active_board.pk,
        "csrf": get_token(request),
        "move_card_url": form_action_manager.get_action_url("kanban:move_card"),
        "columns": [{..., "wip_limit": col.wip_limit, "cards": [
            {"id": c.id, "title": c.title, "position": c.position, "excerpt": c.excerpt}
            for c in col.cards.all()
        ]} for col in cols],
    }
```

`page.jsx` reads it at mount time, never owns URL constants or CSRF
tokens, and re-uses the same payload to drive optimistic updates.

`DeepMergePolicy` is configured as the JS-context policy in
`config/settings.py`, so the layout-level `active_boards_count` from
`boards/page.py` and the page-level `board` payload merge into one
`window.Next.context` object.

### 6. Optimistic move with rollback

`Board` keeps `columns` in `useState`. A drop applies the move locally
through a pure `applyMoveLocally(columns, cardId, targetColumnId,
targetPosition)` helper before the `fetch` resolves, so the UI never
waits on the network. On `!response.ok` or a thrown fetch the previous
snapshot is restored and an inline error banner appears. The
`?moved=<card_id>` query parameter still drives the SSR `preview`
composite as a graceful fallback when JavaScript is unavailable.

### 7. WIP-limit invariant in two layers

`CreateCardForm.clean()` performs a best-effort count check so common
posts surface a friendly error before the handler runs. The
authoritative check sits inside the action handler under
`Column.objects.select_for_update()` and returns
`HttpResponseBadRequest` if a concurrent post fills the slot between
form validation and insertion. The React layer reflects the limit live
through the WIP badge that turns red when `cards.length` exceeds
`wip_limit`.

### 8. DI provider used in URL routes and form actions

`BoardProvider` resolves `DBoard[Board]` from `url_kwargs["id"]` for
page rendering and from POST `board_id` for action handlers, so
settings handlers receive a board through DI without
`Board.objects.get(...)`. `CardProvider` mirrors the pattern for
`DCard[Card]` against POST `card_id`.

Page modules that use these markers do not start with
`from __future__ import annotations`, because the DI resolver compares
parameter annotations by identity.

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
- [`next/forms/manager.py`](../../next/forms/manager.py) — `form_action_manager.get_action_url(...)` used by the page to lift the move endpoint URL into the React layer.
- [`next/deps/providers.py`](../../next/deps/providers.py) — `RegisteredParameterProvider` ABC used by `BoardProvider`/`CardProvider`.
