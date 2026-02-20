# Forms Example

This example demonstrates next-dj's form handling system integrated with file-based routing: form actions, ModelForm with create/edit flows, and the `{% form %}` template tag.

## What This Example Demonstrates

This example showcases next-dj's form capabilities in a small Todo app:

- **Form actions** registered with `@forms.action()` for create and update
- **ModelForm** with `get_initial()` for new (empty) vs edit (instance) flows
- **Template tag** `{% form @action="..." %}` with automatic CSRF and action URL
- **Context from routing** — URL parameter `id` passed into edit page context and into form handler
- **Django messages** for success feedback after create/update
- **Layout** with shared nav and messages block

The app provides a todo list at `/`, create form on the same page, and edit at `/edit/<id>/`.

## Example Structure

```
forms/
├── config/                      # Django project configuration
│   ├── settings.py              # Project settings with NEXT_PAGES
│   └── urls.py                  # Main URL configuration (next.urls at root)
├── todos/                       # Django application
│   ├── models.py                # Todo model
│   ├── migrations/
│   └── pages/                   # Pages directory
│       ├── layout.djx           # Base layout (nav, messages, block)
│       ├── page.py              # Home: list + create form
│       ├── template.djx         # Home template (list + {% form %} for create)
│       └── edit/
│           └── [int:id]/        # Edit page with URL parameter
│               ├── page.py     # Edit: context todo, update form action
│               └── template.djx
└── db.sqlite3                   # SQLite database (auto-generated)
```

### Main Pieces

**Todo model** (`todos/models.py`):
- `title`, `description`, `is_completed`, `created_at`, `updated_at`
- Used by both create and edit forms

**Home page** (`todos/pages/page.py`):
- **URL:** `/`
- **Context:** `@context("todos")` → `get_todos()` returns all todos
- **Form action:** `@forms.action("create_todo", form_class=TodoForm)` → `create_todo_handler` saves and redirects to `/`
- **Form:** `TodoForm` with `get_initial()` returning `{}` for new todos

**Edit page** (`todos/pages/edit/[int:id]/page.py`):
- **URL:** `/edit/<id>/`
- **Context:** `@context("todo")` → `get_todo(request, id)` returns one todo or 404
- **Form action:** `@forms.action("update_todo", form_class=TodoEditForm)` → `update_todo_handler` saves and redirects to `/`
- **Form:** `TodoEditForm` with `get_initial(request, id)` returning the `Todo` instance so ModelForm gets `instance=...` for editing; URL param `id` is passed from the route

**Templates:**
- `layout.djx`: HTML shell, nav (active state for `/`), Django messages, `{% block template %}`
- Home `template.djx`: `{% load forms %}`, `{% form @action="create_todo" %}`, list of todos with links to `/edit/{{ todo.id }}/`
- Edit `template.djx`: `{% form @action="update_todo" %}` with `{{ form.as_p }}`

## How It Works

1. **Form actions** — Handlers are registered with `@forms.action("name", form_class=FormClass)`. The name is used in templates as `{% form @action="name" %}`. POST goes to an internal action URL; next-dj dispatches to the handler and passes the validated form (and URL kwargs for parameterized pages).

2. **ModelForm and get_initial** — For create, `get_initial(request)` returns `{}`. For edit, `get_initial(request, id, **kwargs)` returns the `Todo` instance; the form is built with `instance=...`, so saving updates the same record. URL parameter `id` comes from the file route `[int:id]`.

3. **Template tag** — `{% load forms %}` and `{% form @action="create_todo" %} ... {% endform %}` render a `<form method="post">` with the correct action URL, CSRF token, and (on edit) hidden field for `id` (e.g. `_url_param_id`). Inside the tag, `form` is the form instance (e.g. `{{ form.as_p }}`).

4. **Context** — List page uses `get_todos()`; edit page uses `get_todo(request, id)` so the template can show the todo and the form is pre-filled via `get_initial`.

5. **Messages** — After create/update, handlers call `messages.success(request, "...")` and redirect to `/`; the layout shows messages in the block above the content.

## Running the Example

### Prerequisites

- Python 3.8+
- Django 4.0+
- next-dj package installed

### Setup

1. Navigate to the example directory:
   ```bash
   cd examples/forms
   ```

2. Install dependencies:
   ```bash
   pip install django next-dj
   ```

3. Run migrations:
   ```bash
   python manage.py migrate
   ```

### Running the Server

Start the Django development server:
```bash
python manage.py runserver
```

### Testing the Application

- **Home (list + create):** http://127.0.0.1:8000/
- **Edit todo:** http://127.0.0.1:8000/edit/1/ (use an id that exists)

Try creating a todo on the home page, then opening it from the list and updating it on the edit page. You should see success messages after each submit.

### Running Tests

From the example directory:
```bash
pytest
```

Tests cover: home 200 and empty state, create todo (POST to action URL), list shows todos, edit page 200 and initial data, update todo (POST with `_url_param_id`), edit 404 for missing id, and module/context/action registration.

## Key Code Snippets

### Registering a form action (create)

```python
from next import forms
from next.pages import context

@context("todos")
def get_todos(_request):
    return list(Todo.objects.all())

class TodoForm(forms.ModelForm):
    class Meta:
        model = Todo
        fields = ["title", "description", "is_completed"]

    @classmethod
    def get_initial(cls, _request):
        return {}

@forms.action("create_todo", form_class=TodoForm)
def create_todo_handler(request, form):
    form.save()
    messages.success(request, "Todo created successfully.")
    return HttpResponseRedirect("/")
```

### Edit page: get_initial with URL parameter

```python
@context("todo")
def get_todo(_request, id, **_kwargs):
    return Todo.objects.get(pk=id)  # or raise Http404

class TodoEditForm(forms.ModelForm):
    ...

    @classmethod
    def get_initial(cls, _request, id, **_kwargs):
        try:
            return Todo.objects.get(pk=id)  # instance for ModelForm
        except Todo.DoesNotExist:
            return {}

@forms.action("update_todo", form_class=TodoEditForm)
def update_todo_handler(request, form, **_kwargs):
    form.save()
    messages.success(request, "Todo updated successfully.")
    return HttpResponseRedirect("/")
```

### Using the form in a template

```html
{% load forms %}

{% form @action="create_todo" %}
    {{ form.as_p }}
    <button type="submit">Create Todo</button>
{% endform %}
```

## Contributing

This example is part of the next-dj project. If you find issues or have suggestions for improvement, please:

1. Check the main project repository for existing issues
2. Create a new issue with detailed description
3. Follow the project's contribution guidelines
4. Ensure any changes maintain backward compatibility

For more information about next-dj, visit the main project documentation.
