# Markdown blog

A blog whose articles are plain `template.md` files on disk. A custom `MarkdownTemplateLoader` registered under `NEXT_FRAMEWORK["TEMPLATE_LOADERS"]` teaches the framework to read those files as page bodies and renders them to HTML on request.

The example covers the reading side of the framework: a custom `TemplateLoader` plug-in, the `template_loaded` signal, a two-root page layout, `@context(serialize=True)` feeding `window.Next.context` for a share button, a co-located `component.js`, a Django context processor wired through the router, and virtual pages.

## What you will see

| URL                   | Description                                                |
| --------------------- | ---------------------------------------------------------- |
| `/`                   | Latest posts, one entry per folder under `screens/posts/`. |
| `/posts/welcome/`     | A longer post. Headings, lists, and reading-time meta.     |
| `/posts/hello-world/` | A minimal post with a fenced code block.                   |
| `/about/`             | Virtual page. Only `template.djx`, no `page.py`.           |

## How to run

```bash
cd examples/markdown-blog
uv run python manage.py migrate
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest
```

Nothing is stored in the database, so `migrate` only builds Django's own tables. Tailwind loads via the Play CDN inside the shared [`page_head`](../_shared/_components/page_head/component.djx) component, which [`site/layout.djx`](site/layout.djx) calls with `tailwind_plugins="typography"` so the `prose` classes wrapping the rendered Markdown resolve. No Node, no build step.

## Walking the code

### 1. Two page roots and two component roots

[`config/settings.py`](config/settings.py) renames the conventional folders, adds a project-level root beside the app tree, wires a per-router context processor, and registers the custom Markdown loader alongside the built-in djx loader:

```python
NEXT_FRAMEWORK = {
    "PAGE_BACKENDS": [
        {
            "BACKEND": "next.urls.FileRouterBackend",
            "APP_DIRS": True,
            "DIRS": [str(BASE_DIR / "site")],
            "PAGES_DIR": "screens",
            "OPTIONS": {
                "context_processors": [
                    "django.template.context_processors.request",
                    "blog.context_processors.site_nav",
                ]
            },
        }
    ],
    "COMPONENT_BACKENDS": [
        {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [
                str(SHARED_DIR / "_components"),
                str(BASE_DIR / "site" / "_parts"),
            ],
            "COMPONENTS_DIR": "_parts",
        }
    ],
    "TEMPLATE_LOADERS": [
        "blog.loaders.MarkdownTemplateLoader",
        "next.pages.loaders.DjxTemplateLoader",
    ],
}
```

`DIRS` names the project-level page root [`site/`](site/), which holds the single outer [`layout.djx`](site/layout.djx) with `<!DOCTYPE html>`, the `page_head` call, and `{% collect_scripts %}`. `APP_DIRS = True` plus `PAGES_DIR = "screens"` picks up the routable pages from [`blog/screens/`](blog/screens/). Neither root knows about the other, and every page in the app is wrapped by the project envelope first.

The component backend gets `site/_parts` as a second `DIRS` entry. A component root listed in `DIRS` resolves at the empty route scope, so [`site_footer`](site/_parts/site_footer/component.djx) is visible from every template in the project without living next to any page. The shared shadcn kit in [`../_shared/_components/`](../_shared/_components/) is the first entry and supplies `page_head`, `app_shell`, `nav_link`, and `page_header`.

`OPTIONS.context_processors` is the router's own extension point for per-page context processors. It merges with Django's `TEMPLATES[0].OPTIONS.context_processors`, entries from the router take priority, and duplicates are dropped.

`TEMPLATE_LOADERS` is a list of dotted paths to `next.pages.loaders.TemplateLoader` subclasses. Supplying the key **replaces** the default `["next.pages.loaders.DjxTemplateLoader"]`, so the djx loader is listed explicitly to keep `template.djx` support.

### 2. Custom `MarkdownTemplateLoader`

[`blog/loaders.py`](blog/loaders.py) subclasses `TemplateLoader` and treats a sibling `template.md` as the page body:

```python
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

- **`can_load`** — cheap existence check, so the chain can skip this loader without touching the disk twice.
- **`load_template`** — reads the file and returns the rendered body string. Returning `None` on a read error lets the chain fall through to the next loader instead of raising mid-request.
- **`source_path`** — points at the on-disk file for the stale-cache detector, so editing a `.md` file recomposes the template on the next request without a server restart.

`source_name = "template.md"` is the label the framework prints in `next.W043` when a page declares this source alongside a higher-priority one.

### 3. Markdown helpers

[`blog/markdown_template.py`](blog/markdown_template.py) holds what the loader does not. `render_markdown` runs the `markdown` package with the `fenced_code` extension, which is what turns the code block in `hello-world` into `<pre><code class="language-python">`. `post_metadata` scans the body for the first line starting with `# ` and falls back to the title-cased folder name when there is none, so a post without a heading still lists correctly on the index. `reading_minutes` counts whitespace-separated words at 200 wpm and never returns less than one minute.

The URL name is derived, not stored. `post_metadata` builds `next:page_posts_<slug>` with hyphens replaced by underscores, matching the name the file router generates for the folder.

### 4. Per-post `page.py`

Each post module registers metadata and nothing else, because the loader owns the body:

```python
_POST = Path(__file__).parent / "template.md"


@context("post", serialize=True)
def post() -> dict[str, str]:
    return post_metadata(_POST)


@context("reading_minutes")
def read() -> int:
    return reading_minutes(read_post_body(_POST))
```

No `template = "..."`, no `render()`, no `render_markdown` call. `post` carries `serialize=True` so `{slug, url_name, title}` lands in `window.Next.context.post` for the share button. `reading_minutes` stays server-only and feeds the meta bar. Import time computes a `Path` and nothing more, the file is read when a request arrives.

### 5. Nested layout wraps the rendered Markdown

[`screens/posts/layout.djx`](blog/screens/posts/layout.djx) adds the back link, the meta bar with the reading time and the share button, and a `prose` container that receives the article HTML through `{% block template %}`. The loader returns the body with no wrapper of its own, so the chrome lives entirely in the layout and every post under `screens/posts/` inherits it.

The outer [`site/layout.djx`](site/layout.djx) wraps that article in turn, which makes the post pages a two-level layout composition across two page roots.

### 6. Page body priority

next.dj resolves the page body in this order:

1. A `render(request, ...)` function returning `str` (composed through the layout) or any `HttpResponse` subclass (returned verbatim, the escape hatch for redirects, JSON, streaming).
2. A `template = "..."` module attribute on the page.
3. The first registered `TemplateLoader` whose `can_load(page)` returns `True`.

Only step 3 is used here, and both loaders take part. `MarkdownTemplateLoader` backs the two posts, `DjxTemplateLoader` backs the index (`screens/page.py` beside `screens/template.djx`) and the virtual `/about/` page. The highest-priority present source wins, and a page declaring more than one gets [`next.W043`](../../docs/content/ref/system-checks.rst) at `manage.py check` time naming the winner. A `TEMPLATE_LOADERS` entry that is not a string surfaces as `next.E042`, one that cannot be imported or is not a `TemplateLoader` subclass as `next.E043`.

### 7. Tracing which loader won through `template_loaded`

The framework sends `next.pages.signals.template_loaded` after a page registers its template source, with the page `file_path` as the only payload. [`blog/receivers.py`](blog/receivers.py) uses it to answer the question the priority list above raises in practice — which source actually backed a given page:

```python
@receiver(template_loaded)
def _on_template_loaded(file_path: Path, **kwargs) -> None:
    with _lock:
        _loader_hits[str(file_path)] = _detect_source(file_path)
```

`_detect_source` looks for a sibling `template.md`, then a sibling `template.djx`, and reports `page.py` when neither is present. The map is guarded by a `threading.Lock` because pages load lazily on first request and the dev server serves those requests on several threads. `loader_hits()` returns a copy, so a caller never iterates the live dictionary while another thread writes to it. [`BlogConfig.ready`](blog/apps.py) imports the module so the connection exists before the first page loads.

### 8. `{% url %}` with a variable name

The index links each post through the derived name rather than a hard-coded path:

```djx
{% for post in posts %}
  <a href="{% url post.url_name %}">{{ post.title }}</a>
{% endfor %}
```

Django's `{% url %}` accepts an unquoted variable as the name. Renaming a post folder changes the route, the generated URL name, and `post.url_name` together, so no template edit follows.

### 9. Share button, a component with no Python side

[`_parts/share_button/`](blog/screens/_parts/share_button/) is a directory with `component.djx` and `component.js` and no `component.py`. A directory holding a `component.djx` is already a component, so nothing has to be added to make the framework find it.

The click handler reads the serialized post metadata the page put on the window:

```js
const post = window.Next?.context?.post;
await navigator.clipboard.writeText(`${post.title} — ${location.href}`);
```

`component.js` is collected by `{% collect_scripts %}` in the root layout only on pages that render the component. The index and `/about/` never call `share_button`, so its script is absent from their HTML. The handler bails out when `window.Next.context.post` is missing, which is what happens if the component is ever rendered outside a post page, and it reports a failed `navigator.clipboard` write on the button itself rather than throwing.

### 10. Context processor for site-wide chrome

[`blog/context_processors.py`](blog/context_processors.py) returns `site_tagline`, `site_year`, and `site_path` for every template rendered through the router. `site_path` is read straight off `request.path` and rendered by the footer, so the processor uses the argument the `next.E040` check requires it to accept instead of ignoring it.

### 11. URL names from the file router

| File | URL | Name |
| --- | --- | --- |
| `screens/page.py` + `screens/template.djx` | `/` | `next:page_` |
| `screens/about/template.djx` (virtual) | `/about/` | `next:page_about` |
| `screens/posts/welcome/page.py` + `template.md` | `/posts/welcome/` | `next:page_posts_welcome` |
| `screens/posts/hello-world/page.py` + `template.md` | `/posts/hello-world/` | `next:page_posts_hello_world` |

Hyphens in folder names become underscores in the name, so `hello-world` routes as `/posts/hello-world/` and reverses as `page_posts_hello_world`. `screens/posts/` itself holds only `layout.djx`, so it contributes chrome without becoming a route.

## Gotchas

### A page needs a body source, and a sibling layout counts

`next.E012` fails when a `page.py` has none of a `render()` function, a `template` attribute, a loader that can load it, or a sibling `layout.djx`. An _ancestor_ layout does not satisfy it. Every post directory here passes through its `template.md`.

### The nav components resolve against `resolver_match`

The header calls the shared [`nav_link`](../_shared/_components/nav_link/component.py) component with `variant="bar"`, and its `is_active` callable compares `request.resolver_match.view_name` with the `url_name` prop. This blog only needs the exact match. The `active_when` prop for a substring match is exercised by [`examples/shortener`](../shortener/).

### The asset-version guard needs an explicit version

The example pins an explicit asset `VERSION` in `PARTIAL_BACKENDS`, the shared convention explained in the [examples README](../README.md#conventions-every-example-follows).

### Loader output is a template body, not a variable

`MarkdownTemplateLoader.load_template` returns HTML and the framework splices it into the composed template source, which Django's engine then parses. There is no `|safe` and no double escaping, and by the same token a loader must never return user-supplied HTML unsanitised. This example trusts the files in its own repository.

## Further reading

- [`next/pages/loaders.py`](../../next/pages/loaders.py) — the `TemplateLoader` ABC, `build_registered_loaders`, `compose_body`, and layout discovery.
- [`next/pages/manager.py`](../../next/pages/manager.py) — `_resolve_page_body` and the layout composition entry point.
- [`next/pages/signals.py`](../../next/pages/signals.py) — the `template_loaded` payload contract used in section 7.
- [`next/pages/processors.py`](../../next/pages/processors.py) — context-processor discovery across the router and Django `TEMPLATES`.
- [`next/static/serializers.py`](../../next/static/serializers.py) — how `@context(serialize=True)` values reach `window.Next.context`.
- [`docs/content/topics/pages.rst`](../../docs/content/topics/pages.rst) — the "Custom template loaders" section this example anchors.
- [`docs/content/ref/system-checks.rst`](../../docs/content/ref/system-checks.rst) — `next.E012`, `next.E040`, `next.E042`, `next.E043`, `next.W043`, and `next.W069`.
