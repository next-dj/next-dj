# Components Example

This example demonstrates next-dj **components** in a small **blog** app (English UI): file-based routing, simple and composite components with **slots**, root and branch-scoped `_components`, `next.forms` for auth and posts, middleware for protected routes, and pytest.

## Feature coverage (components)

| Technique | Where |
|-----------|--------|
| Simple `.djx` | `root_components/footer.djx`, `dirs_root_note.djx` (``DIRS`` root, rendered from ``layout.djx`` so template path context exists) |
| Composite (`component.djx` only) | `post_card/`, `author_chip/` under `pages/_components/` |
| `@context("key")` + unkeyed `@context` (dict merge) | `root_components/header/component.py` |
| `render()` in `component.py` (Python HTML) | `root_components/server_time/` (nested in header) |
| Inline `component = "..."` (no `component.djx`) | `root_components/version_stamp/component.py` (in footer) |
| Branch scope (`pages/.../_components/` only under that subtree) | `pages/posts/_components/draft_banner.djx` on create/edit |
| Root `layout.djx` as call site | `root_scope_badge` (`pages/_components/`) and `dirs_root_note` (`root_components/` via ``DIRS``). Use the layout for nested `{% component %}` so ``current_template_path`` is set. |
| Slots + nested components | Home `template.djx` (`#component` / `#slot`; `author_chip` inside `post_card`) |
| Generic card + recommendations | `pages/_components/card/`, `recommendations/` (`@context` + nested `#component "card"`) |

## What This Example Demonstrates

- **Simple components** — single `.djx` file (e.g. footer).
- **Composite components** — folder with `component.djx` and/or `component.py`. `component.py` can register **component context** via `from next.components import context` and `@context` / `@context("key")` (similar to `next.pages.context`).
- **Slots** — Block `{% #component "post_card" %} ... {% #slot "title" %} ... {% /slot %} ... {% /component %}`. Void `{% component "header" %}` for components without inner markup. Props on the opening tag are string literals only; slots carry dynamic values from loops.
- **author_chip** — small composite: default circular avatar (first letter of login) via `{% #set_slot "avatar" %}`, plus a `login` slot for the username. Used inside `post_card` meta and on the post detail header.
- **Root components** (`root_components/`) are wired through ``NEXT_FRAMEWORK["DEFAULT_COMPONENT_BACKENDS"][0]["DIRS"]``. Simple **dirs_root_note** only exists to show that this tree is not under ``myapp/pages/``. Other files there (header, footer, version_stamp, server_time) are the real demo. Everything in that folder is visible from every template. **`pages/_components/`** is shared across all templates under `pages/`. **`root_scope_badge`** lives there and renders from **`layout.djx`** in the same directory as `page.py`, which matches the “root template + root `_components` subtree” case. **`pages/posts/_components/`** is only for templates under the `posts/` branch (see `draft_banner`).
- **Layout** — `layout.djx` wraps pages. Global **header** is a composite with `@context("user")`, merged branding keys, and a nested **server_time** component using `render()`.
- **Forms** — `@forms.action()` for register, login, create post, update post. ModelForm + `get_initial()` for edit.
- **Auth** — login/register pages, `LogoutView` at `/account/logout/`, middleware requiring login for `/posts/create/` and `/posts/<id>/edit/`.
- **Blog** — `Post` model, paginated list at `/`, detail with recommendations, author-only edit.

## Example Structure

```
components/
├── config/                      # Django project (settings, urls, test_settings for pytest)
│   ├── settings.py              # NEXT_FRAMEWORK, LOGIN_URL, middleware
│   ├── test_settings.py         # In-memory SQLite for tests
│   └── urls.py                  # admin, logout, include(next.urls)
├── myapp/
│   ├── models.py                # Post
│   ├── migrations/
│   ├── middleware.py            # LoginRequiredForPostEditorMiddleware
│   └── pages/
│       ├── layout.djx           # HTML shell, {% component header/footer %}
│       ├── page.py              # Home: @context page_obj + Paginator
│       ├── template.djx         # Article list + post_card slots + pagination + recommendations
│       ├── _components/
│       │   ├── root_scope_badge/  # Composite: layout.djx uses this from pages root (corner case)
│       │   ├── post_card/       # Composite: title, meta (author + date), actions
│       │   ├── card/            # Generic card (title, body, actions slots)
│       │   ├── recommendations/ # @context + nested card components
│       │   └── author_chip/     # Avatar (initial) + login slot
│       ├── account/login/       # LoginForm + @forms.action("login")
│       ├── account/register/
│       └── posts/
│           ├── _components/     # Scoped to posts/* templates only
│           │   └── draft_banner.djx
│           ├── create/
│           └── [int:id]/details/ | edit/
├── root_components/             # Registered via NEXT_FRAMEWORK DEFAULT_COMPONENT_BACKENDS DIRS
│   ├── header/                  # Composite: context + nested server_time
│   ├── server_time/             # render() only (no component.djx)
│   ├── version_stamp/           # component = "..." string only
│   ├── dirs_root_note.djx       # Simple: proves DIRS root (not under myapp/pages/)
│   └── footer.djx
├── pytest.ini
├── manage.py
└── tests/
    ├── conftest.py
    └── tests.py
```

## Main Pieces

**Post model** (`myapp/models.py`): `title`, `content`, `author` (FK User), `created_at`. `get_absolute_url()` for links.

**Home** (`myapp/pages/page.py`): `@context("page_obj")` -> `Paginator(Post.objects..., 10)`. Template lists cards and pagination.

**Detail** (`posts/[int:id]/details/page.py`): context `post`, `recommended` (up to three other posts).

**Create / edit** (`posts/create/`, `posts/[int:id]/edit/page.py`): `PostCreateForm` / `PostEditForm` with `@forms.action`. Edit context checks author (`PermissionDenied` if not author). **draft_banner** component on both forms.

**Account** (`account/login/`, `account/register/page.py`): `LoginForm`, `RegisterForm` with `@forms.action`.

**Header** (`root_components/header/`): `component.py` exposes `user` via `@context("user")`, merged `site_brand_*` via unkeyed `@context`, and embeds **server_time** (`render()` in Python).

**Footer** (`root_components/footer.djx`): includes **version_stamp** (inline template string in `component.py`).

## How It Works

1. **File router** — Directories named `_components` are skipped for URL segments (same name as `COMPONENTS_DIR` on `DEFAULT_COMPONENT_BACKENDS`).

2. **Component scope** — Templates resolve components from root dirs plus `_components` folders on the path from the pages root down to the template. A folder `pages/posts/_components/` is visible only under `pages/posts/...`, not from `pages/template.djx` at the root of the tree.

3. **Component context** — In `component.py`, use `from next.components import context` and `@context` / `@context("user")`. The API is similar to `next.pages.context` but designed specifically for components. Do not use `next.pages.context` in `component.py`.

4. **Forms** — Same pattern as the `forms` example: `{% form @action="create_post" %}`, POST to internal action URL. Edit pages include hidden `_url_param_id` from the route.

5. **Protected routes** — Middleware redirects anonymous users to `LOGIN_URL` with optional `?next=` for create/edit.

## Running the Example

### Prerequisites

- Python 3.12+
- Django 4.2+
- next-dj installed (e.g. editable from the repo root)

### Setup

```bash
cd examples/components
pip install django next-dj
python manage.py migrate
```

### Running the Server

```bash
python manage.py runserver
```

### URLs to Try

| URL | Description |
|-----|-------------|
| `/` | Post list (10 per page, `?page=`) |
| `/posts/<id>/details/` | Post + recommended posts |
| `/posts/create/` | New post (signed in). Branch-scoped banner |
| `/posts/<id>/edit/` | Edit (author only). Same banner |
| `/account/login/`, `/account/register/` | Sign in / sign up |

### Running Tests

From the repository root (use `--no-cov` to skip the main project coverage gate when running only this example):

```bash
uv run pytest examples/components/tests/ -v --no-cov
```

From `examples/components/`:

```bash
cd examples/components
uv run pytest tests/ -v --no-cov
```

`pytest.ini` sets `testpaths` and `pythonpath`. Tests use `config.test_settings` (in-memory SQLite).

## Contributing

Issues and PRs welcome via the main next-dj repository. Keep backward compatibility when changing examples.
