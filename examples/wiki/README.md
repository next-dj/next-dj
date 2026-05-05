# DB-backed wiki

A wiki served from two stores at once. Some pages live as files in
`wiki/routes/`. The rest live in the database as `Article` rows. Both
kinds share one router, one root layout, and one search page.

## What you will see

| URL | Description |
|-----|-------------|
| `/` | Index with a curated list of file documentation and every article in the database. |
| `/docs/routing/` | File-backed page describing how the file router works. |
| `/docs/components/` | File-backed page describing the composite component pattern. |
| `/articles/new/` | Create form with a live Markdown preview pane. |
| `/articles/edit/<slug>/` | Edit form for the given article with a live Markdown preview. |
| `/wiki/<slug>/` | Public article view rendered from the database row. |
| `/search/?q=routing` | Mixed search across the file catalogue and article titles plus bodies. |

Articles persist in SQLite. After every create, edit, or delete the
router rebuilds itself in-process and the new URL is reachable on the
next request.

## How to run

```bash
cd examples/wiki
uv run python manage.py migrate
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest
```

Tailwind loads via the Play CDN in [`wiki/routes/layout.djx`](wiki/routes/layout.djx).
No Node, no build step.

Open the index, click `New article`, write some Markdown, and submit.
The new article becomes available at `/wiki/<slug>/` immediately. Open
the search page with `?q=...` to see both file and database matches in
the same response.

## Walking the code

### 1. Two URL sources, one router

`wiki.backends.HybridRouterBackend.generate_urls` returns
`super().generate_urls()` plus one named pattern per existing article.
Each alias targets the catchall callback at `wiki/[slug]/` and binds a
fixed `slug` kwarg so `DArticle` resolves the right row. The catchall
file route `wiki/routes/wiki/[slug]/page.py` owns the rendering logic.
The aliases give templates a per-slug `reverse()` name and keep the
public URL space tidy at `/wiki/<slug>/`.

The catchall view never has to know whether it was reached through the
generic `<slug>` capture or through a hybrid alias. Both paths feed the
same DI flow.

### 2. Reloading after data changes

`wiki/receivers.py` listens to `post_save` and `post_delete` of
`Article` and calls `router_manager.reload()`. The reload path is one
public method that:

1. Drops the cached backend list.
2. Reinstantiates every backend from `DEFAULT_PAGE_BACKENDS`.
3. Clears Django's URL resolver caches.
4. Sends the `router_reloaded` signal.

The next request observes the fresh URL tree without a process restart.
Tests confirm the loop end-to-end through `SignalRecorder`.

### 3. DI provider for the slug

`wiki.providers.ArticleProvider` claims any parameter annotated as
`DArticle[Article]`. It reads `context.url_kwargs["slug"]` and either
returns the matching row or raises `Http404`. The catchall page,
the edit form, and the preview context all use the same provider,
so the slug-to-row lookup lives in one place.

### 4. Live preview through parent context

`articles/new/page.py` and `articles/edit/[slug]/page.py` both register
a context entry named `body`. The `markdown_preview` component reads
`body` from the parent template context, runs it through
`render_markdown`, and emits the safe HTML. No explicit prop is
needed because the parent context flows into the component scope. The
JavaScript layer wires the textarea to the preview pane for keystroke
updates without round-tripping the server.

### 5. Slug reservations

`Article.clean()` rejects slugs that collide with file-route prefixes
(`docs`, `articles`, `search`, `wiki`). Both forms enforce the same
rule plus a uniqueness check against `Article.slug`. A reserved slug
re-renders the form with the error message and the live preview pane
intact.

### 6. LIKE search across two stores

`wiki/routes/search/page.py` runs two scans for each query. A literal
substring match against a curated catalogue surfaces matching file
pages. A `Q(title__icontains) | Q(body_md__icontains)` query against
`Article` surfaces matching rows. The same template renders both lists
side by side.

### 7. No nested layout

The example wires only one `layout.djx` at the routes root. A nested
layout is not needed and would add noise. Examples that do need nested
layouts are `examples/multi-tenant` and `examples/markdown-blog`.

## Further reading

- [next/urls/manager.py](../../next/urls/manager.py) — `RouterManager.reload`
  emits the `router_reloaded` signal.
- [next/urls/backends.py](../../next/urls/backends.py) —
  `FileRouterBackend.generate_urls` is the public extension surface.
- [next/deps/providers.py](../../next/deps/providers.py) — DI provider
  contract used by `ArticleProvider`.
- [next/components/context.py](../../next/components/context.py) —
  `@component.context` wiring used by `markdown_preview`.
