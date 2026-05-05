# Kanban

A drag-and-drop Kanban board powered by co-located React components and a
Vite build pipeline. The example demonstrates that adding a brand-new asset
type (`.jsx`) to next.dj requires no changes to the framework core. A single
custom `StaticFilesBackend` subclass (`ViteManifestBackend`), two registry
calls in `AppConfig.ready()`, and one signal connection is all the
integration layer needs. Server rendering keeps every page useful without
JavaScript, and the React layer adds native HTML5 drag-and-drop on top.

## What you will see

| URL | Description |
|-----|-------------|
| `/` | List of active boards. Archived boards are hidden. |
| `/board/<id>/` | Server-rendered columns and cards plus React-mounted drag-and-drop. |
| `/board/<id>/settings/` | Forms to rename a board, archive it, or add a column with a WIP limit. |
| `POST kanban:move_card` | Move a card across columns. The handler renumbers card positions inside an atomic transaction. |
| `POST kanban:create_card` | Create a card at the tail of a column. Rejected when the column is at its WIP limit. |
| `POST kanban:create_column` | Append a column to the board with an optional `wip_limit`. |
| `POST kanban:rename_board` | Update the board title. |
| `POST kanban:archive_board` | Toggle the archived flag. Archived boards drop out of the index. |

Three demo boards seed via a data migration. Two are active
(`engineering-roadmap` and `marketing-launch`) and one is archived
(`old-experiments`). The active boards together carry four columns and
seven cards.

## Framework features showcased

- **Co-located JSX (ViewComponent-style): `StemRegistry` + `KindRegistry`.**
  `page.jsx` lives alongside `page.py`. `component.jsx` lives alongside
  `component.py` and `component.djx`. Two registry calls in
  `KanbanConfig.ready()` teach discovery to find those files and route them
  through the custom renderer without any patch to next.dj core.

- **`ViteManifestBackend`: dev URL vs. hashed prod URL.**
  When `DEV_ORIGIN` is set, `register_file` returns a Vite dev-server URL
  so HMR works. Without it, the backend reads `dist/.vite/manifest.json` to
  resolve hashed file names for production. The renderer always emits
  `<script type="module" src="...">`.

- **`collector_finalized` signal: `@vite/client` injected without template edits.**
  In `DEBUG` mode `KanbanConfig.ready()` connects `_inject_vite_client` to
  the `collector_finalized` signal. That function prepends the Vite HMR
  client script to the collected asset list so no template needs a
  hardcoded `<script>` tag.

- **`DeepMergePolicy`: layered JS context from two Python files.**
  `page.py` and `column/component.py` both write to the `board` key under
  `window.Next.context`. With `DeepMergePolicy` wired through
  `kanban/static_policies.py`, the two partial dicts merge into one
  `window.Next.context.board` payload.

- **Pure React components and co-located tests (Vitest + RTL).**
  `card/component.jsx` and `column/component.jsx` are pure named exports with
  no side-effects. Each ships a co-located `*.test.jsx` file exercised by
  Vitest + React Testing Library. `page.jsx` exports `Board` and also has
  a co-located `page.test.jsx`.

- **Form actions with composite preview.**
  The move handler in `kanban/actions.py` redirects to the board page with
  `?moved=<card_id>`. The page picks the moved card up via `Card` DI and
  renders the template-only `preview` composite to show a confirmation banner.

- **`inherit_context=True` across the layout chain.**
  `board_object`, `columns`, `moved_card`, and `board_payload` on the board
  page are all marked `inherit_context=True`, so the settings page reuses
  the same board metadata without re-resolving it.

- **WIP-limit validation via DI.**
  `CreateCardForm.clean` reads the target column, counts its current cards,
  and raises `ValidationError` when the column is at its WIP limit. The
  `Column` is resolved from the `column_id` form field, exercising the
  form-clean path.

## How to run

### Django

```bash
cd examples/kanban
uv run python manage.py migrate
uv run python manage.py runserver
```

The first `migrate` run seeds three demo boards. Open `http://localhost:8000/`
to land on the board list.

### Vite dev server

Install JS dependencies from the repo root, then from the example directory:

```bash
npm install            # root devDependencies (if any)
cd examples/kanban
npm install
npm run dev           # starts Vite at http://localhost:5173
```

With the dev server running, set `DEV_ORIGIN = "http://localhost:5173"` in
`config/settings.py` (or your local override). Django will resolve every
`.jsx` asset to the Vite dev server URL and the `@vite/client` HMR script
will be injected automatically via the `collector_finalized` signal.

### Production build

```bash
cd examples/kanban
npm run build         # writes hashed files to kanban/static/kanban/dist/
uv run python manage.py runserver
```

Set `MANIFEST_PATH` in `STATIC_BACKEND["OPTIONS"]` to the path of
`dist/.vite/manifest.json`. The backend will read hashed file names from
the manifest and delegate URL resolution to Django staticfiles.

### Tests

```bash
# Python
uv run pytest

# JavaScript
npm test
```

Coverage is enforced at 100 percent for the Python side:

```bash
uv run pytest --cov=. --cov-config=../.coveragerc --cov-fail-under=100
```

## Key ideas

### 1. Co-location structure

Every route and component owns its asset files in the same directory:

```
boards/board/[int:id]/
├── page.py           <- @context callables, serialize=True
├── page.jsx          <- named export Board, mounts ReactDOM
├── page.test.jsx     <- Vitest + RTL tests for Board
├── template.djx      <- server skeleton with <div id="kanban-board">
├── layout.djx        <- board header + Board/Settings nav
└── _pieces/
    ├── card/
    │   ├── component.py
    │   ├── component.djx
    │   ├── component.jsx   <- export function Card(...)
    │   ├── component.css
    │   └── component.test.jsx
    └── column/
        ├── component.py
        ├── component.djx
        ├── component.jsx   <- export function Column(...)
        ├── component.css
        └── component.test.jsx
```

### 2. Two calls in `apps.py`

```python
class KanbanConfig(AppConfig):
    def ready(self) -> None:
        from next.static import default_kinds
        from next.static.discovery import default_stems

        default_kinds.register(
            "jsx",
            extension=".jsx",
            slot="scripts",
            renderer="render_babel_script_tag",
        )

        # Teach discovery to pick up page.jsx alongside page.py.
        default_stems.register("template", "page")

        if settings.DEBUG:
            from next.static.signals import collector_finalized
            collector_finalized.connect(_inject_vite_client)
```

No other integration point in the framework is touched.

### 3. `ViteManifestBackend`

```python
class ViteManifestBackend(StaticFilesBackend):
    def register_file(self, source_path, logical_name, kind):
        if kind != "jsx":
            return super().register_file(source_path, logical_name, kind)

        if self._dev_origin:
            return self._build_dev_url(source_path)   # -> Vite dev URL

        if self._manifest_path:
            return self._resolve_from_manifest(source_path, logical_name)  # -> hashed URL

        return super().register_file(source_path, logical_name, kind)

    def render_babel_script_tag(self, url, *, request=None):
        target = self._dev_url_map.get(url, url)
        return f'<script type="module" src="{target}"></script>'
```

The renderer name `render_babel_script_tag` matches the string passed to
`default_kinds.register`. The framework dispatches to it via `getattr` on
the active backend, so naming is the only contract.

### 4. `@vite/client` via signal

```python
def _inject_vite_client(sender, *, request, **kwargs):
    from next.static.assets import StaticAsset

    origin = "http://localhost:5173"
    sender.add(
        StaticAsset(
            url="",
            kind="js",
            inline=f'<script type="module" src="{origin}/@vite/client"></script>',
        ),
        prepend=True,
    )
```

`sender` is the collector instance. Prepending means the HMR client loads
before any application module script. No template change is needed.

### 5. React receives data from `window.Next.context.board`

`page.py` builds the complete board payload and marks it serializable:

```python
@context("board", inherit_context=True, serialize=True)
def board_payload(active_board: DBoard[Board], request: HttpRequest) -> dict:
    return {
        "id": board_with_cards.pk,
        "title": board_with_cards.title,
        "csrf": get_token(request),
        "move_card_url": form_action_manager.get_action_url("kanban:move_card"),
        "columns": [...],
    }
```

`page.jsx` reads it at mount time:

```jsx
export function Board() {
  const ctx = window.Next?.context?.board ?? {};
  const [columns, setColumns] = useState(ctx.columns ?? []);
  // ...
}

const el = document.getElementById("kanban-board");
if (el) ReactDOM.createRoot(el).render(<Board />);
```

The React component never owns URL constants or CSRF tokens. It reads
everything from the server-provided context object.

### 6. Pure components and co-located tests

`card/component.jsx` is a named export with no side-effects:

```jsx
export function Card({ id, title }) {
  const [dragging, setDragging] = useState(false);
  return (
    <div
      data-kanban-card={id}
      draggable
      onDragStart={(e) => {
        setDragging(true);
        e.dataTransfer.setData("text/card-id", String(id));
      }}
      onDragEnd={() => setDragging(false)}
      className={`kanban-card ...${dragging ? " opacity-50" : ""}`}
    >
      {title}
    </div>
  );
}
```

Its test file lives next to it and runs with Vitest + RTL:

```jsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Card } from "./component";

describe("Card", () => {
  it("renders the card title", () => {
    render(<Card id={1} title="Fix login bug" />);
    expect(screen.getByText("Fix login bug")).toBeInTheDocument();
  });

  it("puts card id into dataTransfer on dragstart", () => {
    render(<Card id={42} title="Fix login bug" />);
    const el = screen.getByText("Fix login bug");
    const setData = vi.fn();
    fireEvent.dragStart(el, { dataTransfer: { setData } });
    expect(setData).toHaveBeenCalledWith("text/card-id", "42");
  });
});
```

### 7. Template as server-rendered skeleton

`template.djx` renders column placeholders that are visible before React
hydrates, keeping the page usable without JavaScript:

```djx
<div class="space-y-6">
  {% if moved_card %}
    {% component "preview" %}
  {% endif %}
  <div id="kanban-board" class="flex gap-4 overflow-x-auto pb-4">
    {# Server-rendered column placeholders shown while React initialises #}
    {% for column in columns %}
      <div class="kanban-column-placeholder w-64 rounded-lg border ...">
        <h3 class="text-sm font-semibold text-slate-700">{{ column.title }}</h3>
      </div>
    {% endfor %}
  </div>
</div>
```

React mounts into `#kanban-board` and replaces the placeholders with
interactive column and card components.

## Tests

### Python (pytest)

| Class | What it checks |
|-------|----------------|
| `TestBoardList` | Active boards render. Archived boards are hidden. The empty state copy appears when no boards exist. |
| `TestBoardView` | Columns and cards render. `id="kanban-board"` is present. `type="module"` scripts are present. The nested layout chain composes. |
| `TestSettings` | Rename, archive, and add-column forms render and mutate the database when posted. |
| `TestMoveCard` | Cards move between columns. Positions renumber to a contiguous range. Cross-board moves and negative positions are rejected. |
| `TestPreviewComponent` | The preview composite renders only when the URL carries `?moved=` and skips unknown card ids. |
| `TestCreateCard` | New cards land at the tail of the target column. WIP limits block creation. Columns with no limit accept unlimited cards. |
| `TestJsxBackend` | `page.jsx` is emitted as `<script type="module" src="...">`. No `type="text/babel"` tags are present. |
| `TestJsContext` | `Next._init({...})` carries the merged board state including columns and cards with full card trees. |
| `TestInheritedHeaderCount` | `active_boards_count` from `boards/page.py` flows into board detail and settings pages via `inherit_context`. |
| `TestCdnCachePolicy` | Local script tags do not carry CDN cache-control attributes. |

`tests/test_unit.py` covers model `__str__` methods, the `HashContentDedup`
and `DeepMergePolicy` re-exports in `static_policies`, the
`ViteManifestBackend` renderers (module script tag, dev URL map override,
unaffected CSS and JS renderers), the `BoardProvider` and `CardProvider`
resolvers in isolation, and the `MoveCardForm` and `CreateCardForm`
clean-method branches.

### JavaScript (Vitest)

| File | What it checks |
|------|----------------|
| `_pieces/card/component.test.jsx` | `Card` renders title, is draggable, sets `data-kanban-card`, puts card id in `dataTransfer`, toggles opacity class on drag start and drag end. |
| `_pieces/column/component.test.jsx` | `Column` renders title and all cards, sets `data-kanban-column`, calls `onDrop` with correct arguments, adds and removes `drop-active` class on drag events. |
| `page.test.jsx` | `Board` renders all columns from `window.Next.context.board`, posts to `move_card_url` with correct payload and CSRF token on drop, renders empty board gracefully when context is missing. |

## Further reading

- [`kanban/apps.py`](kanban/apps.py) — `KanbanConfig.ready()` with the two registry calls and the signal connection.
- [`kanban/backends.py`](kanban/backends.py) — `ViteManifestBackend` dev/prod routing and `render_babel_script_tag`.
- [`vite.config.ts`](vite.config.ts) — glob multi-entry build that discovers all co-located `.jsx` files.
- [`vitest.config.ts`](vitest.config.ts) — Vitest setup targeting `kanban/**/*.test.{jsx,tsx}`.
- [`next/static/assets.py`](../../next/static/assets.py) — the public `KindRegistry` API used by `apps.py`.
- [`next/static/defaults.py`](../../next/static/defaults.py) — the framework bootstrap that registers `css` and `js` through the same call.
- [`next/static/collector.py`](../../next/static/collector.py) — `HashContentDedup`, `DeepMergePolicy`, and the slot-keyed buckets.
- [`next/static/signals.py`](../../next/static/signals.py) — `collector_finalized` signal fired after collection completes.
- [`next/static/manager.py`](../../next/static/manager.py) — placeholder-driven injection that dispatches per-asset renderers through `getattr` on the active backend.
- [`next/components/context.py`](../../next/components/context.py) — `@component.context` and the `serialize=True` flag.
- [`next/forms/manager.py`](../../next/forms/manager.py) — `form_action_manager.get_action_url(...)` used by the page to lift the move endpoint URL into the React layer.
