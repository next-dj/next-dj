# `_template` — starter scaffold for a next.dj example

This folder is not a working example. It is the canonical skeleton copied for every new example in this repository. Copy it, rename, and fill in.

## Conventions

* `PAGES_DIR` is set to `routes`, `COMPONENTS_DIR` is set to `_widgets`. Every example overrides both to show that the naming is user-controlled. Pick names that fit the domain.
* Tailwind is loaded via the Play CDN in the root layout. No build step.
* `conftest.py` uses `next.testing.eager_load_pages` and `NextClient`.
* Every file is intentionally short. Fill in what you need, drop what you do not.

## How to run

```bash
cd examples/_template
uv run python manage.py migrate
uv run python manage.py runserver
uv run pytest
```

The default smoke test in `tests/test_e2e.py` fetches `/` and asserts the welcome banner renders.
