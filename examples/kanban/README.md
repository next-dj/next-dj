# Kanban

A drag-and-drop Kanban board powered by composite React components, the
public `KindRegistry` extension point, and content-hash CSS deduplication.
The example shows that adding a brand-new asset type, in this case
`.jsx`, does not require any patches to next-dj. A single custom static
backend plus one bootstrap call teaches discovery, the finder, the
collector, and the manager about the new file extension. Server rendering
keeps the page useful with JavaScript turned off, and the React layer
upgrades it with native HTML5 drag-and-drop on the client.

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

- **Custom `StaticBackend` plus `KindRegistry` extension.**
  [`kanban/backends.py`](kanban/backends.py) defines a
  `BabelStaticBackend(StaticFilesBackend)` that adds one new method,
  `render_babel_script_tag`, and [`kanban/apps.py`](kanban/apps.py)
  registers the `jsx` kind via the public `default_kinds.register`
  call. The framework picks up `component.jsx` files, serves them
  through Django staticfiles, and emits
  `<script type="text/babel" src="...">` tags. Look at
  [`next/static/assets.py`](../../next/static/assets.py) and
  [`next/static/discovery.py`](../../next/static/discovery.py) to
  see how no special-casing for `js` or `css` lives in core anymore.
- **Multi-level `@context(serialize=True)` plus `DeepMergePolicy`.**
  [`kanban/boards/board/[int:id]/page.py`](kanban/boards/board/[int:id]/page.py)
  attaches board identity (`id`, `title`, `csrf`, `move_card_url`) to
  the merged `board` context. The column composite at
  [`kanban/boards/board/[int:id]/_pieces/column/component.py`](kanban/boards/board/[int:id]/_pieces/column/component.py)
  attaches `columns` to the same `board` key. With
  [`DeepMergePolicy`](../../next/static/collector.py) wired through
  [`kanban/static_policies.py`](kanban/static_policies.py), the two
  partial structures merge into one `window.Next.context.board` payload.
- **`HashContentDedup` on co-located CSS.**
  `column/component.css` and `card/component.css` ship byte-for-byte
  identical content. Without dedup the page would carry two `<link>`
  tags. With
  [`HashContentDedup`](../../next/static/collector.py) the hash matches
  and the collector keeps a single asset, whichever was registered
  first.
- **Form actions with composite preview.**
  The move handler in [`kanban/actions.py`](kanban/actions.py) returns
  to the board page with `?moved=<card_id>`. The page picks the moved
  card up via [`Card`](kanban/models.py) DI and renders the
  template-only `preview` composite to surface a confirmation banner.
- **`inherit_context=True` across the layout chain.**
  The `board_object`, `columns`, `moved_card`, and `board` context
  callables on the board page are marked `inherit_context=True`, so the
  settings page reuses the same board metadata without re-resolving it.
- **WIP-limit validation as a DI demo.**
  [`CreateCardForm.clean`](kanban/forms.py) reads the target column,
  counts its current cards, and raises `ValidationError` when the
  column reaches its WIP limit. The `Column` is resolved from the
  `column_id` form field, exercising the form-clean path.
- **CSRF-aware fetch from JSX.**
  [`card/component.jsx`](kanban/boards/board/[int:id]/_pieces/card/component.jsx)
  reads `csrf` and `move_card_url` from `window.Next.context.board`,
  attaches the token to the POST request, and reloads on success. The
  React side stays small and never owns its own URL constants.

## How to run

```bash
cd examples/kanban
uv run python manage.py migrate
uv run python manage.py runserver
uv run pytest
```

The first run seeds three demo boards. Open `http://localhost:8000/` to
land on the board list. Drag a card from one column to another to see
the React layer fire and the page reload with a `?moved=` confirmation
banner rendered by the `preview` composite.

## Key ideas

### 1. The custom backend is a six-line class

`BabelStaticBackend` is a subclass of `StaticFilesBackend` that exposes
one extra renderer method. The framework picks the renderer per asset
through `KindRegistry.renderer(kind)`, so naming the method to match
the registration is the only contract.

```python
class BabelStaticBackend(StaticFilesBackend):
    def render_babel_script_tag(self, url, *, request=None):
        return f'<script type="text/babel" src="{url}"></script>'
```

The matching registration in `apps.py` uses the public API exclusively.

```python
default_kinds.register(
    "jsx",
    extension=".jsx",
    slot="scripts",
    renderer="render_babel_script_tag",
)
```

After that, `component.jsx` files are picked up by discovery, mapped to
URLs by the finder, deduped by `HashContentDedup`, and rendered through
the custom method.

### 2. DeepMergePolicy unifies layered context

`window.Next.context.board` ends up with `id`, `title`, `slug`,
`archived`, `csrf`, `move_card_url`, and `columns`. Page-level context
contributes the first six keys. Component-level context contributes
`columns`. With the default `FirstWinsPolicy`, only the page-level keys
would survive. The `TestJsContext.test_first_wins_policy_loses_data`
test asserts exactly this property.

### 3. Two CSS files, one link tag

`column/component.css` and `card/component.css` are identical bytes.
The collector hashes file contents through `HashContentDedup`, so only
one asset registration survives. The `TestCssDedup` tests grep the
final HTML for kanban-namespaced `<link>` tags and assert exactly one
match.

### 4. Native drag-and-drop, no extra dependency

`column.jsx` decorates server-rendered columns with `dragover` and
`dragleave` styling. `card.jsx` reads the move endpoint URL from
`window.Next.context.board.move_card_url`, attaches the CSRF token,
and POSTs the form-encoded payload. No third-party drag library is
involved. The browser receives a normal HTML page that already shows
the board, then babel-standalone compiles the JSX in place to add the
client behaviour.

### 5. WIP limits gate creation through DI-resolved models

`CreateCardForm.clean` looks up the target column from `column_id`,
checks the count of existing cards against `wip_limit`, and rejects
the submission with a `ValidationError`. The action handler does not
need to repeat the check.

### 6. The preview composite is template-only

`preview/component.djx` has no `component.py`. It reads `moved_card`
straight from the parent context produced by the page. Composite
components are not required to ship Python code, so the framework
keeps them as cheap as their server-rendered template.

## Tests

`tests/test_e2e.py` covers ten test classes.

| Class | What it checks |
|-------|----------------|
| `TestBoardList` | Active boards render. Archived boards are hidden. The empty state copy appears when no boards exist. |
| `TestBoardView` | Columns, cards, and the React CDN script tags all render. The nested layout chain composes. The settings page sees the inherited board context. |
| `TestSettings` | Rename, archive, and add-column forms render and mutate the database when posted. |
| `TestMoveCard` | Cards move between columns. Positions renumber to a contiguous range. Cross-board moves and negative positions are rejected. |
| `TestCardExcerpt` | Long card bodies render with an ellipsis. |
| `TestPreviewComponent` | The preview composite renders only when the URL carries `?moved=` and skips unknown card ids. |
| `TestCreateCard` | New cards land at the tail of the target column. WIP limits block creation. Columns with no limit accept unlimited cards. |
| `TestCssDedup` | Identical co-located CSS files collapse to one `<link>` tag. |
| `TestJsxBackend` | Both `column.jsx` and `card.jsx` are emitted as `<script type="text/babel" src="...">` tags. The CSS link rendering path is unaffected. |
| `TestJsContext` | `Next._init({...})` carries the merged board state, including columns and cards. Switching to `FirstWinsPolicy` loses one of the two layers, justifying `DeepMergePolicy`. |
| `TestReactCdn` | React, react-dom, and babel-standalone CDN scripts render in the right order before any `text/babel` block. |

`tests/test_unit.py` covers model `__str__` methods, the
`HashContentDedup` and `DeepMergePolicy` re-exports in
`static_policies`, the `BabelStaticBackend` renderers, the
`BoardProvider` and `CardProvider` resolvers in isolation, and the
`MoveCardForm` and `CreateCardForm` clean-method branches.

Coverage is enforced at 100 percent for the example. Run

```bash
uv run pytest --cov=. --cov-config=../.coveragerc --cov-fail-under=100
```

to verify.

## Further reading

- [`next/static/assets.py`](../../next/static/assets.py) — the public
  `KindRegistry` API used by `apps.py`.
- [`next/static/defaults.py`](../../next/static/defaults.py) — the
  framework bootstrap that registers `css` and `js` through the same
  call user code uses.
- [`next/static/collector.py`](../../next/static/collector.py) —
  `HashContentDedup`, `DeepMergePolicy`, and the slot-keyed buckets.
- [`next/static/manager.py`](../../next/static/manager.py) —
  placeholder-driven injection that dispatches per-asset renderers
  through `getattr` on the active backend.
- [`next/components/context.py`](../../next/components/context.py) —
  `@component.context` and the `serialize=True` flag.
- [`next/forms/manager.py`](../../next/forms/manager.py) —
  `form_action_manager.get_action_url(...)` used by the page to lift
  the move endpoint URL into the React layer.
