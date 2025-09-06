# Pages Application Example

This example demonstrates a complete Django application using next-dj's file-based routing system, showcasing real-world usage patterns including database models, admin integration, and advanced template features.

## What This Example Demonstrates

This example showcases next-dj in a realistic Django application context, demonstrating:

- **Database integration** with Django models and admin interface
- **Template inheritance** and advanced template features
- **Context management** with database queries and complex data
- **URL parameter handling** for dynamic content display
- **Template.djx files** as an alternative to Python template strings
- **Real-world application structure** with proper Django app organization

The example creates a simple catalog application that displays products with dynamic routing and template rendering.

## Example Structure

```
pages/
├── config/               # Django project configuration
│   ├── settings.py       # Project settings with next-dj configuration
│   └── urls.py           # Main URL configuration
├── catalog/              # Django application
│   ├── models.py         # Product model definition
│   ├── admin.py          # Django admin configuration
│   ├── migrations/       # Database migrations
│   └── pages/            # Pages directory
│       ├── page.py       # Main catalog page
│       ├── template.djx  # Template file (alternative to Python strings)
│       └── catalog/      # Nested pages
│           ├── page.py   # Catalog listing page
│           └── [int:id]/ # Dynamic product detail page
│               └── page.py
└── db.sqlite3            # SQLite database (auto-generated)
```

### Application Features

**Product Model** (`catalog/models.py`):
- Defines a simple Product model with name, description
- Includes Django admin integration for easy content management
- Demonstrates database integration with file-based routing

**Main Catalog Page** (`catalog/pages/page.py`):
- Creates URL: `/catalog/`
- Uses `template.djx` file for template definition
- Shows context function registration for database queries
- Demonstrates template.djx usage as alternative to Python strings

**Product Listing** (`catalog/pages/catalog/page.py`):
- Creates URL: `/catalog/catalog/`
- Displays all products in a list format
- Shows context management with database queries
- Demonstrates nested page structure

**Product Detail** (`catalog/pages/catalog/[int:id]/page.py`):
- Creates URL: `/catalog/catalog/<int:id>/`
- Displays individual product details
- Shows parameterized routing with database lookups
- Demonstrates error handling for non-existent products

## How It Works

The example demonstrates next-dj's integration with Django's full feature set:

1. **Model Integration**: Pages can query Django models and pass data to templates
2. **Template.djx Files**: Templates can be defined in separate `.djx` files instead of Python strings
3. **Context Functions**: Pages register context functions that provide data to templates
4. **Parameter Handling**: URL parameters are automatically passed to view functions
5. **Database Queries**: Pages can perform database operations and handle results
6. **Error Handling**: Pages can handle errors gracefully (e.g., product not found)

### Template.djx Usage

The example shows how to use `template.djx` files:

```html
<!-- catalog/pages/template.djx -->
<h1>{{ catalog.title }}</h1>
<p>{{ catalog.description }}</p>
<ul>
{% for product in catalog.products %}
    <li>
        <a href="{% url 'page_catalog_catalog_int_id' product.id %}">
            {{ product.name }} - ${{ product.price }}
        </a>
    </li>
{% endfor %}
</ul>
```

This approach separates template logic from Python code, making templates easier to edit and maintain.

## Running the Example

### Prerequisites

- Python 3.8+
- Django 4.0+
- next-dj package installed

### Setup

1. Navigate to the example directory:
   ```bash
   cd examples/pages
   ```

2. Install dependencies:
   ```bash
   pip install django next-dj
   ```

3. Run migrations:
   ```bash
   python manage.py migrate
   ```

4. Create sample data:
   ```bash
   python manage.py shell
   ```
   ```python
   from catalog.models import Product
   Product.objects.create(name="Laptop", description="High-performance laptop")
   Product.objects.create(name="Mouse", description="Wireless mouse")
   Product.objects.create(name="Keyboard", description="Mechanical keyboard")
   exit()
   ```

### Running the Server

Start the Django development server:
```bash
python manage.py runserver
```

### Testing the Application

1. **Main catalog page**: http://127.0.0.1:8000/
2. **Product listing**: http://127.0.0.1:8000/catalog/
3. **Product details**: 
   - http://127.0.0.1:8000/catalog/1/
   - http://127.0.0.1:8000/catalog/2/
   - http://127.0.0.1:8000/catalog/3/

### Admin Interface

Access the Django admin to manage products:
1. Create a superuser: `python manage.py createsuperuser`
2. Visit: http://127.0.0.1:8000/admin/
3. Login and manage products in the Catalog section

### Verification

1. **Check URL patterns**: Run `python manage.py show_urls` to see generated patterns (You need to install an extra package `django-extensions`)
2. **Test template rendering**: Verify that templates render with database data
3. **Test parameter handling**: Try different product IDs in the detail URLs
4. **Test error handling**: Try accessing non-existent product IDs
5. **Verify admin integration**: Add/edit products through the admin interface

## Contributing

This example is part of the next-dj project. If you find issues or have suggestions for improvement, please:

1. Check the main project repository for existing issues
2. Create a new issue with detailed description
3. Follow the project's contribution guidelines
4. Ensure any changes maintain backward compatibility

For more information about next-dj, visit the main project documentation.
