Documentation Guide
===================

This guide explains how to write and maintain documentation for next.dj.

Documentation Structure
-----------------------

The documentation is organized into logical sections (see ``index.rst``):

* **Guide** - Getting started and main framework topics (routing, templates, components, context, forms, and more)
* **API Reference** - Generated API docs and reference pages such as system checks
* **Contributing** - This guide and :doc:`the contributing guide </content/contributing/contributing>`

File Naming Convention
----------------------

Use descriptive, lowercase names with hyphens:

* ``file-router.rst`` - not ``file_router.rst`` or ``FileRouter.rst``
* ``templates-layouts.rst`` - not ``templates_layouts.rst``
* ``context-system.rst`` - not ``context_system.rst``

Writing Style
-------------

* Use clear, concise language
* Write in English
* Use present tense ("The system does..." not "The system will do...")
* Be consistent with terminology
* Include code examples where helpful

RST Formatting
--------------

* Use proper heading levels (``=``, ``-``, ``~``, ``^``)
* Include table of contents with ``.. toctree::``
* Use code blocks with language specification:

  .. code-block:: python

     def example_function():
         return "Hello, World!"

* Use inline code with double backticks: ``function_name``
* Use cross-references: :ref:`getting-started`

When Adding New Features
------------------------

1. **Update the main index** - Add new sections to ``index.rst``
2. **Create feature documentation** - Write comprehensive docs in appropriate section
3. **Update API docs** - Add new classes/functions to API reference
4. **Add examples** - Include practical examples in ``examples/`` directory
5. **Update installation guide** - If new dependencies are added

When Modifying Existing Features
--------------------------------

1. **Update relevant documentation** - Find and update existing docs
2. **Check for breaking changes** - Update migration guides if needed
3. **Update examples** - Ensure examples still work with changes
4. **Update API docs** - Reflect any API changes

Code Examples
-------------

* Always test code examples before committing
* Use realistic examples that users can copy-paste
* Include both simple and advanced examples
* Show error handling where appropriate
* Use consistent variable naming

Documentation Checklist
-----------------------

Before submitting documentation changes:

- [ ] All new features are documented
- [ ] Code examples are tested and working
- [ ] RST syntax is correct (no warnings)
- [ ] Links are working (use ``make docs-linkcheck``)
- [ ] Table of contents is updated
- [ ] Cross-references are working

Building Documentation
----------------------

Doc tooling is invoked from the **project root** via the root ``Makefile`` (there is no ``docs/Makefile``).

To build documentation locally:

.. code-block:: bash

   make docs

After ``uv sync --locked --group docs``, you can build with ``uv run sphinx-build docs docs/_build``.

To open the built HTML in your browser after a build:

.. code-block:: bash

   make docs-serve

On macOS, ``make docs-serve`` runs ``open docs/_build/index.html``. On other systems, open ``docs/_build/index.html`` in a browser yourself. On Linux, ``xdg-open`` is a common way to do that.

Common Issues
-------------

* **Duplicate object descriptions** - Use ``:no-index:`` for duplicate API docs
* **Missing imports** - Ensure all modules are importable in ``conf.py``
* **Broken links** - Run ``make docs-linkcheck`` to find broken links
* **RST warnings** - Fix indentation and formatting issues

Remember that good documentation is as important as good code.
