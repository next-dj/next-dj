# `_template`: starter scaffold for a next.dj example

The skeleton copied for every new example in this repository. It runs and its tests pass, but it demonstrates nothing beyond the starter itself. Copy the folder, work through the rename checklist, then fill in the feature you want to show.

## Where it comes from

The scaffold starts from Django's own commands, run from an empty folder:

```bash
uv run django-admin startproject config .
uv run python manage.py startapp home
uv run python manage.py startapp myapp
```

next.dj then joins in canonical file names. `config/settings.py` lists `"next"`, `home` and `myapp` in `INSTALLED_APPS` and holds `NEXT_FRAMEWORK`, `config/urls.py` mounts the `myapp` tree under `myapp/` through `pages()` and includes `next.urls` at the root for the rest, and `chrome/` is the project-level page root. `home` ships the public welcome page in `home/pages/`. `myapp` ships an `Item` model with `ItemQuerySet.visible_to`, `create_item` and `rename_item` in `services.py`, an `item_created` signal in `signals.py`, object permissions in `permissions.py`, an abstract `ItemForm` in `forms.py`, an `ItemAdmin` that writes through the services, and a page tree at `/myapp/`. Pages replace the `views.py` that `startapp` writes, so it is gone, and so are the empty `models.py`, `admin.py` and `migrations/` of `home`.

On top of that the folder carries:

- The shared UI kit. `config/settings.py` puts [`../_shared/`](../_shared/) on `sys.path`, lists `../_shared/static` in `STATICFILES_DIRS` and `../_shared/_components` in `COMPONENT_BACKENDS["DIRS"]`, and [`chrome/layout.djx`](chrome/layout.djx) renders the envelope through the shared `page_head` and `app_shell` components.
- `STATIC_VERSION`, `SITE` and `METADATA` under `NEXT_FRAMEWORK`, with the tab icon shipped as `home/static/home/icon.svg`.
- `pytest.ini` and the tests under `tests/`, which replace the `tests.py` module `startapp` writes, since every example drives its pages through `next.testing.NextClient` with plain pytest.
- `@override` on `ItemAdmin.save_model`, which tells ruff the boolean positional `change` is the signature Django calls.
- This README.

## Layout

Two kinds of page roots feed one file router. `chrome/` is the project-level root listed in `PAGE_BACKENDS["DIRS"]`, and its `layout.djx` is the outermost HTML envelope wrapped around every page. `home/pages/` and `myapp/pages/` are per-app roots the router finds through `APP_DIRS=True`, and their directories spell the URL below the place [`config/urls.py`](config/urls.py) gives them, the way a Django project includes the URLconf of an app:

```python
urlpatterns = [
    path("admin/", admin.site.urls),
    path("myapp/", pages("myapp")),
    path("", include("next.urls")),
]
```

`pages("myapp")` from `next.urls` mounts the `pages/` tree of `myapp` under the prefix the project picks, so `myapp/pages/page.py` answers `/myapp/` with no `myapp/` directory repeating the prefix inside the tree. The tree leaves `include("next.urls")`, which keeps routing every other tree at the root, so `home/pages/page.py` answers `/`. The pages of a `pages()` tree are named in the namespace of their app, and `page_reverse(namespace="myapp")` and `reverse("myapp:page_")` both give `/myapp/`. `uv run python manage.py routes` prints where every page landed, with its URL name and access rule. Components resolve from `_components/` folders inside a page tree and from the shared kit.

`LoginRequiredMiddleware` closes every view by default, the pages included. The welcome page declares `page.public()`, and `/myapp/` sends an anonymous visitor to the admin sign-in. `NEXT_FRAMEWORK["LOGIN_REQUIRED"] = True` closes the page trees the same way without the middleware, as the [admin example](../admin/README.md) does, and only the middleware also closes a plain Django view the project adds beside them. A page tree reads rows through `.objects.visible_to(...)` or a function of `services.py` and writes through a form or a service, as `myapp` already does.

The app follows the canonical next.dj layout, the one every example in this repository shares:

```
myapp/
├── apps.py          # MyappConfig
├── models.py        # Item, ItemQuerySet.visible_to
├── services.py      # create_item, rename_item
├── signals.py       # item_created
├── permissions.py   # add_perm rules
├── forms.py         # abstract ItemForm
├── admin.py         # ItemAdmin over the services
└── pages/           # PAGES_DIR, the tree pages("myapp") mounts at /myapp/
    ├── page.py
    └── template.djx
```

Add a `deps.py` for DI providers and named dependencies when the example needs one, and the framework imports it at startup with no `ready()` code, like `signals.py`, `permissions.py` and `forms.py`.

## Rename checklist

The scaffold ships deliberately generic names. Each rename touches more than one file, so change them together.

| Rename | Also update |
| --- | --- |
| `myapp/` | `INSTALLED_APPS` in [`config/settings.py`](config/settings.py), the `pages("myapp")` include and its `myapp/` prefix in [`config/urls.py`](config/urls.py), `next_pages` in [`pytest.ini`](pytest.ini), the `myapp.*` permission names in `permissions.py` and `forms.py`, and the `related_name` and constraint name in `models.py` |
| `chrome/` | `PAGE_BACKENDS["DIRS"]` |
| `next.dj template`, `https://template.example` and `Next.dj example template` | `NAME` and `URL` under `NEXT_FRAMEWORK["SITE"]`, and the `title` default under `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` in `config/settings.py`, the tab title of every page that declares none |
| The site `description` | `description` under `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]`, the search snippet of every page that declares none |

## Conventions

- Tailwind loads from the Play CDN through the shared `page_head` component. No build step, no Node.
- The tab title is metadata, not a prop. `page_head` renders `{% metadata %}`, which folds `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` with whatever each `page.py` declares as `metadata = {...}` or under `@page.metadata`. `NEXT_FRAMEWORK["SITE"]` names the site and its origin, `NAME` fills `{site_name}` and `URL` is what canonical, Open Graph and icon links are absolute on. `INDEXABLE` is left at `"auto"`, so the dev server with `DEBUG = True` renders every page `noindex` and a deploy renders them indexable. `uv run python manage.py check --deploy --tag seo` passes on the scaffold as shipped, keep it that way as pages arrive.
- Project-local assets are spelled as staticfiles names, never as `/static/...` literals. `page_head` registers `shared/css/tokens.css`, `shared/css/base.css` and `shared/js/base.mjs` through `{% use_style %}` and `{% use_module %}`. The icon under `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` is the one literal path, because settings load before staticfiles can resolve a name.
- `pytest.ini` is the whole test scaffold. `testpaths` lists `tests`. `addopts` opts into the framework's pytest plugin with `-p next.testing.plugin`, `next_pages` points it at the page trees it imports once per session, `next_clear_cache` empties the cache between tests, and the `next_client` fixture is the test client that speaks the partial protocol. `tests/conftest.py` holds the users and items the tests share.
- `STATIC_VERSION` names the deploy stamp. The tag lands on every asset URL as a `v` parameter and on every partial response as the asset version, and the [examples README](../README.md#conventions-every-example-follows) explains what it guards.

## How to run

```bash
cd examples/_template
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
uv run pytest
```

`/` is public, `/myapp/` lists the items of the signed-in user and adds new ones, and `/admin/` edits them through the same services. `tests/test_integration.py` drives the welcome page, its head and asset URLs, the route map, the prefix and namespace of the `myapp` tree, the item page with its form, and the admin add and change views, and `tests/test_unit.py` covers the services, `visible_to` and the permissions. Every example is gated at 100% coverage by `make test-examples`.

## Further reading

- [`../README.md`](../README.md): the catalog of finished examples and the conventions they share.
- [`../shortener/README.md`](../shortener/README.md): the walkthrough example to read first. It covers routing, context callables, forms, and components end to end.
