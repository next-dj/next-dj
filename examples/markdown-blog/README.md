# Markdown blog

A blog whose articles are plain Markdown files on disk, each opened by a few lines of front matter. One dynamic route at `screens/posts/[slug]/` serves every post, and the front matter feeds the page head: the description, the keywords, the Open Graph article block and the `BlogPosting` structured data. A custom `MarkdownTemplateLoader` registered under `NEXT_FRAMEWORK["TEMPLATE_LOADERS"]` shows the other road for a static page, a sibling `template.md` read as the page body.

The example covers the reading side of the framework: a custom `TemplateLoader` plug-in, the `template_loaded` signal, a two-root page layout, `@context(serialize=True)` feeding `window.Next.context` for a share button, a co-located `component.js`, a Django context processor wired through the router, page metadata with JSON-LD, breadcrumbs, an RSS feed, a social image, a sitemap and a `robots.py`.

## What you will see

| URL | Description |
| --- | --- |
| `/` | Latest posts, one entry per Markdown file under `blog/posts/`, the newest first. |
| `/posts/welcome/` | A longer post. Headings, lists, breadcrumbs and reading-time meta. |
| `/posts/hello-world/` | A minimal post with a fenced code block and no `description` of its own. |
| `/about/` | Static page. A `template.md` body read by the custom loader, and a `page.py` that only declares metadata. |
| `/feed.xml` | RSS over every post, advertised in the head of every page. |
| `/sitemap.xml` | Every route, each post dated by its last edit. |
| `/robots.txt` | Built from `robots.py` at the page root, its `Sitemap:` line written by the framework. |

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

### 2. One route for every post

A post is a file, not a folder. [`blog/posts/welcome.md`](blog/posts/welcome.md) opens with its front matter:

```markdown
---
title: Welcome to the blog
author: Ada Lovelace
date: 2026-01-12
updated: 2026-03-02
description: A tour of the blog, where every post is a Markdown file and one dynamic route serves them all.
keywords: next.dj, markdown, django
---
This is a demo blog built on **next-dj**. ...
```

[`blog/posts.py`](blog/posts.py) reads it into a frozen `Post`. `parse_post` splits the block off the top, reads `key: value` lines, and keeps the rest as Markdown source. `description` is optional, [`hello-world.md`](blog/posts/hello-world.md) leaves it out and `excerpt` stands in, the first paragraph that is not a heading with inline markup stripped and cut to 24 words. `updated` is optional too, and `Post.modified` answers the publication day for a post never edited. `load_post(slug)` raises `Http404` for a slug without a file, `all_posts()` lists every file the newest first, and `Post.html` renders the body through [`blog/markdown_template.py`](blog/markdown_template.py), whose `fenced_code` extension turns the code block in `hello-world` into `<pre><code class="language-python">`.

[`screens/posts/[slug]/page.py`](blog/screens/posts/%5Bslug%5D/page.py) is the only post page there is:

```python
@context("article")
def article(slug: str) -> Post:
    return load_post(slug)


@context("post", serialize=True)
def post(article: Post) -> dict[str, str]:
    return {"slug": article.slug, "title": article.title}
```

`article` reads the file the URL names once per request. `post` names the `article` key as its parameter and receives the value the first callable produced, the way section 8 does for the head, and hands the share button the two fields it reads through `window.Next.context.post`. Everything else stays on the server. [`template.djx`](blog/screens/posts/%5Bslug%5D/template.djx) beside it is one line, `{{ article.html }}`. Adding a post means adding one `.md` file, the route, the index, the feed and the sitemap pick it up.

### 3. Custom `MarkdownTemplateLoader` for a static page

[`blog/loaders.py`](blog/loaders.py) subclasses `TemplateLoader` and treats a sibling `template.md` as the page body. [`screens/about/`](blog/screens/about/) holds a `page.py` that only declares metadata and a `template.md` the loader reads:

```python
class MarkdownTemplateLoader(TemplateLoader):
    source_name = "template.md"

    def can_load(self, file_path: Path) -> bool:
        return (file_path.parent / "template.md").exists()

    def load_template(self, file_path: Path) -> str | None:
        md_file = file_path.parent / "template.md"
        try:
            html = render_markdown(md_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return None
        return format_html(PROSE, SafeString(html))

    def source_path(self, file_path: Path) -> Path | None:
        md_file = file_path.parent / "template.md"
        return md_file if md_file.exists() else None
```

Three methods, three responsibilities:

- **`can_load`** — cheap existence check, so the chain can skip this loader without touching the disk twice.
- **`load_template`** — reads the file and returns the rendered body string inside a `prose` article, since Markdown carries no classes of its own. Returning `None` on a read error lets the chain fall through to the next loader instead of raising mid-request.
- **`source_path`** — points at the on-disk file for the stale-cache detector, so editing a `.md` file recomposes the template on the next request without a server restart.

`source_name = "template.md"` is the label the framework prints in `next.W043` when a page declares this source alongside a higher-priority one. A loader fits a page whose body is one file of its own. It is the wrong tool for the posts, since a loader sees the `page.py` path and never the URL, so a dynamic route reads its content in a `@context` instead.

### 4. Nested layout, breadcrumbs and the meta bar

[`screens/posts/layout.djx`](blog/screens/posts/layout.djx) wraps every post. It renders the breadcrumb trail, the title, the publication date, the reading time and the share button, and a `prose` container that receives the article HTML through `{% template %}`. The outer [`site/layout.djx`](site/layout.djx) wraps that article in turn, which makes the post pages a two-level layout composition across two page roots.

The trail is not spelled by hand:

```django
{% breadcrumbs as crumbs %}
<nav aria-label="Breadcrumb">
  <ol>
    {% for crumb in crumbs %}
      <li>{% if crumb.current %}<span aria-current="page">{{ crumb.label }}</span>
          {% else %}<a href="{{ crumb.url }}">{{ crumb.label }}</a>{% endif %}</li>
    {% endfor %}
  </ol>
</nav>
```

Each `page.py` from the root down adds one crumb, labelled by its `breadcrumb` key or by its own title. The root [`screens/page.py`](blog/screens/page.py) declares `"breadcrumb": "Home"`, a key that is never inherited, `screens/posts/` holds no `page.py` and adds nothing, and the post adds its title. The bare `{% breadcrumbs %}` renders the same trail as a plain `<nav>`, the `as` form hands the crumbs to a template that styles them. The head gets the trail as well: a `BreadcrumbList` joins the JSON-LD graph of every page two crumbs deep, the one section 8 shows beside the `BlogPosting`.

### 5. Page body priority

next.dj resolves the page body in this order:

1. A `render(request, ...)` function returning `str` (composed through the layout) or any `HttpResponse` subclass (returned verbatim, the escape hatch for redirects, JSON, streaming).
2. A `template = "..."` module attribute on the page.
3. The first registered `TemplateLoader` whose `can_load(page)` returns `True`.

Only step 3 is used here, and both loaders take part. `MarkdownTemplateLoader` backs `/about/`, `DjxTemplateLoader` backs the index (`screens/page.py` beside `screens/template.djx`) and the post route. The highest-priority present source wins, and a page declaring more than one gets [`next.W043`](../../docs/content/ref/system-checks.rst) at `manage.py check` time naming the winner. A `TEMPLATE_LOADERS` entry that is not a string surfaces as `next.E042`, one that cannot be imported as `next.E043`, and one that resolves to a class that is no `TemplateLoader` as `next.E089`.

### 6. Tracing which loader won through `template_loaded`

The framework sends `next.pages.signals.template_loaded` after a page registers its template source, with the page `file_path` as the only payload. [`blog/receivers.py`](blog/receivers.py) uses it to record which source backed each page, the question the priority list above raises in practice:

```python
@receiver(template_loaded)
def _on_template_loaded(file_path: Path, **kwargs) -> None:
    with _lock:
        _loader_hits[str(file_path)] = _detect_source(file_path)
```

`_detect_source` looks for a sibling `template.md`, then a sibling `template.djx`, and reports `page.py` when neither is present. The map is guarded by a `threading.Lock` because pages load lazily on first request and the dev server serves those requests on several threads. `loader_hits()` returns a copy, so a caller never iterates the live dictionary while another thread writes to it. [`BlogConfig.ready`](blog/apps.py) imports the module so the connection exists before the first page loads.

### 7. Share button, a component with no Python side

[`_parts/share_button/`](blog/screens/_parts/share_button/) is a directory with `component.djx` and `component.js` and no `component.py`. A directory holding a `component.djx` is already a component, so nothing has to be added to make the framework find it.

The click handler reads the serialized post the page put on the window:

```js
const post = window.Next?.context?.post;
await navigator.clipboard.writeText(`${post.title} — ${location.href}`);
```

`component.js` is collected by `{% collect_scripts %}` in the root layout only on pages that render the component. The index and `/about/` never call `share_button`, so its script is absent from their HTML. The handler bails out when `window.Next.context.post` is missing, which is what happens if the component is ever rendered outside a post page, and it reports a failed `navigator.clipboard` write on the button itself rather than throwing.

### 8. Page metadata from the front matter

The `<title>` is not in any template. [`site/layout.djx`](site/layout.djx) calls the shared `page_head` component and that component renders `{% metadata %}`, the builtin tag that writes the head tags of the page being rendered. What it writes is the fold of three tiers, outermost first.

The settings tier is `NEXT_FRAMEWORK["SITE"]` beside `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` in [`config/settings.py`](config/settings.py). `SITE["URL"]` is the published origin, `https://blog.example` here, and every relative URL the tag emits is made absolute against it, so the canonical, the feed and the social image point at the published domain rather than at whatever host served the request. `SITE["NAME"]` fills `{site_name}` and `og:site_name`. `SITE["INDEXABLE"]` is pinned to `True`, the default `"auto"` follows `DEBUG` and would put `noindex` on every page of the dev server. The defaults hold the viewport, the tab icon, a site-wide `description`, `og: {"type": "website"}` and the `title` template `{title} · {site_name}` with its `default`.

The root [`screens/page.py`](blog/screens/page.py) declares the tier every page shares, a module dict every descendant inherits ahead of its own metadata:

```python
metadata: MetadataDict = {
    "title": "Latest posts",
    "breadcrumb": "Home",
    "canonical": True,
    "og": {
        "images": [
            {
                "url": SOCIAL_IMAGE,
                "type": "image/png",
                "width": 1200,
                "height": 630,
                "alt": "A white band with an indigo mark on a slate background",
            }
        ]
    },
    "alternates": {
        "feeds": [{"url": reverse_lazy("feed"), "type": "rss", "title": "next.dj blog"}]
    },
}
```

The dict folds once, not per request, and the feed address comes from its URL name rather than being spelled twice, since `reverse_lazy("feed")` waits for the URLconf and reverses when a page renders. `canonical: True` means the page's own path, so `/posts/welcome/` emits `https://blog.example/posts/welcome/` without anyone spelling it. `alternates.feeds` adds `<link rel="alternate" type="application/rss+xml">`, so a feed reader pointed at any page finds section 9. `og.images` is the social card of section 10. [`screens/about/page.py`](blog/screens/about/page.py) is the smallest possible `page.py`, a dict with a title and a description.

A post cannot be a dict, its head lives in its front matter. `post_meta` in [`[slug]/page.py`](blog/screens/posts/%5Bslug%5D/page.py) is the dynamic tier:

```python
@page.metadata
def post_meta(article: Post) -> MetadataDict:
    return {
        "title": article.title,
        "description": article.description,
        "keywords": list(article.keywords),
        "og": {
            "type": "article",
            "article": {
                "published_time": article.published,
                "modified_time": article.modified,
                "authors": [article.author],
                "tags": list(article.keywords),
            },
        },
        "jsonld": [
            {
                "@type": "BlogPosting",
                "headline": article.title,
                "datePublished": article.published,
                "dateModified": article.modified,
                "image": [absolute_url(SOCIAL_IMAGE)],
                "author": [{"@type": "Person", "name": article.author}],
            }
        ],
    }
```

It names the `article` context key and reads the `Post` that callable already parsed. `keywords` takes a list and renders one `<meta name="keywords">` joined by commas. The `og` block merges into the one the settings and the root declare, so `og:type` turns to `article` while `og:site_name` and the social image stay. The structured data is a plain schema.org dict, the dates serialise as ISO days, and every node of the fold is placed in one `@graph`, so a post carries the `BlogPosting` and the `BreadcrumbList` of section 4 in a single script. The framework makes an `@id` absolute but leaves the other values as written, so `image` goes through `next.pages.metadata.absolute_url`, the helper the head resolves its own URLs with, and names the same card the Open Graph tags use, since search engines show an article as a rich result only when it has an image.

The integration tests read the head back through `next.testing.assert_metadata`, which parses the tags of a response and compares only the keys a test names. `manage.py check` validates every metadata dict and the sources of the page root at startup.

### 9. An RSS feed beside the pages

[`blog/feeds.py`](blog/feeds.py) is a stock `django.contrib.syndication` `Feed` over `all_posts()`, mounted at `/feed.xml` under the name `feed` in [`config/urls.py`](config/urls.py). Its `link`, `feed_url` and `item_link` go through `next.pages.metadata.absolute_url`, so the feed says `https://blog.example` whichever host served it.

### 10. A social image and a tab icon from staticfiles

[`blog/static/blog/opengraph-image.png`](blog/static/blog/opengraph-image.png) and [`icon.svg`](blog/static/blog/icon.svg) beside it are ordinary app static files, so `collectstatic` and a hashed storage apply to them like to any other asset. `SOCIAL_IMAGE` in [`blog/posts.py`](blog/posts.py) is `static("blog/opengraph-image.png")`, and the root metadata of section 8 declares it under `og.images` with its type, its 1200 by 630 size and its alt text, so every page renders the full `og:image` block. The tab icon is declared once under `icons` in `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]`, where the settings module can only spell its URL, `/static/blog/icon.svg`.

### 11. Context processor for site-wide chrome

[`blog/context_processors.py`](blog/context_processors.py) returns `site_tagline`, `site_year`, and `site_path` for every template rendered through the router. `site_path` is read straight off `request.path` and rendered by the footer, so the processor uses the argument the `next.E040` check requires it to accept instead of ignoring it.

### 12. URL names from the file router

| File | URL | Name |
| --- | --- | --- |
| `screens/page.py` + `screens/template.djx` | `/` | `next:page_` |
| `screens/about/page.py` + `screens/about/template.md` | `/about/` | `next:page_about` |
| `screens/posts/[slug]/page.py` + `template.djx` | `/posts/<slug>/` | `next:page_posts_slug` |

The index links each post with `{% url 'next:page_posts_slug' slug=post.slug %}`. `screens/posts/` itself holds only `layout.djx`, so it contributes chrome without becoming a route.

### 13. Sitemap and robots from the page root

A `sitemap.py` at the top of a page root switches `/sitemap.xml` on for that tree. The blog's is [`blog/screens/sitemap.py`](blog/screens/sitemap.py):

```python
changefreq = "weekly"


@sitemap.items("posts/[slug]")
def posts() -> Iterator[SitemapEntry]:
    for post in all_posts():
        yield SitemapEntry(kwargs={"slug": post.slug}, lastmod=post.modified)
```

Every route without a `[param]` segment is listed on its own, so `/` and `/about/` are in the document because they are directories. The post route has a parameter, so `@sitemap.items` names its URLs, one `SitemapEntry` per file with the kwargs that reverse the route and the day of its last edit as `lastmod`. The module attributes are the ones Django's `Sitemap` class reads, `changefreq` here, plus `cache` for a `cache_page` wrapper. Every `<loc>` is absolute on `SITE["URL"]`. The XML comes from the templates of `django.contrib.sitemaps`, which is why that app joins `INSTALLED_APPS` in [`config/settings.py`](config/settings.py). The tests read the document back through `next.testing.parse_sitemap`.

`include("next.urls")` sits at the root of [`config/urls.py`](config/urls.py), so it mounts `/sitemap.xml`, `/sitemap-<section>.xml` and `/robots.txt` at the host root with no URL of their own. `/sitemap-blog.xml` is the section of this one root, labelled after the app, and with a single root `/sitemap.xml` is the same document. An index takes its place on its own once a second root declares a `sitemap.py` or a section grows past `limit`.

Robots comes from [`blog/screens/robots.py`](blog/screens/robots.py), one `RobotsRule` that allows everything:

```python
rules = [RobotsRule(allow="/")]
```

The framework renders the group and appends the `Sitemap:` line itself, absolute on `SITE["URL"]`, so no host is written into a file. `rules` may also be a callable building the groups per request. A static `robots.txt` at the page root is served byte for byte instead, which suits a file handed over by someone else. A site has one source for `/robots.txt`, both files in one root or robots in two roots is an error at check time.

## Gotchas

### A page needs a body source, and a sibling layout counts

`next.E012` fails when a `page.py` has none of a `render()` function, a `template` attribute, a loader that can load it, or a sibling `layout.djx`. An _ancestor_ layout does not satisfy it. `/about/` passes through its `template.md`, the post route through its `template.djx`.

### The nav components resolve against `resolver_match`

The header calls the shared [`nav_link`](../_shared/_components/nav_link/component.py) component with `variant="bar"`, and its `is_active` callable compares `request.resolver_match.view_name` with the `url_name` prop. This blog only needs the exact match. The `active_when` prop for a substring match is exercised by [`examples/shortener`](../shortener/).

### The asset-version guard needs a deploy stamp

The example sets `STATIC_VERSION` and the partial asset version derives from it, the shared convention explained in the [examples README](../README.md#conventions-every-example-follows).

### Loader output is a template body, not a variable

`MarkdownTemplateLoader.load_template` returns HTML and the framework splices it into the composed template source, which Django's engine then parses. There is no `|safe` and no double escaping, and by the same token a loader must never return user-supplied HTML unsanitised. This example trusts the files in its own repository, the post bodies included, which `Post.html` marks safe for the same reason.

## Further reading

- [`next/pages/loaders.py`](../../next/pages/loaders.py) — the `TemplateLoader` ABC, `build_registered_loaders`, `compose_body`, and layout discovery.
- [`next/pages/signals.py`](../../next/pages/signals.py) — the `template_loaded` payload contract used in section 6.
- [`next/pages/processors.py`](../../next/pages/processors.py) — context-processor discovery across the router and Django `TEMPLATES`.
- [`next/pages/metadata/`](../../next/pages/metadata/) — the metadata chain of section 8: the `MetadataDict` schema, `absolute_url`, the breadcrumbs and the `{% metadata %}` renderer.
- [`next/seo/`](../../next/seo/) — the sitemap and robots of section 13 and `RobotsRule`.
- [`next/static/serializers.py`](../../next/static/serializers.py) — how `@context(serialize=True)` values reach `window.Next.context`.
- [`docs/content/topics/pages.rst`](../../docs/content/topics/pages.rst) — the "Custom template loaders" section this example anchors.
- [`docs/content/ref/system-checks.rst`](../../docs/content/ref/system-checks.rst) — `next.E012`, `next.E040`, `next.E042`, `next.E043`, `next.E089`, and `next.W043`.
