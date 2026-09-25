# DB-backed wiki

A wiki served from two stores at once. Some pages live as files in `wiki/routes/`. The rest live in the database as `Article` rows. Both kinds share one router, one root layout, and one search page.

## What you will see

| URL | Description |
| --- | --- |
| `/` | Index with a curated list of file documentation and every article in the database. |
| `/docs/routing/` | File-backed page describing how the file router works. |
| `/docs/components/` | File-backed page describing the composite component pattern and the block children channel. |
| `/articles/new/` | Create form with a live Markdown preview pane. |
| `/articles/edit/<slug>/` | Edit form for the given article with a live Markdown preview. |
| `/wiki/<slug>/` | Public article view rendered from the database row. |
| `/search/?q=routing` | Mixed search across the file catalogue and article titles plus bodies. |
| `/sitemap.xml` | The three documentation routes plus one entry per article row with its save date as `lastmod`. |
| `/robots.txt` | Rendered from `robots.py`, keeps crawlers off the search, names the sitemap. |

Articles persist in SQLite. After every create, edit, or delete the router rebuilds itself in-process and the new URL is reachable on the next request.

## How to run

```bash
cd examples/wiki
uv run python manage.py migrate
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest
```

Tailwind loads via the Play CDN in [`shell/layout.djx`](shell/layout.djx). No Node, no build step.

Open the index, click `New article`, write some Markdown, and submit. The new article becomes available at `/wiki/<slug>/` immediately. Open the search page with `?q=...` to see both file and database matches in the same response.

## Walking the code

### 1. Two URL sources, one router

`wiki.backends.HybridRouterBackend.generate_urls` returns `super().generate_urls()` plus one named pattern per existing article. Each alias targets the catchall callback at `wiki/[slug]/` and binds a fixed `slug` kwarg so `DArticle` resolves the right row. The catchall file route `wiki/routes/wiki/[slug]/page.py` owns the rendering logic. The aliases give templates a per-slug `reverse()` name and keep the public URL space tidy at `/wiki/<slug>/`.

The catchall view never has to know whether it was reached through the generic `<slug>` capture or through a hybrid alias. Both paths feed the same DI flow.

### 2. Reloading after data changes

`wiki/receivers.py` listens to `post_save` and `post_delete` of `Article` and calls `router_manager.reload()`. The reload path is one public method that:

1. Drops the cached backend list.
2. Reinstantiates every backend from `PAGE_BACKENDS`.
3. Clears Django's URL resolver caches.
4. Sends the `router_reloaded` signal.

The next request observes the fresh URL tree without a process restart. Tests confirm the loop end-to-end through `SignalRecorder`.

### 3. DI provider for the slug

`wiki.providers.ArticleProvider` claims any parameter annotated as `DArticle[Article]`. It reads `context.url_kwargs.get("slug")`, returns the matching row, raises `Http404` when no row carries that slug, and answers `None` when the call captured no slug at all. The catchall page and both contexts of the edit page use it, so the slug-to-row lookup lives in one place. `static_can_handle` settles the claim from the annotation, so the plan compiler picks the provider once per callable, and `compile_resolve` unpacks the model out of `DArticle[Article]` once per plan, leaving each request the query alone.

The edit form does not go through the provider. A `ModelForm` names the URL kwarg that identifies its row with `Meta.instance_from_url = "slug"`, and the framework loads that row onto `self.instance` when it builds the form, which is what the object-level permission hook in section 8 reads.

### 4. Form fields rendered by the shared kit

Both forms declare their widgets as `ComponentWidget("input")` and `ComponentWidget("markdown_textarea", ...)`, so `{{ form.body_md }}` renders shared kit components instead of Django's stock widget HTML. Extra keyword arguments travel as component props: `rows=12` reaches the `textarea` the composite renders. Around each control the shared `field` component supplies the label, the `for_id`, and the error slot.

### 5. Live preview nested inside the field widget

Neither page registers a preview context and neither template mentions the pane. `ComponentWidget("markdown_textarea")` is the whole wiring: the shared composite renders the plain `textarea` primitive and calls `{% component "markdown_preview" source=value %}` right under it from inside the widget render, so the pane is filled server-side from the bound field value on first paint and on every re-render after a failed submission. The JavaScript layer then wires the textarea to the pane for keystroke updates without round-tripping the server.

`render_markdown` escapes the body before handing it to the Markdown renderer, so inline HTML in an article reaches the page as text while headings, lists, fenced code, and links still resolve. It then rewrites `href` values pointing at `javascript:`, `data:`, or `vbscript:`, which Markdown auto-link parsing otherwise accepts. The co-located `component.mjs` repeats both steps against `marked`, so the pane the browser paints matches the one the server would have sent.

`markdown_preview` lives in [`examples/_shared/_components/markdown_preview/`](../_shared/_components/markdown_preview/) and `markdown_textarea`, the composite that pairs it with the control, sits beside it. Its `component.py` renders the `source` prop, its co-located `component.mjs` ships the client behaviour, and its `component.css` styles code and pre blocks. A component rendered by a `ComponentWidget` gets the same render frame a page template gets, so the nested calls resolve and the framework auto-discovers their co-located assets and dedupes them into the page slots, `markdown_preview.css` included. This example never names a static path. The server-side render is shared too: `render_markdown` lives in [`examples/_shared/markup.py`](../_shared/markup.py) and the wiki article page calls it for the published body.

The script registers its work through `Next.partial.onMount("[data-markdown-preview]", ...)` rather than a `document.querySelectorAll` scan at load. The runtime runs the callback over the initial DOM and over every subtree it later inserts, so a preview pane that re-renders inside a morphed form is rebound the same way the first render was, with no stale listener left behind by a swap. The callback binds to the control it sits next to, the `data-markdown-source` textarea the composite rendered it under, and remembers that pairing in a `WeakMap`, so a re-render rebinds nothing that is still wired and a form with two Markdown fields keeps two independent panes. The one shell powers both the wiki form and the near-identical multi-tenant note form without hardcoding a field name.

### 6. Slug reservations

`Article.clean()` rejects slugs that collide with file-route prefixes (`docs`, `articles`, `search`, `wiki`). Both forms enforce the same rule plus a uniqueness check against `Article.slug`. A reserved slug re-renders the form with the error message and the live preview pane intact.

### 7. LIKE search across two stores

`wiki/routes/search/page.py` runs two scans for each query. A literal substring match against a curated catalogue surfaces matching file pages. A `Q(title__icontains) | Q(body_md__icontains)` query against `Article` surfaces matching rows. The same template renders both lists side by side inside a `search-results` zone.

The search form carries `data-next-target="search-results"`, `data-next-trigger="input"`, and `data-next-debounce="300"`, so the runtime debounces keystrokes, issues a GET for the `search-results` zone, and morphs the two result lists in place. `page.py` does not change: the same view answers the full page and the zone request, reading `?q=` from `request.GET` either way. Without the runtime the form is a plain `<form method="get">` and the Search button reloads the page, so a bookmark of `/search/?q=routing` still reproduces the listing.

### 8. Object-level edit permission

`Article` carries a `locked` boolean. The edit form in `wiki/routes/articles/edit/[slug]/page.py` declares `has_object_permission(self)`, an object-level hook that the framework resolves like `on_valid` and runs after the form binds, so `self.instance` is the loaded target row. The override reads only what it needs from `self.instance` and returns `not self.instance.locked`. A locked article short-circuits to a bare 403 before validation runs, so there is no form re-render. The create form has no such hook and stays open.

### 9. Free children in the documentation figure

Both file-backed doc pages wrap examples in [`wiki/routes/docs/_blocks/doc_figure/`](wiki/routes/docs/_blocks/doc_figure/). The component sits inside the page tree, so it is visible from every template under `/docs/` and from nowhere else. It has exactly one insertion point, so a named slot would be ceremony: the caller writes markup between `{% #component "doc_figure" %}` and `{% /component %}`, the framework hands it over as `children`, and the template splices it with `{{ children }}`.

The two channels differ, and `/docs/components/` shows the difference on one call. The block body is spliced as written, so the `<em>` and `<strong>` runs inside it reach the page as markup — whether the values interpolated there were escaped is the calling template's business, exactly as with `{% include %}`. The `caption` prop carries the same snippet from `markup_sample` in `page.py` and is escaped like every prop, so the figcaption shows `<em>emphasis</em>` as text.

### 10. One layout, two page roots

The router walks two roots. `PAGE_BACKENDS["DIRS"]` lists `shell/`, a project-level page root whose only file is [`shell/layout.djx`](shell/layout.djx) with the outer HTML envelope. `APP_DIRS = True` plus `PAGES_DIR = "routes"` picks up `wiki/routes/`, which owns every page and ships no `layout.djx` of its own. One wrapper covers the whole site, so nothing here needs a nested layout. Examples that do are `examples/multi-tenant` and `examples/markdown-blog`.

### 11. Article titles through the slug provider

The tab title of an article is the article's title, and the row is not fetched again for it. [`wiki/[slug]/page.py`](wiki/routes/wiki/[slug]/page.py) registers two `@context` callables that take `item: DArticle[Article]`, the provider of section 3, and a third callable under `@page.metadata` that names the `article` context key as its parameter:

```python
@page.metadata
def article_meta(article: Article) -> MetadataDict:
    return {"title": article.title}
```

Context callables run before the head is rendered and share one dependency cache with the metadata callable, so a parameter named after a context key receives the value that callable produced, exactly as a downstream `@context` would. `article_meta` therefore reads the row the slug provider fetched for `article` and issues no query of its own, which the integration test pins by counting the by-slug selects of one GET. The `{% metadata %}` tag in the shared `page_head` component renders the fold of `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]`, the module dict of every ancestor `page.py`, and the callable, so the title lands as `Routing internals · next.dj Wiki` through the template the settings declare.

The rest of the tree is static. The root [`routes/page.py`](wiki/routes/page.py) declares a `title` default for the index, which renders as-is because a default skips the template, and a site description that every page inherits. The two documentation pages and the search page name themselves with a one-line dict. `articles/new/` and `articles/edit/<slug>/` add `"robots": {"index": False}`, so a crawler that follows the edit links off the index gets `<meta name="robots" content="noindex">` on the form pages and keeps the public article as the page worth ranking. The search page is the robots file's job instead, section 12 explains why the two are split that way.

### 12. A sitemap fed by the database

Articles are rows, so the sitemap has to ask the table. [`wiki/routes/sitemap.py`](wiki/routes/sitemap.py) sits at the top of the page root and declares the one dynamic trail:

```python
exclude = ["search", "articles/**"]


@sitemap.items("wiki/[slug]")
def articles() -> Iterator[Entry]:
    for article in Article.objects.only("slug", "updated_at"):
        yield Entry(kwargs={"slug": article.slug}, lastmod=article.updated_at)
```

The trail is the directory path of the page, `[slug]` included, and the check refuses a trail no page under the root answers to. Each `Entry` carries the kwargs that reverse the page's URL name, so `/wiki/<slug>/` is built by the router rather than spelled by hand, and the `lastmod` lands as `<lastmod>` on the entry. The callable runs through the same resolver as a `@context`, `request` is a parameter when it is needed and a `Depends` works, this one needs nothing. `only()` keeps the query to the two columns the entry reads. The file is loaded once and re-read when it changes, the query runs on every request of the document.

The static side of the tree needs no declaration. `/`, `/docs/routing/` and `/docs/components/` are listed because they are routes without a parameter, `articles/new/` is skipped because section 11 marks it `noindex`, and `exclude` drops two trails by glob, `search`, which the robots file below keeps crawlers off, and `articles/edit/[slug]`, a dynamic route with no callable. Without the second glob the check asks whether that trail was forgotten, and without the first it reports a listed URL that robots disallows. The aliases the `HybridRouterBackend` of section 1 appends are named patterns for the same page, the sitemap walks the page tree and lists each article once. `base` in `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` is `https://wiki.example`, so every `<loc>` is absolute on the published origin and the document does not depend on the host that served it. The section route is `/sitemap-wiki.xml`, labelled after the app.

[`wiki/routes/robots.py`](wiki/routes/robots.py) is the declared form of robots, `rules = [Rule(user_agent="*", disallow=["/search/"])]`, and `/robots.txt` renders the group followed by a `Sitemap:` line with the absolute sitemap URL. `host` beside `rules` adds a `Host:` line, an empty `robots.py` renders allow-all plus the `Sitemap:` line. The two mechanisms split the work by what each can do. A `Disallow` stops the fetch, which is right for the search, every query is a URL of its own and none of them has content to rank. A `noindex` needs the fetch, the tag is inside the page, so the two form pages of section 11 stay crawlable and carry the tag instead. The framework never derives one from the other, and `manage.py check` reports a `Disallow` that covers a `noindex` page or a URL the sitemap lists.

## Further reading

- [next/urls/manager.py](../../next/urls/manager.py) — `RouterManager.reload` emits the `router_reloaded` signal.
- [next/urls/backends.py](../../next/urls/backends.py) — `FileRouterBackend.generate_urls` is the public extension surface.
- [next/deps/providers.py](../../next/deps/providers.py) — DI provider contract used by `ArticleProvider`.
- [next/pages/metadata/](../../next/pages/metadata/) — the metadata chain behind `@page.metadata` and `{% metadata %}`.
- [next/seo/](../../next/seo/) — `@sitemap.items`, `Entry`, `Rule`, and the `RouteSitemap` that lists the page tree.
- [next/components/context.py](../../next/components/context.py) — `@component.context` wiring used by `markdown_preview`.
- [next/templatetags/components.py](../../next/templatetags/components.py) — the `{% #component %}` tag that collects slots and free children.
