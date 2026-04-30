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

## Framework features showcased

- **Hybrid router backend.** `wiki.backends.HybridRouterBackend`
  subclasses `FileRouterBackend` and overrides `generate_urls` to emit
  one named alias per article slug alongside the file routes. The
  aliases share a single catchall view.
- **Public router reload.** `next.urls.router_manager.reload()` rebuilds
  the URL tree, clears the Django resolver cache, and emits the
  `router_reloaded` signal. The example wires it to `post_save` and
  `post_delete` of `Article`.
- **DI provider for slug lookup.** `DArticle[Article]` mirrors the
  shortener pattern. The provider reads the URL `slug` and either
  returns the model or raises `Http404`.
- **Composite component inside a form.** `markdown_preview` renders the
  current draft body through the same Python helper that powers the
  public article view.
- **Co-located static.** Every `page.css` and `component.css` lives next
  to its template. The `{% collect_styles %}` and `{% collect_scripts %}`
  tags emit them once each per request.
- **Slug reservations.** `Article.clean()` plus form-level `clean_slug`
  reject the prefixes used by file routes (`docs`, `articles`, `search`,
  `wiki`).

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

## Key ideas

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

## Tests

`tests/test_e2e.py` covers eight scenarios end-to-end.

| Class | Coverage |
|-------|----------|
| `TestIndex` | Index lists both file pages and articles. |
| `TestFileDocs` | Each file documentation page renders through the layout. |
| `TestArticleCreation` | Create form publishes a new `/wiki/<slug>/` URL. |
| `TestArticleEdit` | Edit form replaces the persisted body. |
| `TestArticleDeletion` | Delete removes the dynamic URL. |
| `TestSearch` | Search returns matches from both stores. |
| `TestRouterReloadSignal` | `router_reloaded` fires on save and delete. |
| `TestValidationPreservesPreview` | Reserved slug re-renders with a live preview. |

Run the suite with:

```bash
uv run pytest
```

## Forward-compat

- **Suspense.** The article view splits cleanly across two context
  callables (`article` and `rendered_html`). When async lands, the
  Markdown render becomes the obvious place to await without touching
  the page module shape.
- **Partial rerender.** The forms already follow the redirect-after-post
  pattern. The validation re-render uses `_next_form_page` which is the
  same hook a partial fragment would target.
- **Native React.** The Markdown preview is a small JavaScript bundle
  that listens to a textarea. Replacing it with a React island fits in
  the same `component.js` file without touching the page module.

## Further reading

- [next/urls/manager.py](../../next/urls/manager.py) — `RouterManager.reload`
  emits the `router_reloaded` signal.
- [next/urls/backends.py](../../next/urls/backends.py) —
  `FileRouterBackend.generate_urls` is the public extension surface.
- [next/deps/providers.py](../../next/deps/providers.py) — DI provider
  contract used by `ArticleProvider`.
- [next/components/context.py](../../next/components/context.py) —
  `@component.context` wiring used by `markdown_preview`.
