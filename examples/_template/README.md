# `_template` — starter scaffold for a next.dj example

The skeleton copied for every new example in this repository. It runs and its test passes, but it demonstrates nothing on its own. Copy the folder, work through the rename checklist, then fill in the feature you want to show.

## Layout

Two page roots feed one file router. `chrome/` is the project-level root listed in `PAGE_BACKENDS["DIRS"]`, and its `layout.djx` is the outermost HTML envelope wrapped around every page. `myapp/routes/` is the per-app root the router finds through `APP_DIRS=True`, and it holds the pages themselves. Components resolve the same way, from the app's `_widgets/` and from the shared kit in [`../_shared/_components/`](../_shared/_components/) listed in `COMPONENT_BACKENDS["DIRS"]`. `STATICFILES_DIRS` picks up `../_shared/static` alongside the example's own `static/`, which is how the shadcn palette and its tokens arrive.

## Rename checklist

The scaffold ships deliberately generic names. Each rename touches more than one file, so change them together.

| Rename | Also update |
| --- | --- |
| `myapp/` | `INSTALLED_APPS` in [`config/settings.py`](config/settings.py), and `next_pages` in [`pytest.ini`](pytest.ini) |
| `myapp/routes/` | `PAGES_DIR` in `PAGE_BACKENDS`, and `next_pages` in `pytest.ini` |
| `myapp/routes/_widgets/` | `COMPONENTS_DIR` in `COMPONENT_BACKENDS` |
| `chrome/` | `PAGE_BACKENDS["DIRS"]` |

Pick names that fit the domain rather than reusing `routes` and `_widgets`. Every example renames both, which is what shows the naming is yours and not the framework's.

## Conventions

- Tailwind loads from the Play CDN through the shared `page_head` component. No build step, no Node.
- `pytest.ini` is the whole test scaffold. `addopts` opts into the framework's pytest plugin with `-p next.testing.plugin`, `next_pages` points it at the page tree it imports once per session so the router is populated before the first request, `next_clear_cache` empties the cache between tests, and the `next_client` fixture is the test client that speaks the partial protocol.
- `PARTIAL_BACKENDS` pins an explicit asset `VERSION`, and every example inherits that line from here. The [examples README](../README.md#conventions-every-example-follows) explains why it is pinned and what `next.W069` checks.
- Every file is intentionally short. Fill in what you need, drop what you do not.

## How to run

```bash
cd examples/_template
uv run python manage.py migrate
uv run python manage.py runserver
uv run pytest
```

The smoke test in `tests/test_integration.py` fetches `/` and asserts the welcome banner renders. Keep it green while you fill the scaffold in, then grow it into the example's own suite. Every example is gated at 100% coverage by `make test-examples`.

## Further reading

- [`../README.md`](../README.md) — the catalog of finished examples and the conventions they share.
- [`../shortener/README.md`](../shortener/README.md) — the walkthrough example to read first. It covers routing, context callables, forms, and components end to end.
