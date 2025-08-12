# File Routing Example

This example demonstrates how to use the Next framework's file-based routing system in a Django application.

## Features

- **Automatic URL generation** from `page.py` files in `pages/` directories
- **Dynamic parameter support** with `[param]` and `[type:param]` syntax
- **Args support** with `[[args]]` syntax for catch-all routes
- **Django-style configuration** with `NEXT_PAGES` setting
- **Customizable pages directory** names
- **App-level and root-level** pages support

## Configuration

### NEXT_PAGES Setting

The `NEXT_PAGES` setting in `config/settings.py` allows you to configure how the file router works:

```python
NEXT_PAGES = [
    {
        'BACKEND': 'next.urls.FileRouterBackend',
        'APP_DIRS': True,  # Look in Django apps
        'OPTIONS': {
            'PAGES_DIR_NAME': 'pages',  # Custom pages directory name
        },
    },
    {
        'BACKEND': 'next.urls.FileRouterBackend',
        'APP_DIRS': False,  # Only look in root directory
        'OPTIONS': {
            'PAGES_DIR_NAME': 'root_pages',
        },
    },
]
```

#### Configuration Options

- **BACKEND**: Currently only supports `'next.urls.FileRouterBackend'`
- **APP_DIRS**: 
  - `True`: Look for pages in Django apps (default)
  - `False`: Only look for pages in root directory (like static/media)
- **OPTIONS**: 
  - **PAGES_DIR_NAME**: Custom name for pages directory (default: `'pages'`)

### Default Configuration

If `NEXT_PAGES` is not specified, the framework uses this default:

```python
NEXT_PAGES = [
    {
        'BACKEND': 'next.urls.FileRouterBackend',
        'APP_DIRS': True,
        'OPTIONS': {},
    },
]
```

## URL Patterns

### Simple Pages

```
myapp/pages/home/page.py → /home/
myapp/pages/about/page.py → /about/
```

### Dynamic Parameters

```
myapp/pages/profile/[id]/page.py → /profile/<str:id>/
myapp/pages/posts/[int:post-id]/page.py → /posts/<int:post_id>/
myapp/pages/users/[uuid:user-id]/page.py → /users/<uuid:user_id>/
```

### Args (Catch-all)

```
myapp/pages/blog/[[args]]/page.py → /blog/<path:args>/
```

### Mixed Patterns

```
myapp/pages/user/[user-id]/posts/[[args]]/page.py → /user/<str:user_id>/posts/<path:args>/
```

## Page Structure

Each `page.py` file must contain a `render` function:

```python
# myapp/pages/home/page.py
def render(request):
    return {"message": "Hello from home page"}

# myapp/pages/profile/[id]/page.py
def render(request, id=None, **kwargs):
    return {"user_id": id, "message": f"Profile for user {id}"}

# myapp/pages/blog/[[args]]/page.py
def render(request, args=None, **kwargs):
    return {"path": args, "message": f"Blog post at {args}"}
```

## Directory Structure

```
myapp/
├── __init__.py
├── pages/
│   ├── home/
│   │   └── page.py
│   ├── profile/
│   │   └── [id]/
│   │       └── page.py
│   └── blog/
│       └── [[args]]/
│           └── page.py
```

## Usage

### Include All Pages

```python
# config/urls.py
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('next.urls')),  # Auto-includes all configured pages
]
```

### Include Specific App Pages

```python
from next.urls import include_pages

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include_pages('myapp')),
]
```

## Examples

This example includes several page types:

- **Simple page**: `/simple/` - Basic page without parameters
- **Dynamic page**: `/kwargs/[int:post-id]/` - Page with typed parameter  
- **Args page**: `/args/[[args]]/` - Page with catch-all route
- **Root page**: `/home/` - Page from root-level pages directory

The example demonstrates both app-level pages (from `myapp/pages/`) and root-level pages (from `root_pages/`).

## Running the Example

1. Install dependencies:
   ```bash
   pip install django
   ```

2. Run migrations:
   ```bash
   python manage.py migrate
   ```

3. Start the development server:
   ```bash
   python manage.py runserver
   ```

4. Visit the pages:
   - http://localhost:8000/simple/
   - http://localhost:8000/kwargs/123/
   - http://localhost:8000/args/any/nested/path/

## Customization

### Custom Pages Directory Name

To use a different directory name than `pages`:

```python
NEXT_PAGES = [
    {
        'BACKEND': 'next.urls.FileRouterBackend',
        'APP_DIRS': True,
        'OPTIONS': {
            'PAGES_DIR_NAME': 'views',  # Use 'views' instead of 'pages'
        },
    },
]
```

### Root-Only Pages

To only look for pages in the project root (like static files):

```python
NEXT_PAGES = [
    {
        'BACKEND': 'next.urls.FileRouterBackend',
        'APP_DIRS': False,  # Don't look in Django apps
        'OPTIONS': {
            'PAGES_DIR_NAME': 'root_pages',
        },
    },
]
```

This would look for pages in `BASE_DIR/root_pages/` instead of app directories.
