# Static Assets Example

This example demonstrates next-dj's **static asset subsystem**: automatic discovery of co-located CSS/JS, module-level `styles`/`scripts` lists, the `{% use_style %}` / `{% use_script %}` template tags for shared layout dependencies, placeholder slots (`{% collect_styles %}` / `{% collect_scripts %}`), URL-based deduplication, and Django staticfiles URL resolution. Third-party libraries used as realistic fixtures: **Bootstrap 5**, **Bootstrap Icons**, **Chart.js**, **React 18 + ReactDOM + Babel standalone**, and two Google fonts (Inter, JetBrains Mono).

## Feature coverage (static)

| Technique | Where |
|-----------|-------|
| `{% use_style %}` / `{% use_script %}` in layout | `pages/layout.djx` (Bootstrap CSS + JS, shared by every page) |
| Module-level `styles` on a page | `pages/page.py` (Inter font), `pages/dashboard/page.py` (JetBrains Mono font) |
| Module-level `styles` on a component | `pages/_components/widget/component.py` (Bootstrap Icons) |
| Module-level `scripts` on a component | `pages/dashboard/_components/chart/component.py` (Chart.js) |
| `{% use_script %}` declared from inside a component | `pages/_components/counter/component.djx` (React + ReactDOM + Babel) |
| `{% #use_script %}` block form hoisting inline `<script>` | `pages/_components/counter/component.djx` (Babel mount block hoisted into scripts slot) |
| Co-located `layout.css` / `layout.js` | `pages/layout.css`, `pages/layout.js` |
| Co-located `template.css` / `template.js` | `pages/template.*`, `pages/dashboard/template.css` |
| Co-located `component.css` / `component.js` | `_components/widget/component.*`, `_components/chart/component.*`, `_components/counter/component.css` |
| Slots — `{% collect_styles %}` / `{% collect_scripts %}` | `pages/layout.djx` (head + before `</body>`) |
| Cascade order (use_* → layout → page → component) | Asserted in `tests/tests.py` |
| Deduplication by URL across repeated components | Counter rendered twice on the home page; React/ReactDOM/Babel emitted once |
| Staticfiles-backed URL resolution | URLs are emitted under `/static/next/...`; exercised in `tests/tests.py` |

## What This Example Demonstrates

- **Co-located assets.** Drop `layout.css` / `layout.js` next to `layout.djx`, `template.css` / `template.js` next to `template.djx`, and `component.css` / `component.js` next to `component.djx`. They are picked up automatically and resolved via Django staticfiles under `/static/next/...`.
- **Module-level URLs.** Declare `styles = [...]` and `scripts = [...]` in any `page.py` or `component.py` for per-page or per-component third-party assets.
- **Layout-wide dependencies via template tags.** `{% use_style "URL" %}` / `{% use_script "URL" %}` register shared libraries from the layout without hard-coding `<link>` / `<script>` tags; they land at the top of the collected list so child scopes can override them.
- **Slot-based injection.** `{% collect_styles %}` (in `<head>`) and `{% collect_scripts %}` (before `</body>`) are post-render placeholders — a single pass at the end of `Page.render` replaces both.
- **Cascade ordering.** `use_style`/`use_script` → layout files → template file → `page.py` module list → component files → `component.py` module list. Each level can override everything above it, mirroring the CSS cascade.
- **Deduplication by URL.** Repeating the same component twice on a page emits each CDN URL exactly once. Declaring the same library in both the layout and a component also collapses to a single tag.
- **Complex integrations.** The React counter composes Babel standalone + React 18 + ReactDOM, driven by a `<script type="text/babel">` block inside `component.djx` and `ReactDOM.createRoot(...).render(<Counter />)`. The three CDNs are declared via `{% use_script %}` directly in `component.djx`, and the inline Babel `<script>` is wrapped in `{% #use_script %}` … `{% /use_script %}` so it hoists into the `{% collect_scripts %}` slot at the end of `<body>` — the counter needs no `component.py` at all.

## Example Structure

```
static/
├── config/                         # Django project (settings, urls)
│   ├── __init__.py
│   ├── settings.py                 # NEXT_FRAMEWORK, DEFAULT_STATIC_BACKENDS
│   └── urls.py                     # include(next.urls)
├── manage.py
├── myapp/
│   ├── __init__.py
│   ├── apps.py
│   └── pages/
│       ├── layout.djx              # HTML shell, {% use_style %} Bootstrap, collect_* slots
│       ├── layout.css              # Co-located layout styling
│       ├── layout.js               # Co-located layout script
│       ├── page.py                 # Home: Inter font, page_title context
│       ├── template.djx            # Home body: hero + widget + two counter instances
│       ├── template.css            # Home co-located CSS
│       ├── template.js             # Home co-located JS
│       ├── _components/
│       │   ├── widget/
│       │   │   ├── component.djx   # Bootstrap card + collapse
│       │   │   ├── component.css
│       │   │   ├── component.js
│       │   │   └── component.py    # Bootstrap Icons module-level style
│       │   └── counter/
│       │       ├── component.djx   # {% use_script %} deps + {% #use_script %}-hoisted Babel mount
│       │       └── component.css   # Button styling (no component.py needed)
│       └── dashboard/
│           ├── page.py             # Dashboard: JetBrains Mono font
│           ├── template.djx        # Chart + copy on co-located CSS
│           ├── template.css
│           └── _components/
│               └── chart/
│                   ├── component.djx
│                   ├── component.css
│                   ├── component.js
│                   └── component.py # Chart.js module-level script
└── tests/
    ├── __init__.py
    ├── conftest.py                 # Client + home/dashboard HTML fixtures
    └── tests.py                    # End-to-end assertions on injected tags
```

## Main Pieces

**Layout** (`myapp/pages/layout.djx`): HTML shell with the Bootstrap CDN declared once via `{% use_style %}` / `{% use_script %}`, plus the two `{% collect_* %}` slots. Every page in the tree inherits these.

**Home page** (`myapp/pages/page.py` + `template.djx`): declares the Inter font in its `styles` list, renders the `widget` composite, and mounts two `counter` instances (labels "Likes" and "Stars") to demonstrate dedup.

**Dashboard page** (`myapp/pages/dashboard/page.py` + `template.djx`): declares the JetBrains Mono font and renders the `chart` composite with its Chart.js dependency.

**Widget** (`myapp/pages/_components/widget/`): Bootstrap card with a collapse button; `component.py` pulls in Bootstrap Icons. Demonstrates co-located `.css`/`.js` plus a module-level `styles` list on the same component.

**Chart** (`myapp/pages/dashboard/_components/chart/`): branch-scoped composite. `component.py` declares Chart.js via `scripts`, co-located `component.js` uses it to draw a bar chart.

**Counter** (`myapp/pages/_components/counter/`): React 18 + Babel standalone integration without a `component.py`. The top of `component.djx` registers React, ReactDOM and Babel via `{% use_script %}` tags — they prepend to the script cascade just like the layout's Bootstrap dependency. The body renders a mount `<div>`, then splits the Babel payload into **two** `{% #use_script %}` blocks: one with the shared `Counter` component definition (no context, so every render produces byte-identical HTML and content-dedup collapses it to one entry) and one with the `ReactDOM.createRoot(...)` mount line using `{{ id }}` (different bytes per render, so each instance gets its own boot line). The co-located `component.css` styles the resulting button. Rendered twice on the home page, the counter produces: three CDN `<script>` tags (URL-dedupped to one each), one shared `Counter` definition block, and two per-instance mount blocks — three Babel blocks total, not four.

## How It Works

1. **Render phase.** `Page.render` builds a per-request `StaticCollector` and calls `AssetDiscovery.discover_page_assets` to pre-register layout, template, and `page.py` assets. The Django template renders next — layout `{% use_style %}` / `{% use_script %}` tags prepend to the collector; every `{% component %}` tag calls `discover_component_assets`, appending co-located component files and `component.py` module lists; every `{% #use_style %}` / `{% #use_script %}` block renders its body with the live context and appends it as an inline asset.

2. **Inject phase.** The manager rewrites `<!-- next:styles -->` and `<!-- next:scripts -->` placeholders left by `{% collect_styles %}` / `{% collect_scripts %}` with concatenated `<link>` / `<script>` tags in cascade order.

3. **Cascade order.** On the home page:

   CSS

   1. Bootstrap CSS (`use_style` from layout)
   2. `/static/next/layout.css`
   3. `/static/next/index.css` (template)
   4. Google Font Inter (`pages/page.py` `styles`)
   5. `/static/next/components/widget.css`
   6. Bootstrap Icons (`widget/component.py` `styles`)
   7. `/static/next/components/counter.css`

   JS

   1. Bootstrap JS (`use_script` from layout, prepended)
   2. React, ReactDOM, Babel (`use_script` from `counter/component.djx`, prepended in registration order)
   3. `/static/next/layout.js`
   4. `/static/next/index.js`
   5. `/static/next/components/widget.js`
   6. Counter's shared `Counter` component definition block (hoisted by `{% #use_script %}`, content-dedupped to one entry)
   7. Counter's mount block for `id="likes"` (hoisted by `{% #use_script %}`)
   8. Counter's mount block for `id="stars"` (hoisted by `{% #use_script %}`)

   `use_script` URL declarations prepend (shared deps first); co-located `*.js` files append in render order; `{% #use_script %}` block bodies append last so inline boot code runs after every dependency and every co-located script has loaded.

4. **Deduplication.** The collector uses two strategies:

   - **URL-form assets** (co-located files, module lists, `use_style`/`use_script`) dedupe by URL, so rendering `counter` twice still emits React, ReactDOM and Babel exactly once.
   - **Inline block bodies** (`{% #use_script %}` / `{% #use_style %}`) dedupe by rendered body. The counter exploits this by splitting its Babel payload in two: the shared `Counter` component definition has no per-instance context → identical bodies across renders → collapsed to one entry; the mount block interpolates `{{ id }}` → different bodies per render (`counter-likes` vs `counter-stars`) → both kept. Split your blocks along the "shared vs per-instance" axis to get the best of both worlds.

5. **Staticfiles resolution.** `StaticFilesBackend` registers each discovered file under a logical name (`next/<route>.css`, `next/components/<name>.css`) and resolves final URLs through `staticfiles_storage.url(...)`.

## Running the Example

### Prerequisites

- Python 3.12+
- Django 5+
- next-dj installed (editable from the repo root is fine)

### Setup

```bash
cd examples/static
pip install django next-dj
```

There are no database migrations — the example is entirely rendering-driven.

### Running the Server

```bash
python manage.py runserver
```

### URLs to Try

| URL | Description |
|-----|-------------|
| `/` | Home page: Bootstrap widget + two React/Babel counters + Next-plugin demo |
| `/dashboard/` | Dashboard: Chart.js, unkeyed `@context`, `Depends("build_metadata")`, inherited theme via `Context("theme")` |
| `/inline/` | Inline CSS/JS demo via `{% #use_style %}` / `{% #use_script %}` blocks |
| `/docs/guide/intro/` | Three-level nested layout chain (root → `docs/` → `docs/guide/`) |
| `/broken/` | Page with a missing asset — demonstrates the manifest-miss error path |
| `/static/next/layout.css` | Example of a co-located file URL |
| `/static/next/components/counter.css` | Counter's component CSS |

Open the page source — you will see:

- Bootstrap `<link>` first in `<head>` (layout `use_style`).
- `/static/next/layout.css`, the template CSS, and page-level font URLs following in cascade order.
- Three `<script>` tags for React, ReactDOM and Babel, each appearing exactly **once** before the three `<script type="text/babel">` counter blocks (one shared `Counter` definition + two per-instance mounts), even though the counter component is rendered twice. The Babel blocks themselves live inside the `{% collect_scripts %}` slot (bottom of `<body>`) because they were wrapped in `{% #use_script %}` — not where the `<div>` mount point is drawn.

### Running collectstatic

The example ships the `NextStaticFilesFinder`, so Django's
`collectstatic` command picks up every co-located `layout.*`,
`template.*`, and `component.*` file and writes it under
`STATIC_ROOT/next/…` with the logical route name. A dry-run never
touches the filesystem and is safe to execute against a bare checkout:

```bash
cd examples/static
uv run python manage.py collectstatic --noinput --dry-run --ignore='*.py'
```

`--ignore='*.py'` keeps the page / component / layout modules out of
`STATIC_ROOT` — they live inside the app's `pages/` tree alongside the
assets. In production combine the finder with
`ManifestStaticFilesStorage` to get content-hashed URLs; the default
`StaticFilesBackend` resolves those URLs transparently via
`staticfiles_storage.url(...)`. When an asset is referenced but missing
from the manifest the backend raises a descriptive `RuntimeError` —
see `pages/broken/page.py` and the matching test in `tests/tests.py`.

### Running Tests

From the repository root (use `--no-cov` to skip the core project's coverage gate when running only this example):

```bash
uv run pytest examples/static/tests/ -v --no-cov
```

From `examples/static/`:

```bash
cd examples/static
uv run pytest tests/ -v --no-cov
```

The test suite renders the home and dashboard pages with Django's `Client`, verifies cascade order, counter mount points, dedup of the React CDNs, and staticfiles-backed URL resolution.

## JavaScript Context with the Next Object

next-dj automatically injects a global `Next` object on every page — no configuration needed. Mark specific context functions with `serialize=True` to expose their return values to JavaScript via `window.Next.context`.

### How it works

1. `{% collect_scripts %}` always emits `next.min.js` first, then an inline `Next._init({...})` call with all serialized values.
2. `<link rel="preload" as="script">` for `next.min.js` is inserted automatically before `</head>` to prime the browser cache.
3. `window.Next.context` is frozen — reads are safe, writes are silently ignored.

### Page context (`page.py`)

```python
from next.pages import context

@context("page_meta", serialize=True)
def get_page_meta() -> dict:
    return {"page": "home", "version": "0.4"}
```

### Component context (`component.py`)

```python
from next.components import context

@context("theme", serialize=True)
def get_theme() -> str:
    return "dark"
```

### Vanilla JS access

```js
const ctx = window.Next.context;
// ctx.page_meta.page  → "home"
// ctx.theme           → "dark"
```

### React access

```jsx
function PageBadge() {
    const ctx = window.Next.context;
    return React.createElement("span", null, ctx.page_meta?.page || "");
}
ReactDOM.createRoot(document.getElementById("badge")).render(
    React.createElement(PageBadge)
);
```

`window.Next.context` is available synchronously when your scripts run — `next.min.js` is a blocking script tag and `Next._init` follows it immediately.

### TypeScript declaration

```typescript
declare const Next: { context: Readonly<Record<string, unknown>> };
```

### Key conflict resolution

When the same key is registered by both a page context and a component context, the **page value wins** (first-registration semantics). This mirrors CSS specificity: the outer scope (page) overrides the inner (component).

| Source | Priority |
|--------|----------|
| Page `@context(serialize=True)` | Higher — registered first |
| Component `@context(serialize=True)` | Lower — registered later |

### Demo component

The `next-demo` component in this example (`pages/_components/next-demo/`) reads `window.Next.context` in a plain `<script>` block and writes the values into a `<dl>` on the page. Open the home page source to see `Next._init({"page_meta": {"page": "home", ...}, "theme": "dark"})` in the scripts slot.

## Contributing

Issues and PRs welcome via the main next-dj repository. Keep backward compatibility when changing examples.
