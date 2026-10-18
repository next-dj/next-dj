# `_template`: starter scaffold for a next.dj example

The skeleton copied for every new example in this repository. It runs and its tests pass, but it demonstrates nothing beyond the starter itself. Copy the folder, work through the rename checklist, then fill in the feature you want to show.

## Where it comes from

The scaffold is the output of the two project templates next.dj ships, run from an empty folder:

```bash
uv run django-admin startproject --template="$(uv run python -c 'import next.conf; print(next.conf.__path__[0])')/project_template" config .
uv run python manage.py startapp --template="$(uv run python -c 'import next.conf; print(next.conf.__path__[0])')/app_template" myapp
```

The first command writes `manage.py`, the `config/` settings package, the project-level page root `chrome/` and the `home` app with the public welcome page. The second writes the `myapp` app: an `Item` model with `ItemQuerySet.visible_to`, `create_item` and `rename_item` in `services.py`, an `item_created` signal, object permissions in `permissions.py`, an abstract `ItemForm` in `forms.py`, an `ItemAdmin` that writes through the services, and a page tree at `/myapp/`. `myapp` is then added to `INSTALLED_APPS` by hand, as after any `startapp`.

Everything in the folder is that output, formatted by the repository's ruff, with these additions on top:

- The shared UI kit. `config/settings.py` puts [`../_shared/`](../_shared/) on `sys.path`, lists `../_shared/static` in `STATICFILES_DIRS` and `../_shared/_components` in `COMPONENT_BACKENDS["DIRS"]`, and [`chrome/layout.djx`](chrome/layout.djx) renders the envelope through the shared `page_head` and `app_shell` components.
- `STATIC_VERSION`, `SITE` and `METADATA` under `NEXT_FRAMEWORK`, with the tab icon shipped as `home/static/home/icon.svg`.
- `pytest.ini` and the tests under `tests/`, which replace the Django `TestCase` module the app template writes into `myapp/tests/`, since every example drives its pages through `next.testing.NextClient` with plain pytest.
- `@override` on `ItemAdmin.save_model`, which tells ruff the boolean positional `change` is the signature Django calls.
- This README.

## Layout

Two kinds of page roots feed one file router. `chrome/` is the project-level root listed in `PAGE_BACKENDS["DIRS"]`, and its `layout.djx` is the outermost HTML envelope wrapped around every page. `home/pages/` and `myapp/pages/` are per-app roots the router finds through `APP_DIRS=True`, each mounted at the prefix its `AppConfig.mount` declares, `""` for `home` and `"myapp/"` for `myapp`. `uv run python manage.py next routes` prints where every page landed. Components resolve from `_components/` folders inside a page tree and from the shared kit.

`LoginRequiredMiddleware` closes every page by default. The welcome page declares `page.public()`, and `/myapp/` sends an anonymous visitor to the admin sign-in. `NEXT_FRAMEWORK["BOUNDARIES"] = "strict"` makes `manage.py check --tag boundaries` fail on any boundary finding, so a page tree reads rows through `.objects.visible_to(...)` or a function of `services.py` and writes through a form or a service, as `myapp` already does. Without the key the boundary checks stay silent, and a plain `check` or `runserver` never runs them. [`home/tests/test_boundaries.py`](home/tests/test_boundaries.py) calls `check --tag boundaries --fail-level WARNING` under the test suite, the same test `startproject` writes, and `tests/test_template_sync.py` keeps `home/` in step with the project template.

The app follows the canonical next.dj layout, the one every example in this repository shares:

```
myapp/
├── apps.py          # mount
├── models.py        # Item, ItemQuerySet.visible_to
├── services.py      # create_item, rename_item
├── signals.py       # item_created
├── permissions.py   # add_perm predicates
├── forms.py         # abstract ItemForm
├── admin.py         # ItemAdmin over the services
└── pages/           # PAGES_DIR, the page tree
    ├── page.py
    └── template.djx
```

Add a `deps.py` for DI providers and named dependencies when the example needs one, and the framework imports it at startup with no `ready()` code, like `signals.py`, `permissions.py` and `forms.py`.

## Rename checklist

The scaffold ships deliberately generic names. Each rename touches more than one file, so change them together.

| Rename | Also update |
| --- | --- |
| `myapp/` | `INSTALLED_APPS` in [`config/settings.py`](config/settings.py), `next_pages` in [`pytest.ini`](pytest.ini), the `myapp.*` permission names in `permissions.py` and `forms.py`, and the `related_name` and constraint name in `models.py` |
| `chrome/` | `PAGE_BACKENDS["DIRS"]` |
| `next.dj template`, `https://template.example` and `Next.dj example template` | `NAME` and `URL` under `NEXT_FRAMEWORK["SITE"]`, and the `title` default under `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` in `config/settings.py`, the tab title of every page that declares none |
| The site `description` | `description` under `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]`, the search snippet of every page that declares none |

## Conventions

- Tailwind loads from the Play CDN through the shared `page_head` component. No build step, no Node.
- The tab title is metadata, not a prop. `page_head` renders `{% metadata %}`, which folds `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` with whatever each `page.py` declares as `metadata = {...}` or under `@page.metadata`. `NEXT_FRAMEWORK["SITE"]` names the site and its origin, `NAME` fills `{site_name}` and `URL` is what canonical, Open Graph and icon links are absolute on. `INDEXABLE` is left at `"auto"`, so the dev server with `DEBUG = True` renders every page `noindex` and a deploy renders them indexable. `uv run python manage.py check --deploy --tag seo` passes on the scaffold as shipped, keep it that way as pages arrive.
- Project-local assets are spelled as staticfiles names, never as `/static/...` literals. `page_head` registers `shared/css/tokens.css`, `shared/css/base.css` and `shared/js/base.mjs` through `{% use_style %}` and `{% use_module %}`. The icon under `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` is the one literal path, because settings load before staticfiles can resolve a name.
- `pytest.ini` is the whole test scaffold. `testpaths` lists `tests` and `home/tests`, so `uv run pytest` collects the boundary test of `home` too. `addopts` opts into the framework's pytest plugin with `-p next.testing.plugin`, `next_pages` points it at the page trees it imports once per session, `next_clear_cache` empties the cache between tests, and the `next_client` fixture is the test client that speaks the partial protocol. `tests/conftest.py` holds the users and items the tests share.
- `STATIC_VERSION` names the deploy stamp. The tag lands on every asset URL as a `v` parameter and on every partial response as the asset version, and the [examples README](../README.md#conventions-every-example-follows) explains what it guards.

## How to run

```bash
cd examples/_template
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
uv run pytest
```

`/` is public, `/myapp/` lists the items of the signed-in user and adds new ones, and `/admin/` edits them through the same services. `tests/test_integration.py` drives the welcome page, its head and asset URLs, the route map, the item page with its form, and the admin add and change views, and `tests/test_unit.py` covers the services, `visible_to` and the permissions. Every example is gated at 100% coverage by `make test-examples`.

## Further reading

- [`../README.md`](../README.md): the catalog of finished examples and the conventions they share.
- [`../shortener/README.md`](../shortener/README.md): the walkthrough example to read first. It covers routing, context callables, forms, and components end to end.
