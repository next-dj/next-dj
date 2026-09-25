# Markdown blog

A blog whose articles are plain `template.md` files on disk. A custom `MarkdownTemplateLoader` registered under `NEXT_FRAMEWORK["TEMPLATE_LOADERS"]` teaches the framework to read those files as page bodies and renders them to HTML on request.

The example covers the reading side of the framework: a custom `TemplateLoader` plug-in, the `template_loaded` signal, a two-root page layout, `@context(serialize=True)` feeding `window.Next.context` for a share button, a co-located `component.js`, a Django context processor wired through the router, and virtual pages.

## What you will see

| URL | Description |
| --- | --- |
| `/` | Latest posts, one entry per folder under `screens/posts/`. |
| `/posts/welcome/` | A longer post. Headings, lists, and reading-time meta. |
| `/posts/hello-world/` | A minimal post with a fenced code block. |
| `/es/posts/welcome/` | The same post under the Spanish prefix. Same body, `lang="es"`, its own canonical. |
| `/about/` | Static page. A `template.djx` body and a `page.py` that only declares metadata. |
| `/sitemap.xml` | Every static route once per language with hreflang alternates, from a four-line `sitemap.py`. |
| `/robots.txt` | The static `robots.txt` at the page root, served byte for byte. |

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

Each post module registers context and page metadata and nothing else, because the loader owns the body:

```python
_POST = Path(__file__).parent / "template.md"


@context("post", serialize=True)
def post() -> dict[str, str]:
    return post_metadata(_POST)


@context("reading_minutes")
def read() -> int:
    return reading_minutes(read_post_body(_POST))


@page.metadata
def post_meta(post: dict[str, str]) -> MetadataDict:
    return {"title": post["title"], "description": post["excerpt"]}
```

No `template = "..."`, no `render()`, no `render_markdown` call. `post` carries `serialize=True` so `{slug, url_name, title, excerpt}` lands in `window.Next.context.post` for the share button. `reading_minutes` stays server-only and feeds the meta bar. `post_meta` names the `post` context key as a parameter and receives the dict the first callable produced, so the Markdown is read once per request even though two callables want it. Section 12 covers what the tab and the crawlers see. Import time computes a `Path` and nothing more, the file is read when a request arrives.

### 5. Nested layout wraps the rendered Markdown

[`screens/posts/layout.djx`](blog/screens/posts/layout.djx) adds the back link, the meta bar with the reading time and the share button, and a `prose` container that receives the article HTML through `{% template %}`. The loader returns the body with no wrapper of its own, so the chrome lives entirely in the layout and every post under `screens/posts/` inherits it.

The outer [`site/layout.djx`](site/layout.djx) wraps that article in turn, which makes the post pages a two-level layout composition across two page roots.

### 6. Page body priority

next.dj resolves the page body in this order:

1. A `render(request, ...)` function returning `str` (composed through the layout) or any `HttpResponse` subclass (returned verbatim, the escape hatch for redirects, JSON, streaming).
2. A `template = "..."` module attribute on the page.
3. The first registered `TemplateLoader` whose `can_load(page)` returns `True`.

Only step 3 is used here, and both loaders take part. `MarkdownTemplateLoader` backs the two posts, `DjxTemplateLoader` backs the index (`screens/page.py` beside `screens/template.djx`) and the virtual `/about/` page. The highest-priority present source wins, and a page declaring more than one gets [`next.W043`](../../docs/content/ref/system-checks.rst) at `manage.py check` time naming the winner. A `TEMPLATE_LOADERS` entry that is not a string surfaces as `next.E042`, one that cannot be imported as `next.E043`, and one that resolves to a class that is no `TemplateLoader` as `next.E089`.

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
| `screens/about/page.py` + `screens/about/template.djx` | `/about/` | `next:page_about` |
| `screens/posts/welcome/page.py` + `template.md` | `/posts/welcome/` | `next:page_posts_welcome` |
| `screens/posts/hello-world/page.py` + `template.md` | `/posts/hello-world/` | `next:page_posts_hello_world` |

Hyphens in folder names become underscores in the name, so `hello-world` routes as `/posts/hello-world/` and reverses as `page_posts_hello_world`. `screens/posts/` itself holds only `layout.djx`, so it contributes chrome without becoming a route. Every name resolves under the Spanish prefix too, because [`config/urls.py`](config/urls.py) wraps `include("next.urls")` in `i18n_patterns(..., prefix_default_language=False)`: `/posts/welcome/` stays the English URL and `/es/posts/welcome/` serves the same page with Spanish active.

### 12. Page metadata from the post source

The `<title>` is not in any template. [`site/layout.djx`](site/layout.djx) calls the shared `page_head` component and that component renders `{% metadata %}`, the builtin tag that writes the head tags of the page being rendered. What it writes is the fold of three tiers, outermost first.

The settings tier is `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` in [`config/settings.py`](config/settings.py). `base` is the canonical origin of the site, `https://blog.example` here, and every relative URL the tag emits is made absolute against it, so the canonical and the hreflang links point at the published domain rather than at whatever host served the request. `site_name`, a site-wide `description`, `og: {"type": "website"}` and the `title` template `{title} · {site_name}` with its `default` sit beside it. The template applies to every page below, the default is what a page without a title of its own renders.

The root [`screens/page.py`](blog/screens/page.py) declares the static tier as a module dict, and a dict is inherited by every descendant:

```python
metadata: MetadataDict = {
    "title": "Latest posts",
    "canonical": True,
    "alternates": {"languages": True},
}
```

`canonical: True` means the page's own path, so `/posts/welcome/` emits `https://blog.example/posts/welcome/` without anyone spelling it. `alternates.languages: True` reads `LANGUAGES` and the `i18n_patterns` URLconf and emits one `<link rel="alternate" hreflang>` per language plus `x-default`, each URL translated through Django's `translate_url`. The `LocaleMiddleware` activates Spanish under `/es/`, and the `i18n` context processor registered on the page backend hands `LANGUAGE_CODE` to the layout, so `<html lang>` follows the prefix and `og:locale` follows the active language. [`screens/about/page.py`](blog/screens/about/page.py) is the smallest possible `page.py`, a dict with a title and a description, and it exists only so `/about/` names itself instead of inheriting `Latest posts`.

A post cannot be a dict because its title lives in the Markdown. The `post_meta` callable from section 4 is the dynamic tier, `@page.metadata` runs it for that page only, and its return value is the same `MetadataDict` shape. [`blog/markdown_template.py`](blog/markdown_template.py) supplies the `excerpt` it uses, the first paragraph after the heading with inline markup stripped and cut to a description-sized length. Static and dynamic are one form per file, a `page.py` declares either the dict or the callable, and the callable's name is anything but `metadata` because that module attribute is the dict form.

The integration tests read the rendered head straight off the response, and the browser suite asserts `page.title()` and the alternate links on a real page. `manage.py check` covers the structural half at import time, a template without `{title}`, a template without a default, a `page.py` declaring both forms, metadata without `{% metadata %}` anywhere in the composition, and `manage.py check --deploy --tag seo` audits the content of the static tier before a deploy.

### 13. Sitemap and robots from the page root

A `sitemap.py` at the top of a page root switches `/sitemap.xml` on for that tree. The blog's is [`blog/screens/sitemap.py`](blog/screens/sitemap.py), four lines and no code:

```python
i18n = True
alternates = True
x_default = True
changefreq = "weekly"
```

Every route without a `[param]` segment is listed on its own, so `/`, `/about/` and the two posts are in the document because they are directories, and nothing has to enumerate them. A route whose static metadata says `noindex` is left out, a dynamic route needs an `@sitemap.items` callable, which the [wiki](../wiki/) shows, and `exclude = ["drafts/**"]` drops whole trails by glob. The module attributes are the ones Django's `Sitemap` class reads, `i18n`, `languages`, `alternates`, `x_default`, `changefreq`, `priority`, `protocol`, `limit`, plus `cache` for a `cache_page` wrapper. `i18n = True` lists every URL once per entry of `LANGUAGES`, reversed under `translation.override`, so the `i18n_patterns` prefix of section 12 lands in the path, `/posts/welcome/` and `/es/posts/welcome/` are two `<url>` entries. `alternates` and `x_default` add the `<xhtml:link hreflang>` block to each of them, the same set the `<head>` already carries. Every `<loc>` is absolute on `base`, so the document says `https://blog.example` whichever host served it. The XML comes from the templates of `django.contrib.sitemaps`, which is why that app joins `INSTALLED_APPS` in [`config/settings.py`](config/settings.py).

The sitemap and the robots file belong at the host root, and [`config/urls.py`](config/urls.py) has `next.urls` inside `i18n_patterns`, so the routes that include mounts would answer at `/sitemap.xml` only through the prefix-free default language. The line `path("", include("next.seo.urls"))` above the language block mounts `sitemap.xml`, `sitemap-<section>.xml` and `robots.txt` at the root outright, and a `manage.py check` reports the prefixed mount when the include is missing. `/sitemap-blog.xml` is the section of this one root, labelled after the app, and with a single root `/sitemap.xml` is the same document. An index takes its place on its own once a second root declares a `sitemap.py` or a section grows past `limit`.

Robots has two forms and the blog uses the static one, [`blog/screens/robots.txt`](blog/screens/robots.txt). A `robots.txt` at the page root is served byte for byte as `text/plain; charset=utf-8`, nothing is appended, so the `Sitemap:` line is written by hand with the absolute URL, and the check warns when a sitemap exists and the file does not name it. The other form is a `robots.py` declaring `rules = [Rule(...)]`, the [wiki](../wiki/) and the [shortener](../shortener/) show it, and that form gets the `Sitemap:` line from the framework. A site has one source for `/robots.txt`, both files in one root or robots in two roots is an error at check time.

## Gotchas

### A page needs a body source, and a sibling layout counts

`next.E012` fails when a `page.py` has none of a `render()` function, a `template` attribute, a loader that can load it, or a sibling `layout.djx`. An _ancestor_ layout does not satisfy it. Every post directory here passes through its `template.md`.

### The nav components resolve against `resolver_match`

The header calls the shared [`nav_link`](../_shared/_components/nav_link/component.py) component with `variant="bar"`, and its `is_active` callable compares `request.resolver_match.view_name` with the `url_name` prop. This blog only needs the exact match. The `active_when` prop for a substring match is exercised by [`examples/shortener`](../shortener/).

### The asset-version guard needs a deploy stamp

The example sets `STATIC_VERSION` and the partial asset version derives from it, the shared convention explained in the [examples README](../README.md#conventions-every-example-follows).

### Loader output is a template body, not a variable

`MarkdownTemplateLoader.load_template` returns HTML and the framework splices it into the composed template source, which Django's engine then parses. There is no `|safe` and no double escaping, and by the same token a loader must never return user-supplied HTML unsanitised. This example trusts the files in its own repository.

## Further reading

- [`next/pages/loaders.py`](../../next/pages/loaders.py) — the `TemplateLoader` ABC, `build_registered_loaders`, `compose_body`, and layout discovery.
- [`next/pages/manager/__init__.py`](../../next/pages/manager/__init__.py) — `_resolve_page_body` and the layout composition entry point.
- [`next/pages/signals.py`](../../next/pages/signals.py) — the `template_loaded` payload contract used in section 7.
- [`next/pages/processors.py`](../../next/pages/processors.py) — context-processor discovery across the router and Django `TEMPLATES`.
- [`next/pages/metadata/`](../../next/pages/metadata/) — the metadata chain of section 12: the `MetadataDict` schema, the fold, and the `{% metadata %}` renderer.
- [`next/seo/`](../../next/seo/) — the sitemap and robots of section 13: `RouteSitemap` over the page tree, the two robots sources, and the `next.seo.urls` include.
- [`next/static/serializers.py`](../../next/static/serializers.py) — how `@context(serialize=True)` values reach `window.Next.context`.
- [`docs/content/topics/pages.rst`](../../docs/content/topics/pages.rst) — the "Custom template loaders" section this example anchors.
- [`docs/content/ref/system-checks.rst`](../../docs/content/ref/system-checks.rst) — `next.E012`, `next.E040`, `next.E042`, `next.E043`, `next.E089`, and `next.W043`.
