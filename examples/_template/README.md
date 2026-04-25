# `_template` — starter scaffold for a next.dj example

This folder is not a working example. It is the canonical skeleton copied for every new example in this repository. Copy it, rename, and fill in.

## Layout checklist

```
_template/
├── README.md              # replace with your example readme
├── manage.py              # Django entry point
├── pytest.ini             # pytest config with DJANGO_SETTINGS_MODULE
├── config/                # settings, urls, wsgi, asgi
├── myapp/                 # rename to your domain app
│   ├── apps.py
│   ├── models.py          # replace Placeholder with real models
│   ├── routes/            # pages directory (renamed via NEXT_FRAMEWORK)
│   │   ├── layout.djx     # root layout (Tailwind via Play CDN)
│   │   ├── page.py        # root @context callables
│   │   ├── template.djx
│   │   └── _widgets/      # scoped components directory
│   └── (optional files)   # backends.py, providers.py, receivers.py, etc.
├── static/                # shared static (fonts, favicon)
├── conftest.py            # uses next.testing.eager_load_pages + NextClient
└── tests/
    └── test_e2e.py
```

## Conventions enforced here

* `PAGES_DIR` is set to `routes`, `COMPONENTS_DIR` is set to `_widgets`. Every example overrides both to show that the naming is user-controlled. Pick names that fit the domain.
* Tailwind is loaded via the Play CDN in `routes/layout.djx`. No build step.
* `conftest.py` uses `next.testing.eager_load_pages` and `NextClient`.
* Every file above is intentionally short. Fill in what you need, drop what you do not.

## How to run

```bash
cd examples/_template
uv run python manage.py migrate
uv run python manage.py runserver
uv run pytest
```

The default smoke test in `tests/test_e2e.py` fetches `/` and asserts the welcome banner renders.
