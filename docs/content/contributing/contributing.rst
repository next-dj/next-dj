Contributing to next.dj
========================

Thank you for your interest in contributing to the Next Django Framework! This document outlines the coding standards, development practices, and contribution guidelines that maintain consistency and quality across the codebase.

Development Setup
-----------------

Prerequisites
~~~~~~~~~~~~~

- Python 3.12 or higher
- `uv <https://docs.astral.sh/uv/>`_ package manager
- Git

Local Development Commands
~~~~~~~~~~~~~~~~~~~~~~~~~~

The project uses a Makefile for common development tasks. All commands should be run using ``uv`` instead of direct ``python`` commands.

Installation Commands
^^^^^^^^^^^^^^^^^^^^^

**Install Package in Development Mode**

This command installs the package in editable mode, allowing code changes to be reflected immediately without reinstalling. Perfect for active development when you need to modify the source code and see changes instantly.

.. code-block:: bash

   make install

**Install Development Dependencies**

This command installs all development tools and dependencies needed for testing, linting, type checking, and code formatting.

.. code-block:: bash

   make install-dev

**Setup Complete Development Environment**

This is a one-command setup that prepares everything needed for development. It installs all dependencies and configures pre-commit hooks for automated code quality checks on every commit.

.. code-block:: bash

   make dev-setup

Testing Commands
^^^^^^^^^^^^^^^^

**Run All Tests with 100% Coverage Requirement**

This command runs the complete test suite with 100% coverage requirement. All tests in the ``tests/`` directory run with verbose output, HTML coverage report is generated in ``htmlcov/`` folder, and validation ensures coverage meets the 100% requirement for the main codebase.

.. code-block:: bash

   make test

**Run Tests Without Coverage (Faster)**

This command provides quick test feedback during development without the overhead of coverage analysis.

.. code-block:: bash

   make test-fast

**Run Tests for Examples with 100% Coverage Requirement**

This command validates that all examples have comprehensive test coverage. Each example's ``tests.py`` file runs with 100% coverage requirement.

.. code-block:: bash

   make test-examples

**Run All Tests Including Examples**

This is the comprehensive test suite that runs both main tests and example tests.

.. code-block:: bash

   make test-all

Code Quality Commands
^^^^^^^^^^^^^^^^^^^^^

**Run Linting and Formatting Checks**

This command checks code quality and formatting compliance using Ruff. It automatically fixes many issues and reports any remaining problems that need manual attention.

.. code-block:: bash

   make lint

**Format Code Automatically**

This command automatically formats your code according to project standards.

.. code-block:: bash

   make format

**Run Type Checking with MyPy**

This command performs static type checking to catch type-related errors and ensure type safety.

.. code-block:: bash

   make type-check

**Run All CI Checks Locally**

This command runs the complete CI pipeline locally, ensuring your code will pass all automated checks before submitting.

.. code-block:: bash

   make ci

Code Standards
--------------

General Principles
~~~~~~~~~~~~~~~~~~

The codebase follows SOLID principles and Object-Oriented Programming (OOP) patterns. Code should be:

- **DRY (Don't Repeat Yourself)**: Minimize code duplication
- **SOLID**: Follow Single Responsibility, Open/Closed, Liskov Substitution, Interface Segregation, and Dependency Inversion principles
- **Clean**: Write self-documenting code with clear intent
- **Testable**: Design for easy testing and mocking
- **Replaceable**: Design classes and components to be easily replaceable and extensible

Replaceability Principle
^^^^^^^^^^^^^^^^^^^^^^^^

**Maximum replaceability is a core architectural principle**

A core architectural principle is **maximum replaceability** - all major components should be designed to be easily replaced with custom implementations. This enables:

- **Django Integration Flexibility**: Replace default Django components with custom alternatives
- **Extension Development**: Create plugins and extensions without modifying core code
- **Framework Customization**: Adapt the framework to specific project needs
- **Future Evolution**: Easily upgrade or replace components as requirements change

Import Organization
^^^^^^^^^^^^^^^^^^^

**All imports must be declared at the top of files**

All imports must be declared at the top of files, organized in the following order. Imports inside functions or methods are extremely rare and should only be used in very specific cases.

**Why imports should be at the top:**
- **PEP 8 compliance**: Python's official style guide requires imports at module level
- **Performance**: Imports are cached after first load, so top-level imports are more efficient
- **Readability**: Makes dependencies immediately visible to anyone reading the code
- **IDE support**: Better autocomplete and static analysis when imports are at the top
- **Debugging**: Easier to identify import issues when they're all in one place

Code Style
^^^^^^^^^^

Comments and Docstrings
""""""""""""""""""""""""

- **Comments**: Write in English using lowercase letters, except for proper names
- **Docstrings**: Provide technical descriptions without argument details
- **File headers**: Include general file description docstring at the top
- **No argument descriptions**: Do not describe function arguments in docstrings (temporary rule, will be relaxed later)

Error Handling
""""""""""""""

- Keep try/except blocks small and focused
- Avoid catching the base Exception class
- Follow PEP8 guidelines for exception handling

Loop Optimization
"""""""""""""""""

Minimize the use of loops and prefer built-in functions and comprehensions. This follows the principle of using Python's built-in optimizations and making code more readable and efficient.

Type Hints
""""""""""

Use type hints consistently throughout the codebase:

.. code-block:: python

   from typing import Any, Callable, Generator
   from pathlib import Path

   def process_files(
       file_paths: list[Path], 
       processor: Callable[[Path], str]
   ) -> Generator[str, None, None]:
       """Process multiple files using the provided processor function."""
       for file_path in file_paths:
           yield processor(file_path)

Testing Guidelines
------------------

Test Structure
~~~~~~~~~~~~~~

Tests follow Django's testing patterns using pytest. All test files should be named ``test_<module>.py`` and use classes with OOP principles.

Test Organization
~~~~~~~~~~~~~~~~~

.. code-block:: python

   import pytest
   from unittest.mock import MagicMock, patch
   from django.test import Client

   from next.pages import Page, ContextManager


   class TestPageRendering:
       """Test page rendering functionality."""

       @pytest.fixture
       def page_instance(self):
           """Create a fresh Page instance for each test."""
           return Page()

       @pytest.fixture
       def mock_request(self):
           """Create a mock HTTP request for testing."""
           request = MagicMock()
           request.method = "GET"
           return request

       @pytest.mark.parametrize(
           "template_content,expected_output",
           [
               ("Hello {{ name }}", "Hello World"),
               ("{{ title }}", "Test Title"),
           ],
       )
       def test_template_rendering(self, page_instance, template_content, expected_output):
           """Test that templates render with correct context variables."""
           # test implementation
           pass

Testing Patterns
~~~~~~~~~~~~~~~~

Use pytest.mark.parametrize
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Prefer ``pytest.mark.parametrize`` over fixtures for case-specific data to reduce code duplication:

.. code-block:: python

   @pytest.mark.parametrize(
       "url,expected_status",
       [
           ("/simple/", 200),
           ("/kwargs/123/", 200),
           ("/args/test/path/", 200),
           ("/kwargs/invalid/", 404),
           ("/nonexistent/", 404),
       ],
   )
   def test_pages_accessible(client, url, expected_status):
       """Test that pages are accessible with expected status codes."""
       response = client.get(url)
       assert response.status_code == expected_status

Mocking Strategy
~~~~~~~~~~~~~~~~

Mock as much as possible to isolate units under test:

.. code-block:: python

   @patch("next.pages.inspect.currentframe")
   def test_context_decorator_detection(mock_frame):
       """Test context decorator file path detection."""
       # setup mock frame
       mock_frame.return_value.f_back.f_globals = {"__file__": "/test/path/page.py"}
       
       # test implementation
       result = page._get_caller_path()
       assert result == Path("/test/path/page.py")

Django Test Client
~~~~~~~~~~~~~~~~~~

Use Django REST Framework test client for testing HTTP responses:

.. code-block:: python

   def test_page_renders_correctly(client):
       """Test that pages render correctly with expected content."""
       response = client.get("/test-page/")
       assert response.status_code == 200
       content = response.content.decode()
       assert "Expected Content" in content

Test Coverage Requirements
~~~~~~~~~~~~~~~~~~~~~~~~~~

**100% test coverage is mandatory for all code**

**Coverage Requirements:**
- **Main codebase: 100% coverage requirement** - All framework code must have complete test coverage
- **Examples: 100% coverage requirement** - All example code must have complete test coverage
- **CI validation**: Both main codebase and examples are automatically validated for 100% coverage
- **All new code must include comprehensive tests** - No code can be merged without proper test coverage

Example Development
-------------------

New Feature Requirements
~~~~~~~~~~~~~~~~~~~~~~~~

**Every new feature must include a working example with 100% test coverage**

**Mandatory Requirements:**
- Each new feature requires a complete, working example in the ``examples/`` directory
- Examples must be self-contained and demonstrate the specific feature in isolation
- Examples must include comprehensive ``tests.py`` with 100% code coverage
- CI automatically validates that all examples have 100% test coverage
- Examples without proper tests will cause CI to fail

Example Structure
~~~~~~~~~~~~~~~~~

Each example should be self-contained and demonstrate specific features:

.. code-block:: text

   examples/
   ├── feature-name/
   │   ├── config/
   │   │   ├── settings.py
   │   │   └── urls.py
   │   ├── myapp/
   │   │   ├── pages/
   │   │   │   └── example/
   │   │   │       ├── page.py
   │   │   │       └── template.djx
   │   │   └── models.py
   │   ├── conftest.py
   │   ├── tests.py
   │   └── README.md

Pull Request Process
--------------------

Before Submitting
~~~~~~~~~~~~~~~~~

**Complete all checks before submitting**

1. Run all quality checks: ``make ci``
2. Ensure 100% test coverage for new code
3. Update documentation as needed
4. Follow the coding standards outlined above

Pull Request Requirements
~~~~~~~~~~~~~~~~~~~~~~~~~

**Mandatory Requirements:**
- Clear description of changes with technical details
- Reference to related issues (use "Fixes #123" or "Closes #123")
- Updated tests for new functionality with 100% coverage
- Documentation updates if needed
- All CI checks must pass
- Working example for new features (if applicable)

**Quality Standards:**
- Code follows all established patterns and principles
- Tests are comprehensive and meaningful
- Documentation is clear and complete
- No breaking changes without proper deprecation

Development Workflow
--------------------

**Follow this workflow for all contributions**

1. **Setup**: Run ``make dev-setup`` to configure your environment
2. **Develop**: Create feature branches and follow coding standards
3. **Test**: Run ``make test-all`` to ensure everything works
4. **Quality**: Run ``make ci`` to check code quality
5. **Submit**: Create pull request with comprehensive description

Branch Naming Convention
~~~~~~~~~~~~~~~~~~~~~~~~

Use descriptive branch names that indicate the type of change:
- ``feat/description`` - New features
- ``fix/description`` - Bug fixes
- ``docs/description`` - Documentation updates
- ``refactor/description`` - Code refactoring
- ``test/description`` - Test improvements

Commit Message Format
~~~~~~~~~~~~~~~~~~~~~

Follow conventional commit format:

.. code-block:: text

   type(scope): description

   [optional body]

   [optional footer]

Examples:
- ``feat: refactor pages router``
- ``feat(templates): add support for custom template loaders``
- ``fix(routing): resolve URL pattern conflicts``
- ``docs(api): update template loader documentation``

Getting Help
------------

**Multiple ways to get help**

- Check existing issues and discussions
- Review the codebase for similar implementations
- Ask questions in pull request comments
- Follow the established patterns in the codebase

**The codebase is the source of truth** - When in doubt, examine how similar functionality is implemented and follow those patterns.

Common Issues and Solutions
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Coverage Issues:**
- Use ``make test-coverage`` to see detailed coverage reports
- Check ``htmlcov/index.html`` for visual coverage analysis
- Ensure all code paths are tested

**Import Errors:**
- Verify all imports are at the top of files
- Check for circular import issues
- Use ``uv run python -c "import module"`` to test imports

**Test Failures:**
- Run ``make test-fast`` for quick feedback
- Check test output for specific error messages
- Ensure test data is properly set up