# next-dj Examples

This directory contains practical examples demonstrating next-dj's file-based routing capabilities in various scenarios and use cases.

## Available Examples

### file-routing
A basic example showcasing the core file-based routing functionality with different parameter types and routing strategies.

**Key Features:**
- Simple pages without parameters
- Parameterized routes with type hints
- Wildcard argument handling
- Both app-specific and root-level routing

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

**If you're exploring specific features:**
- Parameter handling: `file-routing` example
- Database integration: `pages` example
- Template management: `pages` example
- Admin integration: `pages` example

## Common Patterns

All examples demonstrate these common next-dj patterns:

- **File structure mapping to URLs**: Directory structure directly maps to URL patterns
- **Parameter syntax**: `[param]`, `[type:param]`, and `[[args]]` for different parameter types
- **Template loading**: Both Python string templates and template.djx files
- **Context management**: Registering context functions for template data
- **Error handling**: Graceful handling of missing templates and invalid parameters

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
