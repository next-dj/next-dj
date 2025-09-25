# next.dj Documentation

This directory contains the documentation for the next.dj framework, optimized for Read the Docs hosting.

## Structure

```
docs/
├── content/                    # Documentation content
│   ├── getting-started/        # Installation and setup
│   │   ├── index.rst
│   │   └── installation.rst
│   ├── core-features/          # Main framework features
│   │   ├── index.rst
│   │   ├── file-router.rst
│   │   ├── templates-layouts.rst
│   │   └── context-system.rst
│   ├── api/                    # API reference
│   │   ├── index.rst
│   │   ├── api.rst
│   │   ├── api/pages.rst
│   │   ├── api/urls.rst
│   │   └── api/checks.rst
│   └── development/            # Development guidelines
│       ├── index.rst
│       ├── contributing.rst
│       └── documentation-guide.rst
├── _static/                    # Static assets
├── _build/                     # Generated documentation
├── conf.py                     # Sphinx configuration
├── requirements.txt            # Python dependencies
├── index.rst                   # Main documentation index
└── README.md
```

## Building Documentation

### From Project Root

```bash
# Build documentation
make docs

# Build and serve documentation
make docs-serve

# Clean documentation build
make docs-clean

# Check links
make docs-linkcheck

# Check spelling
make docs-spelling
```

## Writing Documentation

See the [Documentation Guide](content/development/documentation-guide.rst) for guidelines on writing and maintaining documentation.

## Contributing

When adding or modifying documentation:

1. Follow the structure outlined in the documentation guide
2. Test your changes locally with `make docs`
3. Check for broken links with `make docs-linkcheck`
4. Ensure proper RST formatting
5. Update the table of contents if needed
6. Verify Read the Docs compatibility
