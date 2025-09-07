# Layout Templates Example

This example demonstrates advanced template inheritance and layout management using next-dj's file-based routing system, showcasing how to create reusable layouts and nested template structures.

## What This Example Demonstrates

This example showcases next-dj's capabilities for building complex web applications with sophisticated template management, demonstrating:

- **Template inheritance** with nested layout structures
- **Context processors** for site-wide template variables
- **Nested page routing** with multiple layout levels
- **Bootstrap integration** for modern UI components
- **Active navigation states** with dynamic highlighting
- **Template composition** using multiple template.djx files

The example creates a Bootstrap-based documentation site with multiple layout levels and demonstrates how to build maintainable, scalable web applications using next-dj's template system.

## Example Structure

```
layouts/
├── config/                    # Django project configuration
│   ├── settings.py            # Project settings with context processors
│   └── urls.py                # Main URL configuration
├── layouts/                   # Django application
│   ├── context_processors.py  # Site-wide context processors
│   └── pages/                 # Pages directory
│       ├── layout.djx         # Main site layout template
│       ├── template.djx       # Home page content
│       ├── starter-projects/  # Nested page section
│       │   └── template.djx   # Starter projects content
│       └── guides/            # Guides section with sub-layout
│           ├── layout.djx     # Guides section layout
│           ├── template.djx   # Main guides page
│           ├── webpack/       # Individual guide pages
│           │   └── template.djx
│           ├── parcel/
│           │   └── template.djx
│           └── contributing/
│               └── template.djx
└── db.sqlite3                 # SQLite database (auto-generated)
```

### Layout Hierarchy

**Main Site Layout** (`layouts/pages/layout.djx`):
- Creates the base HTML structure with Bootstrap CSS/JS
- Includes site navigation with active state highlighting
- Provides the main content block for all pages
- Demonstrates template inheritance patterns

**Home Page** (`layouts/pages/template.djx`):
- Creates URL: `/`
- Uses the main layout template
- Shows context processor integration
- Demonstrates site-wide variable usage

**Guides Section Layout** (`layouts/pages/guides/layout.djx`):
- Creates URL: `/guides/`
- Extends the main layout with guides-specific navigation
- Shows nested layout inheritance
- Demonstrates section-specific template organization

**Individual Guide Pages**:
- `/guides/webpack/` - Webpack integration guide
- `/guides/parcel/` - Parcel integration guide  
- `/guides/contributing/` - Contributing guide
- Each uses the guides layout for consistent navigation

**Starter Projects** (`layouts/pages/starter-projects/template.djx`):
- Creates URL: `/starter-projects/`
- Uses the main layout template
- Demonstrates simple page structure

## How It Works

The example demonstrates next-dj's advanced template management capabilities:

1. **Template Inheritance**: Pages can extend layout templates using Django's template inheritance
2. **Context Processors**: Site-wide variables are provided through Django's context processor system
3. **Nested Layouts**: Multiple layout levels can be created for different sections
4. **Template Composition**: Multiple template.djx files work together to create complex layouts
5. **Active Navigation**: Dynamic highlighting based on current URL path
6. **Bootstrap Integration**: Modern UI framework integration with template system

### Context Processors

The example includes a context processor (`layouts/context_processors.py`) that adds site-wide variables:

```python
def site_info(request):
    return {
        "site_name": "next-dj layouts example",
        "site_version": "1.0.0", 
        "debug_mode": settings.DEBUG,
        "current_year": 2024,
    }
```

These variables are available in all templates without explicit context registration.

### Template Inheritance Pattern

The example uses a three-level template inheritance pattern:

1. **Base Layout** (`layout.djx`): HTML structure, CSS/JS includes, main navigation
2. **Section Layout** (`guides/layout.djx`): Section-specific navigation and content blocks
3. **Page Content** (`template.djx`): Individual page content

This pattern allows for:
- Consistent site-wide styling and navigation
- Section-specific layouts and navigation
- Individual page content without duplication

## Running the Example

### Prerequisites

- Python 3.8+
- Django 4.0+
- next-dj package installed

### Setup

1. Navigate to the example directory:
   ```bash
   cd examples/layouts
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

### Testing the Application

Visit the following URLs to see the layout system in action:

- **Home page**: http://127.0.0.1:8000/
- **Starter projects**: http://127.0.0.1:8000/starter-projects/
- **Guides section**: http://127.0.0.1:8000/guides/
- **Individual guides**:
  - http://127.0.0.1:8000/guides/webpack/
  - http://127.0.0.1:8000/guides/parcel/
  - http://127.0.0.1:8000/guides/contributing/

### Key Features to Observe

1. **Template Inheritance**: Notice how all pages share the same base layout
2. **Active Navigation**: Navigation items highlight based on current page
3. **Context Processors**: Site information appears on all pages
4. **Nested Layouts**: Guides section has its own navigation within the main layout
5. **Bootstrap Integration**: Modern, responsive design with Bootstrap components

### Verification

1. **Check URL patterns**: Run `python manage.py show_urls` to see generated patterns (You need to install an extra package `django-extensions`)
2. **Test template inheritance**: Verify consistent layout across all pages
3. **Test active navigation**: Navigate between pages and observe active states
4. **Test context processors**: Verify site-wide variables appear on all pages
5. **Test responsive design**: Resize browser window to test Bootstrap responsiveness

## Advanced Features Demonstrated

### Active Navigation Logic

Navigation items use Django template logic for active state highlighting:

```html
<a class="nav-link {% if request.path == '/' %}btn-primary{% else %}btn-outline-primary{% endif %}" href="/">
    Home
</a>
```

### Context Processor Integration

Site-wide variables are automatically available in all templates:

```html
<p>Welcome to {{ site_name }} v{{ site_version }}</p>
<p>Debug mode: {{ debug_mode|yesno:"Yes,No" }}</p>
```

## Contributing

This example is part of the next-dj project. If you find issues or have suggestions for improvement, please:

1. Check the main project repository for existing issues
2. Create a new issue with detailed description
3. Follow the project's contribution guidelines
4. Ensure any changes maintain backward compatibility

For more information about next-dj, visit the main project documentation.
