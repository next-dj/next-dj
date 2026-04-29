# Markdown blog

A tiny blog built on **next-dj**. Every article is a plain `template.md` file
next to a `page.py`, and a custom `MarkdownTemplateLoader` registered under
`NEXT_FRAMEWORK["TEMPLATE_LOADERS"]` teaches the framework to read `.md` files
as page bodies and renders them to HTML on request.

The example focuses on the reading side of the framework: custom
`TemplateLoader` plug-in, nested layouts, `@context(serialize=True)` feeding
`window.Next.context` for a share button, co-located `component.js`, a Django
context processor, virtual pages, bodyless composite components, and shared
nav components.

## What you will see

| URL | Description |
|-----|-------------|
| `/` | Latest posts — one entry per folder under `screens/posts/`. |
| `/posts/welcome/` | A longer post. Headings, lists, and reading-time meta. |
| `/posts/hello-world/` | A minimal post with a fenced code block. |
| `/about/` | Virtual "about" page (no `page.py`, only `template.djx`). |

## How to run

```bash
cd examples/markdown-blog
uv run python manage.py migrate
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest
```

Tailwind loads via the Play CDN in [`screens/layout.djx`](blog/screens/layout.djx).
No Node, no build step.

## Walking the code

### 1. Rename directories to fit the domain

[`config/settings.py`](config/settings.py) renames the conventional folders,
wires a per-router context processor, and registers the custom Markdown loader
alongside the built-in djx loader:

```python
NEXT_FRAMEWORK = {
    "DEFAULT_PAGE_BACKENDS": [{
        "BACKEND": "next.urls.FileRouterBackend",
        "PAGES_DIR": "screens",
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "blog.context_processors.site_nav",
            ],
        },
    }],
    "DEFAULT_COMPONENT_BACKENDS": [{
        "BACKEND": "next.components.FileComponentsBackend",
        "COMPONENTS_DIR": "_parts",
    }],
    "TEMPLATE_LOADERS": [
        "blog.loaders.MarkdownTemplateLoader",
        "next.pages.loaders.DjxTemplateLoader",
    ],
}
```

`OPTIONS.context_processors` is the Next-router extension point for per-page
context processors. It is merged with Django's own
`TEMPLATES[0].OPTIONS.context_processors`, where entries from the Next router take
priority and duplicates are dropped.

`TEMPLATE_LOADERS` is a list of dotted paths to
`next.pages.loaders.TemplateLoader` subclasses. When the user supplies this
key, it **replaces** the default `["next.pages.loaders.DjxTemplateLoader"]` —
keep the djx loader in the list if you still want `template.djx` support.

### 2. Custom `MarkdownTemplateLoader`

[`blog/loaders.py`](blog/loaders.py) subclasses `TemplateLoader` and teaches
the framework to treat a sibling `template.md` as the page body:

```python
from pathlib import Path

from blog.markdown_template import render_markdown
from next.pages.loaders import TemplateLoader


class MarkdownTemplateLoader(TemplateLoader):
    source_name = "template.md"

    def can_load(self, file_path: Path) -> bool:
        return (file_path.parent / "template.md").exists()

    def load_template(self, file_path: Path) -> str | None:
        md_file = file_path.parent / "template.md"
        try:
            return render_markdown(md_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return None

    def source_path(self, file_path: Path) -> Path | None:
        md_file = file_path.parent / "template.md"
        return md_file if md_file.exists() else None
```

Three methods, three responsibilities:

- **`can_load`** — cheap check that returns `True` when the file this loader
  backs is present next to `page.py`.
- **`load_template`** — reads the file and returns the rendered body
  string. Returning `None` lets the chain fall through to the next loader.
- **`source_path`** — points at the on-disk file for the framework's
  stale-cache detector. When the file's mtime changes, the composed
  template is recomputed on the next request.

`source_name = "template.md"` is the human label surfaced in
[`next.W043`](../../docs/content/reference/system-checks.rst) when a page
declares both `template.md` and, say, a `template` module attribute.

### 3. Markdown helpers — pure, still used for metadata and reading-time

The loader handles the body. Post metadata (title, slug, URL name) and reading
time are still computed per-request by the per-post `page.py`:

```python
def post_metadata(post_path: Path) -> dict[str, str]:
    body = post_path.read_text(encoding="utf-8")
    first_line = body.splitlines()[0]
    slug = post_path.parent.name
    return {
        "slug": slug,
        "url_name": f"next:page_posts_{slug.replace('-', '_')}",
        "title": first_line.removeprefix("# ").strip(),
    }
```

### 4. Per-post `page.py` — two `@context` callables, no Markdown logic

Thanks to the loader, each post module is **tiny**:

```python
# screens/posts/welcome/page.py
from pathlib import Path

from blog.markdown_template import post_metadata, read_post_body, reading_minutes
from next.pages import context


_POST = Path(__file__).parent / "template.md"


@context("post", serialize=True)
def post() -> dict[str, str]:
    return post_metadata(_POST)


@context("reading_minutes")
def read() -> int:
    return reading_minutes(read_post_body(_POST))
```

No `template = "..."`, no `@context("post_html")`, no `render_markdown` call.
The body comes from the loader, and this module only exposes metadata for the
layout chrome and the JS share button.

- `post` (`serialize=True`) lands in `window.Next.context.post` for the share
  button — `{slug, url_name, title}`, small JSON payload.
- `reading_minutes` is server-only, shown in the meta bar.

Import is cheap — only a `Path` is computed. No file is opened until a
request comes in.

### 5. Nested layout provides the chrome and wraps the Markdown HTML

[`screens/posts/layout.djx`](blog/screens/posts/layout.djx) wraps every
article with the back link, meta bar, share button, and a `prose` container
that receives the rendered Markdown:

```djx
<article class="space-y-6">
  <a href="{% url 'next:page_' %}">← Back to posts</a>
  <header>
    <h1>{{ post.title }}</h1>
    <div class="flex items-center justify-between text-sm text-slate-500">
      <span>~ {{ reading_minutes }} min read · /posts/{{ post.slug }}/</span>
      {% component "share_button" %}
    </div>
  </header>

  <div class="prose prose-slate max-w-none">
    {% block template %}{% endblock template %}
  </div>
</article>
```

The `MarkdownTemplateLoader` returns raw HTML (no wrapper) and the layout puts
that HTML inside the `prose` container. This is the cleanest split — the
loader does one thing (Markdown → HTML), the layout does one thing (chrome).

The outer `screens/layout.djx` wraps this article in turn —
`screens/posts/layout.djx` is the *inner* layout in a two-level composition.

### 6. Page body priority — `render()` > `template` attr > registered loaders

next.dj resolves the page body in this order:

1. A `render(request, ...)` function returning `str` (composed through the
   layout) or any `HttpResponse` subclass (returned verbatim — escape hatch
   for redirects, JSON, streaming).
2. A `template = "..."` module attribute on the page.
3. First registered `TemplateLoader` whose `can_load(page)` returns `True`.
   In this blog that is `MarkdownTemplateLoader` (followed by the built-in
   `DjxTemplateLoader`). Used by the home index and `/about/` (`template.djx`)
   and by every post (`template.md`).

The highest-priority present source wins. If a page declares more than one,
`manage.py check` emits
[`next.W043`](../../docs/content/reference/system-checks.rst#compatibility)
naming the winner so the shadow is never silent. Misconfigured loaders
surface as `next.E042` / `next.E043` at check time.

### 7. `{% url %}` with a variable name

The index template links to each post dynamically:

```djx
{% for post in posts %}
  <a href="{% url post.url_name %}">{{ post.title }}</a>
{% endfor %}
```

Django's `{% url %}` accepts a **variable** name (unquoted). `post.url_name`
is computed inside `post_metadata` as `"next:page_posts_<slug_with_underscores>"`
and matches the file-router-generated URL name. Renaming a folder becomes a
search-and-replace in one file — templates stay correct.

### 8. Share button composite — bodyless, reads `window.Next.context`

[`_parts/share_button/`](blog/screens/_parts/share_button/) has no Python side:

```
share_button/
├── component.djx    # button markup with a data-share hook
└── component.js     # click handler, attached on page load
```

A composite component without `component.py` is fine — the framework treats any
directory with a `component.djx` as a component. The click handler reads the
serialized post meta:

```js
const post = window.Next?.context?.post;
await navigator.clipboard.writeText(`${post.title} — ${location.href}`);
```

`component.js` is auto-collected by `{% collect_scripts %}` in the root layout
only on pages that actually render the component — the homepage does not ship
this script.

### 9. Context processor — site-wide template variables

[`blog/context_processors.py`](blog/context_processors.py) is a standard Django
context processor. It receives `request` and returns a dict that is merged into
every template context:

```python
def site_nav(request: HttpRequest) -> dict[str, object]:  # noqa: ARG001
    return {
        "site_tagline": SITE_TAGLINE,
        "site_year": datetime.now(tz=UTC).year,
    }
```

Wired under `NEXT_FRAMEWORK["DEFAULT_PAGE_BACKENDS"][0]["OPTIONS"]["context_processors"]`.
The framework's [`next.E040`](../../docs/content/reference/system-checks.rst)
check fails at `manage.py check` time if the processor signature does not
accept `request`.

`request` is not used here (the processor returns constants), but the
signature is fixed by the Django contract. We explicitly allow the unused
argument with `# noqa: ARG001` so the rule that *actual* unused params are
dropped still holds everywhere else.

### 10. Shared `nav_link` component

Same idiom as the URL shortener: a composite component that reads
`request.resolver_match.view_name` and toggles classes.

```djx
{% component "nav_link" url_name="next:page_" label="Home" %}
{% component "nav_link" url_name="next:page_about" label="About" %}
```

The Python side resolves `href` via `reverse()` and compares the current view
name exactly:

```python
@component.context("is_active")
def _is_active(url_name: str, request: HttpRequest) -> bool:
    return request.resolver_match.view_name == url_name
```

This blog only needs exact match — the shortener example shows the prefix-match
variant via an `active_when` prop.

### 11. URL names from the file router

| File | URL | Name |
|------|-----|------|
| `screens/page.py` | `/` | `next:page_` |
| `screens/about/template.djx` (virtual) | `/about/` | `next:page_about` |
| `screens/posts/welcome/page.py` | `/posts/welcome/` | `next:page_posts_welcome` |
| `screens/posts/hello-world/page.py` | `/posts/hello-world/` | `next:page_posts_hello_world` |

Hyphens in folder names become underscores in the name: `hello-world` →
`page_posts_hello_world`.

## Gotchas

### `{% component %}` props are literal strings

`{% component "nav_link" url_name="next:page_about" %}` works;
`{% component "nav_link" url_name=some_var %}` passes the literal string
`"some_var"`. To drive a component from a list in a loop, either hardcode
the literal prop values (as the root nav does) or rely on the parent
template context being forwarded into the child render, and read
`some_var` directly inside `component.py`.

### `manage.py check` warns if a page has no body source

`next.E012` fails when a `page.py` has none of: `render()`, `template`
attribute, or a registered `TemplateLoader` that can load it. An **ancestor**
`layout.djx` alone is not enough. In this blog `MarkdownTemplateLoader`
satisfies the check for every post directory that has a `template.md`.

If you declare more than one source on the same page, `manage.py check`
emits [`next.W043`](../../docs/content/reference/system-checks.rst) naming the
winner: `render()` > `template` > (registered loaders in declaration order).
Misconfigured loader dotted paths surface as `next.E042` / `next.E043`.

### Virtual pages and bodyless components

The file router routes a directory that contains only a `template.djx` (no
`page.py`) as a **virtual page** — see `/about/` in this example. Likewise, a
component directory with just `component.djx` is a **composite component** and
the framework does not require an empty `component.py`. Skip any file that would
hold no code.

### Loader output is trusted — no `|safe` needed

`MarkdownTemplateLoader.load_template` returns HTML, and that HTML is the
**template body**. The framework places it inside the `posts/layout.djx` slot
and hands the composed string to Django's template engine, which treats it as
part of the template source — not as a Django variable — so there is no double
escaping. Never register a loader that returns user-supplied HTML without
sanitising — this example trusts the files on disk.

## Further reading

- [next/pages/manager.py](../../next/pages/manager.py) — unified view, body
  resolution (`_resolve_page_body`), layout composition entry point.
- [next/pages/loaders.py](../../next/pages/loaders.py) — `TemplateLoader` ABC,
  `build_registered_loaders`, `compose_body`, and layout discovery.
- [next/pages/processors.py](../../next/pages/processors.py) — context-processor
  discovery (Next router + Django `TEMPLATES`).
- [next/static/serializers.py](../../next/static/serializers.py) — how
  `@context(serialize=True)` values reach `window.Next.context`.
- [next/components/context.py](../../next/components/context.py) —
  `@component.context` and prop/parent flattening rules.
- [docs/content/guide/pages-and-templates.rst](../../docs/content/guide/pages-and-templates.rst)
  — "Custom template formats" section with a more detailed loader walkthrough.
