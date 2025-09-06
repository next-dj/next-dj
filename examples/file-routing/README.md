# File-based Routing Example

This example demonstrates the core file-based routing functionality of next-dj, showing how to create dynamic URL patterns from file system structure without writing traditional Django URL configurations.

## What This Example Demonstrates

This example showcases the fundamental concept of next-dj: automatic URL pattern generation from file system structure. It demonstrates:

- **Automatic URL pattern generation** from directory structure
- **Parameterized routes** with type hints and wildcard arguments
- **Template rendering** with context management
- **Multiple routing strategies** (app-specific and root-level pages)

The example includes various page types to illustrate different routing scenarios and parameter handling approaches.

## Example Structure

```
file-routing/
├── config/               # Django project configuration
│   ├── settings.py       # Project settings with next-dj configuration
│   └── urls.py           # Main URL configuration
├── myapp/                # Django application
│   └── pages/            # Pages directory (app-specific routing)
│       ├── simple/       # Basic page without parameters
│       │   └── page.py
│       ├── kwargs/       # Page with typed parameters
│       │   └── [int:post-id]/
│       │       └── page.py
│       └── args/         # Page with wildcard arguments
│           └── [[args]]/
│               └── page.py
└── root_pages/           # Root-level pages directory
    └── home/
        └── page.py
```

### Page Examples

**Simple Page** (`myapp/pages/simple/page.py`):
- Creates URL: `/simple/`
- No parameters, basic template rendering

**Parameterized Page** (`myapp/pages/kwargs/[int:post-id]/page.py`):
- Creates URL: `/kwargs/<int:post_id>/`
- Demonstrates typed parameter handling
- Shows how to access parameters in templates

**Wildcard Page** (`myapp/pages/args/[[args]]/page.py`):
- Creates URL: `/args/<path:args>/`
- Demonstrates wildcard argument handling
- Shows flexible routing for dynamic content

**Root Page** (`root_pages/home/page.py`):
- Creates URL: `/home/`
- Demonstrates root-level page routing
- Shows configuration for non-app-specific pages

## How It Works

The example uses next-dj's file-based routing system:

1. **Directory Scanning**: The system scans configured directories for `page.py` files
2. **URL Pattern Generation**: File paths are converted to Django URL patterns using special syntax:
   - `[param]` → `<str:param>` (string parameter)
   - `[int:param]` → `<int:param>` (typed parameter)
   - `[[args]]` → `<path:args>` (wildcard arguments)
3. **Template Loading**: Each page can define templates via:
   - `template` attribute in `page.py`
   - `template.djx` file in the same directory
4. **Context Management**: Pages can register context functions for template data
5. **View Generation**: Automatic view functions handle parameter passing and template rendering

## Running the Example

### Prerequisites

- Python 3.8+
- Django 4.0+
- next-dj package installed

### Setup

1. Navigate to the example directory:
   ```bash
   cd examples/file-routing
   ```

2. Install dependencies:
   ```bash
   pip install django next-dj
   ```

3. Run migrations (if needed):
   ```bash
   python manage.py migrate
   ```

### Running the Server

Start the Django development server:
```bash
python manage.py runserver
```

### Testing the Routes

Visit the following URLs to see the file-based routing in action:

- **Simple page**: http://127.0.0.1:8000/simple/
- **Parameterized page**: http://127.0.0.1:8000/kwargs/123/
- **Wildcard page**: http://127.0.0.1:8000/args/any/path/here/
- **Root page**: http://127.0.0.1:8000/home/

### Verification

1. **Check URL patterns**: Run `python manage.py show_urls` to see generated URL patterns (You need to install an extra package `django-extensions`)
2. **Test parameter handling**: Visit parameterized URLs with different values
3. **Verify template rendering**: Check that templates render correctly with context data
4. **Test error handling**: Try invalid URLs to see error handling

## Contributing

This example is part of the next-dj project. If you find issues or have suggestions for improvement, please:

1. Check the main project repository for existing issues
2. Create a new issue with detailed description
3. Follow the project's contribution guidelines
4. Ensure any changes maintain backward compatibility

For more information about next-dj, visit the main project documentation.
