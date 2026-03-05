# Components Example

This example demonstrates next-dj's **components** feature: reusable template fragments with props and slots, scoped by page branch and root-level directories.

## What This Example Demonstrates

This example showcases next-dj's component system:

- **Simple components** — a single `.djx` file (e.g. `_components/card.djx`) with props
- **Composite components** — a folder with `component.djx` and optional `component.py` (e.g. `_components/profile/`)
- **Scope** — components in `_components` are visible only to that page branch; root-level components in `root_components/` are visible everywhere
- **Template tags** — `{% component "name" %}`, `{% slot "name" %}`, `{% set_slot "name" %}` for invoking and defining components with slots

The app has a home page and an about page. Both use a global header from `root_components/`. Home uses a simple card and a composite profile; about uses a local card only visible to about and its nested routes.

## Example Structure

```
components/
├── config/                      # Django project configuration
│   ├── settings.py              # NEXT_PAGES (COMPONENTS_DIR), NEXT_COMPONENTS
│   └── urls.py                  # Main URL configuration
├── myapp/                       # Django application
│   └── pages/                   # Pages directory
│       ├── _components/        # Components visible to all app pages
│       │   ├── card.djx         # Simple component (title, description)
│       │   └── profile/         # Composite component
│       │       ├── component.djx
│       │       └── component.py # Optional
│       ├── home/
│       │   ├── page.py
│       │   └── template.djx    # Uses header, card, profile
│       └── about/
│           ├── _components/     # Local: only visible to about and below
│           │   └── local_card.djx
│           ├── page.py
│           └── template.djx    # Uses header, local_card
├── root_components/             # Global components (visible everywhere)
│   └── header.djx
└── tests/
    ├── conftest.py              # Django settings, NEXT_COMPONENTS, client
    └── tests.py                 # Page access, component rendering, scope
```

### Main Pieces

**Settings** (`config/settings.py`):

- `NEXT_PAGES` — one backend with `OPTIONS.COMPONENTS_DIR: "_components"` so the file router skips the component folder and does not create URL segments from it
- `NEXT_COMPONENTS` — one `FileComponentsBackend` with `APP_DIRS: True` and `OPTIONS.COMPONENTS_DIRS: [root_components]` for global components

**Simple component** (`myapp/pages/_components/card.djx`):

- Receives `title` and `description` as props
- Rendered via `{% component "card" title="Post 1" description="First post" %} {% endcomponent %}`

**Composite component** (`myapp/pages/_components/profile/`):

- `component.djx` uses `{% set_slot "avatar" %} ... {% endset_slot %}` for optional slot content with a default (first letter of username)
- Can be called with or without `{% slot "avatar" %} ... {% endslot %}` in the caller

**Root component** (`root_components/header.djx`):

- Visible from every template
- Used on both home and about

**Local component** (`myapp/pages/about/_components/local_card.djx`):

- Visible only from about and any nested route (e.g. about/team)
- Not visible from home

## How It Works

1. **File router** — The directory whose name is `COMPONENTS_DIR` (default `_components`) is skipped when scanning for `page.py` and `template.djx`, so it does not create URL segments. See :doc:`file-router` and :doc:`components` in the main docs.

2. **Discovery** — The component backend scans each app’s pages tree for folders named `_components` and each root in `COMPONENTS_DIRS`. Simple components are single `.djx` files; composite components are subfolders with `component.djx` and optionally `component.py`.

3. **Scope** — For a given template path, visible components are: all root components, plus components from each `_components` folder that is an ancestor of that template’s directory. So `pages/about/team/template.djx` sees root components, `pages/_components`, and `pages/about/_components`, but not `pages/blog/_components`.

4. **Template tags** — In a page or layout template you use `{% component "name" prop="value" %} ... {% endcomponent %}`. Slots are passed with `{% slot "name" %} ... {% endslot %}` inside the component block. In the component template, `{% set_slot "name" %} default {% endset_slot %}` renders the slot content or the default.

## Running the Example

### Prerequisites

- Python 3.8+
- Django 4.0+
- next-dj package installed

### Setup

1. Navigate to the example directory:
   ```bash
   cd examples/components
   ```

2. Install dependencies:
   ```bash
   pip install django next-dj
   ```
   Or from the repo root: `uv run pytest examples/components/tests/` (no separate install needed).

### Running the Server

This example does not include a top-level `manage.py`. To run the app, copy `manage.py` from another example (e.g. `examples/file-routing/manage.py`) and set `DJANGO_SETTINGS_MODULE=config.settings`, or run the app from the repo root with the package installed. The primary way to verify the example is via tests.

### Testing the Routes (if server is running)

- **Home:** http://127.0.0.1:8000/home/ — header, card (“Post 1”, “First post”), profile (“Admin”)
- **About:** http://127.0.0.1:8000/about/ — header, local card (“About card”, “Local component”)

### Running Tests

From the example directory:

```bash
pytest tests/ -v
```

With coverage (from example dir):

```bash
uv run pytest tests/ -v --cov=. --cov-config=../.coveragerc --cov-report=term-missing
```

Tests cover: page access (home, about, 404), rendering with root/simple/composite/local components, scope (local_card only on about), header on both pages, app/config loading, and component resolution for the home template path.

## Contributing

This example is part of the next-dj project. If you find issues or have suggestions for improvement, please:

1. Check the main project repository for existing issues
2. Create a new issue with detailed description
3. Follow the project's contribution guidelines
4. Ensure any changes maintain backward compatibility

For more information about next-dj, visit the main project documentation.
