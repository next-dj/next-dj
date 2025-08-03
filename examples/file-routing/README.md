# File Routing Example

This example demonstrates the file-based routing system for Django applications.

## Structure

```
examples/file-routing/
├── myapp/
│   ├── __init__.py
│   ├── pages/
│   │   ├── simple/
│   │   │   └── page.py          # /simple
│   │   ├── kwargs/
│   │   │   └── [int:post-id]/
│   │   │       └── page.py      # /kwargs/123
│   │   └── args/
│   │       └── [[args]]/
│   │           └── page.py      # /args/a/b/c/d
│   └── views.py
├── config/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── manage.py
└── README.md
```

## Routes

- `/simple` - Simple page without parameters
- `/kwargs/[int:post-id]` - Page with dynamic parameter
- `/args/[[args]]` - Page with variable number of arguments
