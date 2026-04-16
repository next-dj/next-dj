# Static Assets Example

Demonstrates co-located CSS and JS auto-collection and injection in next-dj, using **Bootstrap 5**, **Bootstrap Icons**, **Chart.js**, and two Google fonts as realistic third-party libraries.

## Where each asset is declared

| Scope | Mechanism | Loads |
| ----- | --------- | ----- |
| Layout (`pages/layout.djx`) | `{% use_style %}` / `{% use_script %}` template tags | Bootstrap 5 CSS + JS -- shared by every page |
| Home `page.py` (`styles`) | module-level list | Google Font *Inter* -- used by the home hero |
| Dashboard `page.py` (`styles`) | module-level list | Google Font *JetBrains Mono* -- used for code blocks |
| Widget `component.py` (`styles`) | module-level list | Bootstrap Icons -- used by the widget only |
| Chart `component.py` (`scripts`) | module-level list | Chart.js -- used by the chart only |
| `layout.css` / `layout.js` | co-located files | auto-picked up and served as `/_next/static/layout.css` etc. |
| `template.css` / `template.js` | co-located files | auto-picked up and served as `/_next/static/index.css`, `/_next/static/dashboard.css` etc. |
| `component.css` / `component.js` | co-located files | auto-picked up and served as `/_next/static/components/<name>.css`/`.js` |

## Features shown

- `{% collect_styles %}` / `{% collect_scripts %}` slots in `layout.djx` replaced with every collected `<link>` / `<script>` after rendering.
- `{% use_style "url" %}` / `{% use_script "url" %}` template tags for declaring external URLs directly from a layout or template.
- `styles` / `scripts` list variables in both `page.py` and `component.py` for per-page or per-component dependencies.
- Deduplication by URL: identical libraries declared by multiple layers appear only once.
- Depth-first ordering: layout assets -> page template assets -> page.py external URLs -> component assets (files first, then module variables) in render order.
- Co-located files are renamed by logical identity when served:
  - `pages/layout.css` -> `/_next/static/layout.css`
  - `pages/template.css` -> `/_next/static/index.css`
  - `pages/dashboard/template.css` -> `/_next/static/dashboard.css`
  - `pages/_components/widget/component.css` -> `/_next/static/components/widget.css`
  - `pages/dashboard/_components/chart/component.js` -> `/_next/static/components/chart.js`
- External URLs pass through as-is.

## Run

```bash
cd examples/static
python manage.py runserver
```

Then visit:

- `http://localhost:8000/` -- home page with the Bootstrap + Bootstrap Icons widget, rendered in Inter.
- `http://localhost:8000/dashboard/` -- dashboard subpage with the Chart.js bar chart, code blocks in JetBrains Mono.

Open the page source to see the injected `<link>` and `<script>` tags.
