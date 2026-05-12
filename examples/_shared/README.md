# `examples/_shared` — UI kit for next.dj examples

A shadcn-inspired component palette that every next.dj example pulls in through the regular component subsystem. **Not** part of the `next-dj` package — this is a worked example of how to set up a shared component root with the existing [components](../../docs/content/guide/components.rst) and [static-assets](../../docs/content/guide/static-assets.rst) systems. Copy it into your own project the same way the examples here do, or use it as a reference for building your own kit.

## Why a shared kit

Without one, every page tree tends to copypaste utility classes for buttons, cards, badges, tables, and form fields. The examples in this repo used to ship a different `max-w-*` per layout, four flavours of "muted text", and two ways to render an outline button. The shared kit replaces that with one Python-friendly `{% component %}` call per primitive and one set of CSS variables for the palette.

## Layout

```
examples/_shared/
├── _components/
│   ├── page_head/{component.djx}        # <head>, Tailwind CDN + inline config, tokens
│   ├── app_shell/{component.djx}
│   ├── button/{component.djx,component.py}
│   ├── card/{component.djx}
│   ├── badge/{component.djx,component.py}
│   ├── input/    textarea/    label/    field/
│   ├── alert/
│   ├── table/    nav/    nav_link/
│   ├── page_header/    container.djx    section/    separator.djx
│   ├── empty_state/    skeleton/    avatar/    stat_card/
│   ├── dropdown/{component.djx,component.mjs}
│   └── dialog/{component.djx,component.mjs}
└── static/shared/css/
    ├── tokens.css         # CSS custom properties (--background, --primary, --radius, …)
    └── base.css           # body backdrop, focus rings, font stack
```

## Wiring it into a project

Two changes inside `config/settings.py`:

```python
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SHARED_DIR = BASE_DIR.parent / "_shared"

STATICFILES_DIRS = [SHARED_DIR / "static"]

NEXT_FRAMEWORK = {
    "DEFAULT_PAGE_BACKENDS": [...],
    "DEFAULT_COMPONENT_BACKENDS": [
        {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [str(SHARED_DIR / "_components")],
            "COMPONENTS_DIR": "_widgets",
        },
    ],
}
```

The `DIRS` entry registers `_shared/_components` as a **global** root. Components inside resolve at the empty route scope and become callable from every template (see the [components guide](../../docs/content/guide/components.rst) `components-routing` section).

`STATICFILES_DIRS` adds the shared static tree so `tokens.css` and `base.css` resolve under `/static/shared/...`.

Each example houses the shared HTML envelope in a project-level page root listed under `DEFAULT_PAGE_BACKENDS["DIRS"]` — `chrome/`, `host/`, `site/`, `frame/`, `shell/`, `portal/`, `instrument/`, `marketplace/`, `cockpit/`, `studio/`, or `root_pages/` depending on the project. The dir contains a single `layout.djx` (and optionally `_<components-dir>/` for project-shared components) that wraps every page rendered by the per-app `PAGES_DIR` tree:

```django
<!DOCTYPE html>
<html lang="en">
{% component "page_head" title="My app" %}
<body class="min-h-screen flex flex-col">
  {% #component "app_shell" brand="🔗 My app" brand_href="/" %}
    {% #slot "content" %}
      {% block template %}{% endblock template %}
    {% /slot %}
  {% /component %}
  {% collect_scripts %}
</body>
</html>
```

`page_head` owns the entire `<head>`. It pulls in the Tailwind Play CDN, inlines a `tailwind.config` that maps the design tokens to short colour names (`bg-primary`, `text-muted-foreground`, `border-border`, …), and registers `tokens.css` plus `base.css`. Pass `tailwind_plugins="typography"` to add the matching CDN plugin parameter, or use the `extra` slot to inject extra `<link>`/`<meta>`/`<style>` tags.

To register the project-level root, list it in both backends' `DIRS` when you also want components to live there:

```python
NEXT_FRAMEWORK = {
    "DEFAULT_PAGE_BACKENDS": [
        {
            "BACKEND": "next.urls.FileRouterBackend",
            "APP_DIRS": True,
            "DIRS": [str(BASE_DIR / "site")],   # owns layout.djx
            "PAGES_DIR": "screens",
            ...
        },
    ],
    "DEFAULT_COMPONENT_BACKENDS": [
        {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [
                str(SHARED_DIR / "_components"),
                str(BASE_DIR / "site" / "_parts"),  # project-shared components
            ],
            "COMPONENTS_DIR": "_parts",
        },
    ],
}
```

The page backend entry makes the file router walk `site/` and treat its `layout.djx` as the outermost wrapper. The matching component backend entry registers `site/_parts/` as a root-scope component source so a `site_footer` (or any other one-off component shared across the whole project) resolves from every template. The [`markdown-blog`](../markdown-blog/) example wires both, and [`multi-tenant`](../multi-tenant/) uses the same pattern with its `root_pages/` and `root_blocks/` directories.

## Design tokens and Tailwind config

`tokens.css` defines shadcn-style colour and radius variables on `:root`:

```css
:root {
  --background: 0 0% 100%;
  --foreground: 240 10% 3.9%;
  --primary: 240 5.9% 10%;
  --primary-foreground: 0 0% 98%;
  --secondary: 240 4.8% 95.9%;
  --muted: 240 4.8% 95.9%;
  --muted-foreground: 240 3.8% 46.1%;
  --accent: 240 4.8% 95.9%;
  --destructive: 0 84.2% 60.2%;
  --border: 240 5.9% 90%;
  --input: 240 5.9% 90%;
  --ring: 240 5.9% 10%;
  --radius: 0.5rem;
  --container: 72rem;
}
```

`page_head` ships an inline `tailwind.config` that maps each token to a short colour name with an `<alpha-value>` placeholder. Templates write the regular short form:

```django
<p class="bg-card text-card-foreground border-border">…</p>
<button class="bg-primary text-primary-foreground hover:bg-primary/90">Save</button>
<div class="bg-muted/40 text-muted-foreground">muted backdrop</div>
```

Per-tenant or per-app overrides drop a smaller block of CSS variables on `body` (the [`multi-tenant`](../multi-tenant/) example does this with `--tenant-accent` to recolour the brand without touching the global palette).

The same inline config also exposes `rounded-lg`/`md`/`sm` mapped to `var(--radius)` and `max-w-container` mapped to `var(--container)`.

## Component catalogue

Every entry below is a void call (`{% component "name" prop=value %}`) or a block call (`{% #component "name" %}{% #slot "content" %}…{% /slot %}{% /component %}`) depending on whether you need slots.

| Component | Props | Slots |
|---|---|---|
| `page_head` | `title`, `tailwind_plugins` | `extra` (extra `<link>`/`<meta>`/`<style>` injected before `</head>`) |
| `button` | `variant` (default/secondary/outline/ghost/destructive/link), `size` (sm/md/lg/icon), `type`, `href`, `target`, `name`/`value`, `disabled`, `text`, `extra` | `content` (falls back to `{{ text }}`) |
| `card` | `title`, `description`, `extra` | `content`, `footer` |
| `badge` | `variant` (default/secondary/outline/destructive/success/warning/info/muted), `text`, `extra` | `content` (falls back to `{{ text }}`) |
| `input` / `textarea` | `type`, `name`, `id`, `value`, `placeholder`, `autocomplete`, `required`, `disabled`, `autofocus`, `rows`, `extra` | — |
| `label` | `for_id`, `text`, `extra` | `content` (falls back to `{{ text }}`) |
| `field` | `label`, `for_id`, `required`, `help`, `error`, `extra` | `control` |
| `alert` | `variant` (default/info/success/warning/destructive), `title`, `text`, `extra` | `content` (falls back to `{{ text }}`) |
| `table` | `extra`, `wrapper_extra` | `content` (write raw `<thead>`/`<tbody>`/`<tr>` inside) |
| `nav` | `extra` | `content` |
| `nav_link` | `url_name` (Django named route) **or** `url` (literal), `url_kwargs`, `url_args`, `active_when` (substring match against `resolver_match.view_name`), `label`, `variant` (tabs/pills/bar), `extra` | `content` (falls back to `{{ label }}`) |
| `page_header` | `eyebrow`, `title`, `description`, `extra` | `description` (falls back to `<p>{{ description }}</p>`), `actions` |
| `stat_card` | `label`, `value`, `hint`, `extra` | `value` (falls back to `{{ value }}`) |
| `empty_state` | `icon`, `title`, `description`, `extra` | `actions` |
| `app_shell` | `brand`, `brand_href`, `brand_icon`, `main_extra`, `header_visible` | `brand` (falls back to brand text + icon + href chrome), `nav`, `actions`, `content`, `page_footer` |
| `dropdown` | `label`, `extra` | `trigger`, `items` |
| `dialog` | `id`, `title`, `description`, `extra` | `content`, `footer` |

## Quick recipes

A button with an explicit slot for an icon:

```django
{% #component "button" variant="default" %}
  {% #slot "content" %}
    <svg viewBox="0 0 20 20" class="h-4 w-4">…</svg>
    Save
  {% /slot %}
{% /component %}
```

A card with header, body, and an action bar in the footer:

```django
{% #component "card" title="Plan" description="Free for ever" %}
  {% #slot "content" %}<ul>…</ul>{% /slot %}
  {% #slot "footer" %}
    {% component "button" text="Choose plan" variant="default" %}
  {% /slot %}
{% /component %}
```

A form field with a server-side error and a Django widget passed in as the control:

```django
{% #component "field" label="Slug" for_id="id_slug" error=form.slug.errors|join:" " %}
  {% #slot "control" %}{{ form.slug }}{% /slot %}
{% /component %}
```

Active-state navigation (works with namespaced URL names too):

```django
{% component "nav_link" url_name="next:page_admin" active_when="page_admin" label="Admin" variant="bar" %}
```

Add a favicon or inline `<style>` to the page head:

```django
{% #component "page_head" title="Dashboard" %}
  {% #slot "extra" %}
    <link rel="icon" href="/static/site/favicon.svg">
  {% /slot %}
{% /component %}
```

## Prop / slot naming

Slot and prop names live in separate namespaces — see the [components guide](../../docs/content/guide/components.rst) section "Slots and props share no namespace". The kit takes advantage of this and ships several composites where a slot intentionally shares a name with a prop so the prop drives the default while a slot still overrides it:

- `app_shell` exposes a `brand` slot that defaults to the standard brand chrome driven by `brand`, `brand_href`, and `brand_icon` props. Override the slot to drop in a custom SVG logo while keeping the same surrounding layout.
- `page_header` exposes a `description` slot whose default body is `<p>{{ description }}</p>`. Override it when the description needs richer markup than a plain paragraph allows.
- `stat_card` exposes a `value` slot that defaults to `{{ value }}`. Override it when the value needs additional decoration such as a trend arrow or a unit suffix.

The default-body path keeps the void call site short (`{% component "page_header" title="…" description="…" %}`) while the slot path stays available for the cases that need it.

## React and Vue alongside the kit

[`examples/kanban`](../kanban/) and [`examples/live-polls`](../live-polls/) keep their respective Vite-built React and Vue components for interactive surfaces (board drag-drop, SSE-driven chart). The surrounding chrome — header, page title, buttons, forms — comes from the shared `.djx` palette, so the visual language stays unified across template-only and SPA-style example apps. React and Vue files freely reuse the same Tailwind utilities and CSS custom properties, which is how the colour scheme stays consistent inside the mounted React tree.

## Cleanup checklist when adopting the kit

When you move an existing project onto the shared kit:

- Wire `SHARED_DIR`, `STATICFILES_DIRS`, and `DEFAULT_COMPONENT_BACKENDS["DIRS"]` once in `settings.py`.
- Remove any per-app `nav_link` / `stat_card` / `card` that now duplicates a shared component, otherwise `manage.py check` raises `next.E034` (root namespace collision).
- Replace the `<head>` boilerplate (CDN script, two `{% use_style %}` lines, `{% collect_styles %}`) with `{% component "page_head" title="…" %}`.
- Replace bespoke colour classes (`bg-slate-50`, `text-slate-900`, `bg-indigo-600`, …) with the short token aliases (`bg-background`, `text-foreground`, `bg-primary`, …) so per-tenant overrides cascade correctly.
