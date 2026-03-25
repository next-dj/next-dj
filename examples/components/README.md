# Components Example

This example demonstrates next-dj **components** in a small **blog** app (English UI): file-based routing, simple and composite components with **slots**, root vs scoped `_components`, `next.forms` for auth and posts, middleware for protected routes, and pytest.

## What This Example Demonstrates

- **Simple components** — single `.djx` file (e.g. footer).
- **Composite components** — folder with `component.djx` and optional `component.py`; `component.py` registers **component context** via `from next.components import context` and `@context("key")` decorator (similar to `next.pages.context`).
- **Slots** — `{% component "post_card" %} ... {% slot "title" %} ... {% endslot %} ...` because `{% component %}` props are string literals only; slots carry dynamic values from loops.
- **author_chip** — small composite: default circular avatar (first letter of login) via `{% set_slot "avatar" %}`, plus a `login` slot for the username; used inside `post_card` meta and on the post detail header.
- **Root components** (`root_components/`) visible from every template; **local** `_components` under `pages/` visible to that branch.
- **Layout** — `layout.djx` wraps pages; global **header** is a composite with `@context("user")` and `request.path` for active nav; `request` is still provided by the renderer for CSRF.
- **Forms** — `@forms.action()` for register, login, create post, update post; ModelForm + `get_initial()` for edit.
- **Auth** — login/register pages, `LogoutView` at `/account/logout/`, middleware requiring login for `/posts/create/` and `/posts/<id>/edit/`.
- **Blog** — `Post` model, paginated list at `/`, detail with recommendations, author-only edit.

## Example Structure

```
components/
├── config/                      # Django project (settings, urls)
│   ├── settings.py              # NEXT_PAGES, NEXT_COMPONENTS, LOGIN_URL, middleware
│   └── urls.py                  # admin, logout, include(next.urls)
├── myapp/
│   ├── models.py                # Post
│   ├── migrations/
│   ├── middleware.py            # LoginRequiredForPostEditorMiddleware
│   └── pages/
│       ├── layout.djx           # HTML shell, {% component header/footer %}
│       ├── page.py              # Home: @context page_obj + Paginator
│       ├── template.djx         # Article list + post_card slots + pagination
│       ├── _components/
│       │   ├── post_card/       # Composite: title, meta (author + date), actions
│       │   └── author_chip/     # Avatar (initial) + login slot
│       ├── account/login/       # LoginForm + @forms.action("login")
│       ├── account/register/
│       └── posts/
│           ├── create/
│           └── [int:id]/details/ | edit/
├── root_components/
│   ├── header/                  # Composite: component.djx + component.py (user context)
│   └── footer.djx
├── manage.py
└── tests/
```

## Main Pieces

**Post model** (`myapp/models.py`): `title`, `content`, `author` (FK User), `created_at`; `get_absolute_url()` for links.

**Home** (`myapp/pages/page.py`): `@context("page_obj")` → `Paginator(Post.objects..., 10)`; template lists cards and pagination.

**Detail** (`posts/[int:id]/details/page.py`): context `post`, `recommended` (up to three other posts).

**Create / edit** (`posts/create/`, `posts/[int:id]/edit/page.py`): `PostCreateForm` / `PostEditForm` with `@forms.action`; edit context checks author (`PermissionDenied` if not author).

**Account** (`account/login/`, `account/register/page.py`): `LoginForm`, `RegisterForm` with `@forms.action`.

**Header** (`root_components/header/`): `component.py` exposes `user` via `@context("user")` decorator; template uses `user.is_authenticated` and `request.path` for highlighted nav links.

## How It Works

1. **File router** — Directories named `_components` are skipped for URL segments (`COMPONENTS_DIR` in `NEXT_PAGES` and `NEXT_COMPONENTS`).

2. **Component scope** — Templates resolve components from root dirs + each ancestor `_components` along the template path.

3. **Component context** — In `component.py`, use `from next.components import context` and `@context("user")` decorator. The API is similar to `next.pages.context` but designed specifically for components. Do not use `next.pages.context` in `component.py`.

4. **Forms** — Same pattern as the `forms` example: `{% load forms %}`, `{% form @action="create_post" %}`, POST to internal action URL; edit pages include hidden `_url_param_id` from the route.

5. **Protected routes** — Middleware redirects anonymous users to `LOGIN_URL` with optional `?next=` for create/edit.

## Running the Example

### Prerequisites

- Python 3.8+
- Django 4.0+
- next-dj installed (e.g. from the repo root)

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
| `/posts/create/` | New post (signed in) |
| `/posts/<id>/edit/` | Edit (author only) |
| `/account/login/`, `/account/register/` | Sign in / sign up |

### Running Tests

From the repository root (use `--no-cov` to skip the main project coverage gate when running only this example):

```bash
uv run pytest examples/components/tests/tests.py -v --no-cov
```

## Contributing

Issues and PRs welcome via the main next-dj repository; keep backward compatibility when changing examples.
