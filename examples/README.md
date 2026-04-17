# next-dj Examples

This directory contains practical examples demonstrating next-dj's file-based routing capabilities in various scenarios and use cases.

## Available Examples

### file-routing
A basic example showcasing the core file-based routing functionality with different parameter types and routing strategies.

**Key Features:**
- Simple pages without parameters
- Parameterized routes with type hints
- Wildcard argument handling
- App `pages/` trees plus extra roots via `DIRS` (e.g. `root_pages/`) in one router entry

**Best for:** Understanding the fundamentals of next-dj routing

### pages
A complete Django application example demonstrating real-world usage with database integration, admin interface, and advanced template features.

**Key Features:**
- Database model integration
- Template.djx file usage
- Context management with database queries
- Django admin integration
- Error handling patterns

**Best for:** Learning how to build production-ready applications with next-dj

### layouts
An advanced template inheritance example showcasing sophisticated layout management, context processors, and nested template structures.

**Key Features:**
- Template inheritance with nested layouts
- Context processors for site-wide variables
- Multi-level template composition
- Section-specific layouts

**Best for:** Understanding advanced template patterns and building complex web applications

### forms
A Todo app example demonstrating next-dj's form handling: form actions, ModelForm create/edit flows, and the `{% form %}` template tag.

**Key Features:**
- Form actions registered with `@forms.action()` (create and update)
- ModelForm with `get_initial()` for new vs edit (instance) flows
- `{% form @action="..." %}` with automatic CSRF and action URL
- URL parameters passed into edit page context and form handlers
- Django messages and shared layout

**Best for:** Building CRUD-style pages and understanding next-dj forms with file-based routing

### components
A **blog** sample (English UI) built on next-dj components: simple and composite pieces, **slots** for dynamic props, root vs branch scope, plus `next.forms` (auth + post CRUD), pagination, and a composite **header** with `component.py` context (`user`, active nav via `request.path`).

**Key Features:**
- Simple `.djx` components and composite folders (`component.djx` + optional `component.py` with `@context` decorator from `next.components`)
- Slots for list-driven UI where `{% component %}` only accepts literal props
- Root `root_components/` vs app `pages/_components/` scope. `NEXT_FRAMEWORK` (`DEFAULT_COMPONENT_BACKENDS`, `COMPONENTS_DIR`) so the file router skips the components folder
- Template tags: void `{% component %}`, block `{% #component %}` / `{% /component %}`, `{% #slot %}` / `{% /slot %}`, short `{% slot %}`, `{% #set_slot %}` / `{% /set_slot %}`, short void `{% set_slot %}` (builtins; no `{% load %}` for these)
- Optional: middleware protecting `/posts/create/` and `/posts/<id>/edit/`, `LogoutView`, pytest suite

**Best for:** Reusable UI fragments, slots, component scope, and combining components with forms and file-based routing

### static
A realistic showcase of next-dj's **static asset pipeline**: co-located CSS/JS, module-level `styles`/`scripts` lists, layout-wide dependencies via `{% use_style %}` / `{% use_script %}`, slot-based injection, cascade ordering, deduplication, and Django `staticfiles` integration (Manifest/S3 ready). Third-party stacks integrated: Bootstrap 5, Bootstrap Icons, Chart.js, and a React 18 + Babel standalone click counter.

**Key Features:**
- Co-located `layout.css`/`layout.js` (next to `layout.djx`), `template.css`/`template.js` (next to `template.djx`), and `component.css`/`component.js` (next to `component.djx`)
- `styles = [...]` and `scripts = [...]` module-level lists in `page.py` and `component.py`
- `{% use_style %}` / `{% use_script %}` template tags for shared layout deps (Bootstrap)
- `{% collect_styles %}` / `{% collect_scripts %}` post-render injection slots
- Cascade ordering: `use_*` → layout → page → component (child scopes can override parents)
- Deduplication by URL — repeated components ship each CDN only once
- Complex integration example: React + Babel standalone counter rendered twice via `ReactDOM.createRoot`
- Co-located assets resolved via Django `staticfiles_storage` under `next/` namespace

**Best for:** Wiring in CSS/JS without a bundler, integrating third-party libraries (including React/Babel), and understanding the cascade + dedup contract of the static subsystem

## Getting Started

Each example includes its own README with detailed setup and running instructions. To get started:

1. Choose an example that matches your learning goals
2. Navigate to the example directory
3. Follow the setup instructions in the example's README
4. Run the example and explore the generated URLs

## Example Selection Guide

**If you're new to next-dj:**
Start with the `file-routing` example to understand basic concepts and routing patterns.

**If you're building a real application:**
Use the `pages` example as a reference for integrating next-dj with Django's full feature set.

**If you're building complex layouts:**
Use the `layouts` example to understand advanced template inheritance and layout management patterns.

**If you're building forms and CRUD flows:**
Use the `forms` example to see form actions, ModelForm, and `{% form %}` with file-based routing.

**If you're building reusable UI pieces:**
Use the `components` example for a small blog with simple/composite components, slots, root scope, and forms-driven auth and posts.

**If you're wiring up CSS / JS without a bundler:**
Use the `static` example to see co-located assets, cascade ordering, deduplication, and a React + Babel integration.

**If you're exploring specific features:**
- Parameter handling: `file-routing` example
- Database integration: `pages` example
- Template management: `pages` example
- Admin integration: `pages` example
- Template inheritance: `layouts` example
- Context processors: `layouts` example
- Bootstrap layout (minimal): `layouts` example
- Form actions and ModelForm: `forms` example
- Create/edit flows with URL params: `forms` example
- Components, slots, scope, blog + forms: `components` example
- Co-located CSS/JS, `{% use_style %}`, cascade, dedup: `static` example
- React + Babel integration without a bundler: `static` example

## Common Patterns

All examples demonstrate these common next-dj patterns:

- **File structure mapping to URLs**: Directory structure directly maps to URL patterns
- **Parameter syntax**: `[param]`, `[type:param]`, and `[[args]]` for different parameter types
- **Template loading**: Both Python string templates and template.djx files
- **Context management**: Registering context functions for template data
- **Error handling**: Graceful handling of missing templates and invalid parameters
- **Template inheritance**: Nested layout structures with Django's template system
- **Context processors**: Site-wide template variables and configuration
- **Active navigation**: Dynamic UI states based on current URL path

## Troubleshooting

If you encounter issues while running the examples:

1. Ensure you have the correct Python and Django versions
2. Check that next-dj is properly installed
3. Verify that all dependencies are installed
4. Check the example-specific README for additional troubleshooting steps
5. Review the main project documentation for common issues

## Contributing

These examples are part of the next-dj project. If you find issues or have suggestions for improvement:

1. Check the main project repository for existing issues
2. Create a new issue with detailed description
3. Follow the project's contribution guidelines
4. Ensure any changes maintain backward compatibility

For more information about next-dj, visit the main project documentation.
