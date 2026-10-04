# `_template` — starter scaffold for a next.dj example

The skeleton copied for every new example in this repository. It runs and its test passes, but it demonstrates nothing on its own. Copy the folder, work through the rename checklist, then fill in the feature you want to show.

## Layout

Two page roots feed one file router. `chrome/` is the project-level root listed in `PAGE_BACKENDS["DIRS"]`, and its `layout.djx` is the outermost HTML envelope wrapped around every page. `myapp/routes/` is the per-app root the router finds through `APP_DIRS=True`, and it holds the pages themselves. Components resolve the same way, from the app's `_widgets/` and from the shared kit in [`../_shared/_components/`](../_shared/_components/) listed in `COMPONENT_BACKENDS["DIRS"]`. `STATICFILES_DIRS` picks up `../_shared/static` alongside the example's own `static/`, which is how the shadcn palette and its tokens arrive. The tab icon is the app static file `myapp/static/myapp/icon.svg`, declared under `icons` in `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]`, and the head renders it as `<link rel="icon">`.

## Rename checklist

The scaffold ships deliberately generic names. Each rename touches more than one file, so change them together.

| Rename | Also update |
| --- | --- |
| `myapp/` | `INSTALLED_APPS` in [`config/settings.py`](config/settings.py), and `next_pages` in [`pytest.ini`](pytest.ini) |
| `myapp/routes/` | `PAGES_DIR` in `PAGE_BACKENDS`, and `next_pages` in `pytest.ini` |
| `myapp/routes/_widgets/` | `COMPONENTS_DIR` in `COMPONENT_BACKENDS` |
| `chrome/` | `PAGE_BACKENDS["DIRS"]` |
| `next.dj template`, `https://template.example` and `Next.dj example template` | `NAME` and `URL` under `NEXT_FRAMEWORK["SITE"]`, and the `title` default under `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` in `config/settings.py`, the tab title of every page that declares none |
| The site `description` | `description` under `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]`, the search snippet of every page that declares none |

Pick names that fit the domain rather than reusing `routes` and `_widgets`. Every example renames both, which is what shows the naming is yours and not the framework's.

## Conventions

- Tailwind loads from the Play CDN through the shared `page_head` component. No build step, no Node.
- The tab title is metadata, not a prop. `page_head` renders `{% metadata %}`, which folds `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` with whatever each `page.py` declares as `metadata = {...}` or under `@page.metadata`. `NEXT_FRAMEWORK["SITE"]` names the site and its origin, `NAME` fills `{site_name}` and `URL` is what canonical, Open Graph and icon links are absolute on. The defaults hold the viewport, the tab icon, a site description and a `title` template with its default, so every page shows the default until it declares a title of its own, which then renders as `{title} · {site_name}`. `INDEXABLE` is left at `"auto"`, so the dev server with `DEBUG = True` renders every page `noindex` and a deploy renders them indexable. `uv run python manage.py check --deploy --tag seo` passes on the scaffold as shipped, keep it that way as pages arrive.
- Project-local assets are spelled as staticfiles names, never as `/static/...` literals. `page_head` registers `shared/css/tokens.css`, `shared/css/base.css` and `shared/js/base.mjs` through `{% use_style %}` and `{% use_module %}`. A name follows `STATIC_URL` and carries the storage digest, a literal path does neither. The icon under `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` is the one literal path, because settings load before staticfiles can resolve a name.
- `pytest.ini` is the whole test scaffold. `addopts` opts into the framework's pytest plugin with `-p next.testing.plugin`, `next_pages` points it at the page tree it imports once per session so the router is populated before the first request, `next_clear_cache` empties the cache between tests, and the `next_client` fixture is the test client that speaks the partial protocol.
- `STATIC_VERSION` names the deploy stamp, and every example serving its assets off disk inherits that line from here. The tag lands on every asset URL as a `v` parameter and on every partial response as the asset version, and the [examples README](../README.md#conventions-every-example-follows) explains what it guards, when to bump it, and which examples derive it from something else.
- Every file is intentionally short. Fill in what you need, drop what you do not.

## How to run

```bash
cd examples/_template
uv run python manage.py migrate
uv run python manage.py runserver
uv run pytest
```

The smoke tests in `tests/test_integration.py` fetch `/`, assert the welcome banner renders, read the head back through `next.testing.assert_metadata`, and check that every asset name resolves to a `/static/...` URL. Keep it green while you fill the scaffold in, then grow it into the example's own suite. Every example is gated at 100% coverage by `make test-examples`.

## Further reading

- [`../README.md`](../README.md) — the catalog of finished examples and the conventions they share.
- [`../shortener/README.md`](../shortener/README.md) — the walkthrough example to read first. It covers routing, context callables, forms, and components end to end.
