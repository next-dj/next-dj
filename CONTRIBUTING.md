# Contributing to `next.dj` framework

Thank you for your interest in contributing to the Next Django Framework! This document outlines the coding standards, development practices, and contribution guidelines that maintain consistency and quality across the codebase.

## Development Setup

### Prerequisites

- Python 3.12 or higher
- [uv](https://docs.astral.sh/uv/) package manager
- Git

### Local Development Commands

The project uses a Makefile for common development tasks. All commands should be run using `uv` instead of direct `python` commands.

#### Installation Commands

**Install Package in Development Mode**

This command installs the package in editable mode, allowing code changes to be reflected immediately without reinstalling. Perfect for active development when you need to modify the source code and see changes instantly. Package is installed with `uv pip install -e .` in development mode, so all imports will use the local source code instead of the installed package.

```bash
make install
```

**Install Development Dependencies**

This command installs all development tools and dependencies needed for testing, linting, type checking, and code formatting. These tools are essential for maintaining code quality and running the test suite. All development dependencies from `pyproject.toml` dependency-groups.dev section are installed, including pytest, ruff, mypy, pre-commit, and other quality tools.

```bash
make install-dev
```

**Setup Complete Development Environment**

This is a one-command setup that prepares everything needed for development. It installs all dependencies and configures pre-commit hooks for automated code quality checks on every commit. You get a full development environment with dependencies installed and pre-commit hooks configured to run quality checks automatically before each commit.

```bash
make dev-setup
```

#### Testing Commands

**Run All Tests with 100% Coverage Requirement**

This command runs the complete test suite with 100% coverage requirement. It's the main testing command that ensures code quality and provides detailed coverage reports. All tests in the `tests/` directory run with verbose output, HTML coverage report is generated in `htmlcov/` folder, terminal coverage report shows missing lines, and validation ensures coverage meets the 100% requirement for the main codebase. Tests will fail if coverage is below 100%.

```bash
make test
```

**Run Tests Without Coverage (Faster)**

This command provides quick test feedback during development without the overhead of coverage analysis. Perfect for rapid iteration when you need fast feedback on code changes. All tests run quickly with verbose output but without coverage analysis, providing immediate feedback on test results.

```bash
make test-fast
```

**Run Tests with Coverage Report Only**

This command runs tests with coverage analysis but without the 100% coverage requirement. Useful for checking current coverage levels during development without failing on coverage thresholds. Generates HTML and terminal coverage reports.

```bash
make test-coverage
```

**Run Tests for Examples with 100% Coverage Requirement**

This command validates that all examples have comprehensive test coverage. It scans every example directory and ensures that examples with Python code have complete test coverage. Each example's `tests.py` file runs with 100% coverage requirement, validates that all examples with Python code have tests, and fails if any example lacks tests or doesn't achieve 100% coverage.

```bash
make test-examples
```

**Run All Tests Including Examples**

This is the comprehensive test suite that runs both main tests and example tests. Use this before submitting changes to ensure complete codebase quality. Both main tests and example tests run with their respective coverage requirements, ensuring the entire codebase meets quality standards.

```bash
make test-all
```

#### Code Quality Commands

**Run Linting and Formatting Checks**

This command checks code quality and formatting compliance using Ruff. It automatically fixes many issues and reports any remaining problems that need manual attention. Ruff linter runs on all code with auto-fix enabled, then checks formatting compliance. It handles pycodestyle, pyflakes, isort, flake8-bugbear, comprehensions, and pyupgrade rules, automatically fixing E, W, F, I, B, C4, UP rule violations and reporting any remaining issues.

```bash
make lint
```

**Format Code Automatically**

This command automatically formats your code according to project standards. It applies both linting fixes and code formatting in one command, ensuring consistent code style across the project. Code is automatically formatted to 88 character line length with consistent style, all auto-fixable linting issues are resolved, and code is ready for commit.

```bash
make format
```

**Run Type Checking with MyPy**

This command performs static type checking to catch type-related errors and ensure type safety. It uses strict settings for maximum type safety and includes Django plugin support. Static type analysis runs using mypy with Django plugin, validating all type hints, detecting type-related errors, and ensuring type safety across the codebase.

```bash
make type-check
```

**Run All CI Checks Locally**

This command runs the complete CI pipeline locally, ensuring your code will pass all automated checks before submitting. It's the final quality check before committing changes. Complete CI pipeline execution includes linting, type checking, and testing with 100% coverage requirement for both main codebase and examples, ensuring your code meets all quality standards and will pass automated checks.

```bash
make ci
```

## Code Standards

### General Principles

The codebase follows SOLID principles and Object-Oriented Programming (OOP) patterns. Code should be:

- **DRY (Don't Repeat Yourself)**: Minimize code duplication
- **SOLID**: Follow Single Responsibility, Open/Closed, Liskov Substitution, Interface Segregation, and Dependency Inversion principles
- **Clean**: Write self-documenting code with clear intent
- **Testable**: Design for easy testing and mocking
- **Replaceable**: Design classes and components to be easily replaceable and extensible

#### Replaceability Principle

> [!IMPORTANT]
> **Maximum replaceability is a core architectural principle**

A core architectural principle is **maximum replaceability** - all major components should be designed to be easily replaced with custom implementations. This enables:

- **Django Integration Flexibility**: Replace default Django components with custom alternatives
- **Extension Development**: Create plugins and extensions without modifying core code
- **Framework Customization**: Adapt the framework to specific project needs
- **Future Evolution**: Easily upgrade or replace components as requirements change

> [!TIP]
> **Design for extensibility**: When contributing new features, always consider how they can be extended or replaced. Use abstract base classes, factory patterns, and dependency injection to ensure maximum flexibility.

**Implementation Guidelines:**

```python
# good: abstract base class allows easy replacement
class TemplateLoader(ABC):
    """Abstract interface for loading page templates from various sources."""
    
    @abstractmethod
    def can_load(self, file_path: Path) -> bool:
        pass
    
    @abstractmethod
    def load_template(self, file_path: Path) -> str | None:
        pass

# good: concrete implementation can be replaced
class PythonTemplateLoader(TemplateLoader):
    """Loads templates from Python modules."""
    # implementation

# good: factory pattern enables runtime replacement
class RouterFactory:
    _backends: dict[str, type[RouterBackend]] = {
        "next.urls.FileRouterBackend": FileRouterBackend,
    }
    
    @classmethod
    def register_backend(cls, name: str, backend_class: type[RouterBackend]) -> None:
        """Register a new router backend type for easy replacement."""
        cls._backends[name] = backend_class
```

**Key Replaceability Patterns:**

1. **Abstract Base Classes**: Define clear interfaces that can be implemented differently
2. **Factory Pattern**: Allow runtime registration of alternative implementations
3. **Dependency Injection**: Pass dependencies rather than hard-coding them
4. **Configuration-Driven**: Use settings to control which implementations are used
5. **Plugin Architecture**: Design systems that can be extended without modification

### Import Organization

> [!IMPORTANT]
> **All imports must be declared at the top of files**

All imports must be declared at the top of files, organized in the following order. Imports inside functions or methods are extremely rare and should only be used in very specific cases (like avoiding circular imports or conditional imports for optional dependencies).

**Why imports should be at the top:**
- **PEP 8 compliance**: Python's official style guide requires imports at module level
- **Performance**: Imports are cached after first load, so top-level imports are more efficient
- **Readability**: Makes dependencies immediately visible to anyone reading the code
- **IDE support**: Better autocomplete and static analysis when imports are at the top
- **Debugging**: Easier to identify import issues when they're all in one place

> [!WARNING]
> **Imports inside functions are strongly discouraged** - Only use them for very specific cases like avoiding circular imports or conditional imports for optional dependencies. Always prefer top-level imports.

**✅ Good - Clear dependency visibility:**
All imports are immediately visible at the top of the file, making it easy to understand what the module depends on.

```python
# standard library imports
import os
import sys
from pathlib import Path

# third-party imports
import pytest
from django.conf import settings

# local imports
from next.pages import Page
from next.urls import RouterBackend

def process_files(file_paths):
    return [Path(p) for p in file_paths]
```

**❌ Bad - Hidden dependencies:**
Imports scattered throughout the code make it difficult to understand module dependencies and can lead to unexpected import errors.

```python
def process_files(file_paths):
    from pathlib import Path  # import inside function
    import os  # another import inside function
    return [Path(p) for p in file_paths]

def another_function():
    from django.conf import settings  # scattered imports
    return settings.DEBUG
```

**✅ Good - PEP 8 compliant:**
Follows Python's official style guide for import organization, making code consistent with the broader Python ecosystem.

```python
# proper import grouping
import os
from pathlib import Path

import pytest
from django.conf import settings

from next.pages import Page
from next.urls import RouterBackend
```

**❌ Bad - PEP 8 violation:**
Violates Python's style guide and makes code inconsistent with community standards.

```python
# mixed import order
from next.pages import Page
import os
from django.conf import settings
import pytest
from next.urls import RouterBackend
from pathlib import Path
```

**✅ Good - Performance optimized:**
Imports are loaded once when the module is first imported, then cached for subsequent uses.

```python
import json
from typing import Dict, List

def process_data(data: str) -> Dict:
    return json.loads(data)  # json already imported
```

**❌ Bad - Performance penalty:**
Imports inside functions are executed every time the function is called, causing unnecessary overhead.

```python
def process_data(data: str) -> Dict:
    import json  # imported every function call
    from typing import Dict  # imported every function call
    return json.loads(data)
```

### Code Style

#### Comments and Docstrings

- **Comments**: Write in English using lowercase letters, except for proper names
- **Docstrings**: Provide technical descriptions without argument details
- **File headers**: Include general file description docstring at the top
- **No argument descriptions**: Do not describe function arguments in docstrings (temporary rule, will be relaxed later)

**Why this style matters:**
- **Consistency**: Uniform comment style makes code easier to read and maintain
- **Professional appearance**: Proper documentation shows attention to detail
- **Maintainability**: Clear comments help future developers understand the code
- **PEP 257 compliance**: Follows Python's docstring conventions
- **Type hints provide parameter info**: Since we use type hints, describing arguments in docstrings is redundant
- **Focus on behavior**: Docstrings should explain what the function does, not what parameters it takes

**✅ Good - Clear, professional documentation:**
Comments and docstrings follow consistent style guidelines, making code self-documenting and professional.

```python
"""
File-based page rendering system for Django applications.

This module implements a sophisticated page rendering system that automatically
generates Django views and URL patterns from page.py files located in application
directories. The system supports multiple template sources, context management,
and seamless integration with Django's URL routing.
"""

def process_data(data: list[str]) -> dict[str, int]:
    """
    Process input data and return statistics.
    
    Analyzes the provided data list and generates statistical information
    about the content, including frequency counts and distribution patterns.
    """
    # process each item in the data list
    for item in data:
        # validate item format before processing
        if not isinstance(item, str):
            continue
        # ... processing logic
```

**❌ Bad - Inconsistent or unclear documentation:**
Poor documentation makes code hard to understand and maintain, violating professional standards.

```python
# some file for processing data
# TODO: add more features

def process_data(data):
    # this function processes data
    # it takes a list and returns a dict
    # data is the input list
    # returns dict with stats
    result = {}
    for item in data:
        # check if item is string
        if isinstance(item, str):
            # add to result
            result[item] = result.get(item, 0) + 1
    return result
```

**✅ Good - PEP 257 compliant:**
Docstrings follow Python's official docstring conventions, making them consistent with the broader Python ecosystem.

```python
def calculate_total(items: list[float]) -> float:
    """
    Calculate the total sum of all items.
    
    Processes the input list and returns the mathematical sum
    of all numeric values, handling edge cases gracefully.
    """
    return sum(items)
```

**❌ Bad - PEP 257 violation:**
Inconsistent docstring format makes code unprofessional and harder to parse by documentation tools.

```python
def calculate_total(items):
    """Calculate total sum"""  # too brief
    """
    Calculate the total sum of all items.
    @param items: list of numbers
    @return: float total
    """  # wrong format, describes parameters
    return sum(items)
```

**✅ Good - No argument descriptions:**
Docstrings focus on what the function does, not what parameters it takes. Type hints provide parameter information.

```python
def process_user_data(user_id: int, include_profile: bool = True) -> dict:
    """
    Process user data and return formatted result.
    
    Combines user information with optional profile data,
    applying business logic and validation rules.
    """
    # implementation
```

**❌ Bad - Describing arguments in docstrings:**
Avoid describing function arguments in docstrings as this information is already available through type hints. This rule will be relaxed in the future to allow more comprehensive documentation.

```python
def process_user_data(user_id: int, include_profile: bool = True) -> dict:
    """
    Process user data and return formatted result.
    
    Args:
        user_id: The unique identifier for the user
        include_profile: Whether to include profile data in the result
    
    Returns:
        dict: Formatted user data dictionary
    """
    # implementation
```

**✅ Good - Self-documenting code:**
Comments explain the "why" behind complex logic, not the "what" that's already clear from the code.

```python
# use binary search for better performance on large datasets
def find_item(sorted_list: list, target: int) -> int:
    left, right = 0, len(sorted_list) - 1
    while left <= right:
        mid = (left + right) // 2
        if sorted_list[mid] == target:
            return mid
        elif sorted_list[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1
```

**❌ Bad - Redundant comments:**
Comments that just repeat what the code does add noise and make the code harder to read.

```python
def find_item(sorted_list, target):
    # set left to 0
    left = 0
    # set right to length minus 1
    right = len(sorted_list) - 1
    # while left is less than or equal to right
    while left <= right:
        # calculate mid point
        mid = (left + right) // 2
        # if mid equals target
        if sorted_list[mid] == target:
            # return mid
            return mid
        # else if mid is less than target
        elif sorted_list[mid] < target:
            # set left to mid plus 1
            left = mid + 1
        # else
        else:
            # set right to mid minus 1
            right = mid - 1
    # return -1
    return -1
```

#### Error Handling

- Keep try/except blocks small and focused
- Avoid catching the base Exception class
- Follow PEP8 guidelines for exception handling

**Why proper error handling matters:**
- **Debugging**: Specific exceptions make it easier to identify and fix issues
- **Maintainability**: Clear error handling makes code more robust and easier to maintain
- **User experience**: Proper error messages help users understand what went wrong
- **Security**: Avoiding broad exception catching prevents hiding security vulnerabilities

**✅ Good - Specific exception handling:**
Catches only the exceptions you expect and can handle, making debugging easier and code more robust.

```python
try:
    result = process_file(file_path)
except FileNotFoundError:
    logger.warning(f"file not found: {file_path}")
    return None
except PermissionError:
    logger.error(f"permission denied: {file_path}")
    raise
except ValueError as e:
    logger.error(f"invalid file format: {e}")
    raise
```

**❌ Bad - Broad exception catching:**
Catching base Exception hides real problems and makes debugging nearly impossible.

```python
try:
    result = process_file(file_path)
except Exception:  # too broad
    return None

# or even worse:
try:
    result = process_file(file_path)
except:  # bare except
    pass  # silently ignore all errors
```

**✅ Good - Focused try/except blocks:**
Each try/except block handles a specific operation and its related exceptions, making code easier to understand and maintain.

```python
def process_user_data(user_id: int) -> dict:
    try:
        user = get_user(user_id)
    except UserNotFoundError:
        logger.warning(f"user {user_id} not found")
        return {}
    
    try:
        profile = get_user_profile(user_id)
    except ProfileNotFoundError:
        logger.info(f"no profile for user {user_id}")
        profile = None
    
    return {"user": user, "profile": profile}
```

**❌ Bad - Large try/except blocks:**
Catching exceptions for multiple unrelated operations makes it unclear which operation failed and why.

```python
def process_user_data(user_id: int) -> dict:
    try:
        user = get_user(user_id)
        profile = get_user_profile(user_id)
        data = process_profile_data(profile)
        save_processed_data(data)
        return {"user": user, "profile": profile, "data": data}
    except Exception as e:
        logger.error(f"error processing user {user_id}: {e}")
        return {}
```

**✅ Good - Proper exception propagation:**
Re-raises exceptions that can't be handled locally, allowing higher-level code to decide how to respond.

```python
def load_config(config_path: str) -> dict:
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"config file not found: {config_path}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"invalid JSON in config: {e}")
        raise  # re-raise for caller to handle
    except PermissionError:
        logger.error(f"permission denied: {config_path}")
        raise  # re-raise for caller to handle
```

**❌ Bad - Silent failure:**
Swallowing exceptions without logging or re-raising makes problems invisible and hard to debug.

```python
def load_config(config_path: str) -> dict:
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception:
        return {}  # silently return empty dict

# or even worse:
def load_config(config_path: str) -> dict:
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except:
        pass  # completely silent failure
    return {}
```

#### Loop Optimization

Minimize the use of loops and prefer built-in functions and comprehensions. This follows the principle of using Python's built-in optimizations and making code more readable and efficient.

**Why loop optimization matters:**
- **Performance**: Built-in functions are implemented in C and are much faster than Python loops
- **Readability**: Built-in functions express intent more clearly than manual loops
- **Maintainability**: Less code means fewer bugs and easier maintenance
- **Pythonic code**: Follows Python's philosophy of "batteries included"

**✅ Good - Use built-in functions:**
Built-in functions like `sum()`, `max()`, `min()`, `all()`, `any()` are optimized in C and much faster than manual loops.

```python
# efficient and readable
total = sum(values)
maximum = max(numbers)
has_valid = any(item.is_valid() for item in items)
all_positive = all(x > 0 for x in numbers)

# list comprehensions for filtering/transforming
filtered = [x for x in items if x.is_valid()]
squared = [x**2 for x in numbers]
names = [user.name for user in users]
```

**❌ Bad - Manual loops for simple operations:**
Writing loops for operations that have built-in equivalents is slower and less readable.

```python
# slow and verbose
total = 0
for value in values:
    total += value

maximum = numbers[0]
for num in numbers[1:]:
    if num > maximum:
        maximum = num

has_valid = False
for item in items:
    if item.is_valid():
        has_valid = True
        break

# manual filtering
filtered = []
for x in items:
    if x.is_valid():
        filtered.append(x)
```

**✅ Good - Use comprehensions for data transformation:**
List/dict/set comprehensions are more efficient and readable than manual loops for creating new collections.

```python
# efficient comprehensions
squares = [x**2 for x in range(10)]
evens = [x for x in range(20) if x % 2 == 0]
user_dict = {user.id: user.name for user in users}
unique_names = {user.name for user in users}

# nested comprehensions
matrix = [[i*j for j in range(3)] for i in range(3)]
```

**❌ Bad - Manual collection building:**
Using loops to build lists, dicts, or sets is slower and more error-prone than comprehensions.

```python
# manual collection building
squares = []
for x in range(10):
    squares.append(x**2)

evens = []
for x in range(20):
    if x % 2 == 0:
        evens.append(x)

user_dict = {}
for user in users:
    user_dict[user.id] = user.name

unique_names = set()
for user in users:
    unique_names.add(user.name)
```

**✅ Good - Use generator expressions for memory efficiency:**
Generator expressions are memory-efficient for large datasets and can be chained with other functions.

```python
# memory efficient
total = sum(x**2 for x in range(1000000))
max_length = max(len(line) for line in file)
long_lines = (line for line in file if len(line) > 80)

# chaining generators
result = sum(x for x in (y**2 for y in range(100)) if x % 2 == 0)
```

**❌ Bad - Creating large lists unnecessarily:**
Building large lists in memory when you only need to iterate once wastes memory.

```python
# memory inefficient
squares = [x**2 for x in range(1000000)]
total = sum(squares)

lengths = [len(line) for line in file]
max_length = max(lengths)

long_lines = [line for line in file if len(line) > 80]
for line in long_lines:
    process(line)
```

**✅ Good - Use appropriate data structures:**
Choose the right data structure for the operation (set for membership testing, dict for lookups, etc.).

```python
# efficient lookups
user_ids = {user.id for user in users}  # set for fast membership
user_by_id = {user.id: user for user in users}  # dict for fast lookup

if user_id in user_ids:  # O(1) lookup
    user = user_by_id[user_id]  # O(1) lookup

# efficient counting
from collections import Counter
word_counts = Counter(text.split())
```

**❌ Bad - Using wrong data structures:**
Using lists for operations that should use sets or dicts leads to O(n) instead of O(1) operations.

```python
# inefficient lookups
user_ids = [user.id for user in users]  # list for membership testing
users_list = [(user.id, user) for user in users]  # list of tuples

if user_id in user_ids:  # O(n) lookup
    for uid, user in users_list:  # O(n) search
        if uid == user_id:
            break

# manual counting
word_counts = {}
for word in text.split():
    if word in word_counts:
        word_counts[word] += 1
    else:
        word_counts[word] = 1
```

### Type Hints

Use type hints consistently throughout the codebase:

```python
from typing import Any, Callable, Generator
from pathlib import Path

def process_files(
    file_paths: list[Path], 
    processor: Callable[[Path], str]
) -> Generator[str, None, None]:
    """Process multiple files using the provided processor function."""
    for file_path in file_paths:
        yield processor(file_path)
```

## Testing Guidelines

### Test Structure

Tests follow Django's testing patterns using pytest. All test files should be named `test_<module>.py` and use classes with OOP principles.

### Test Organization

```python
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
```

### Testing Patterns

#### Use pytest.mark.parametrize

Prefer `pytest.mark.parametrize` over fixtures for case-specific data to reduce code duplication:

```python
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
```

#### Mocking Strategy

Mock as much as possible to isolate units under test:

```python
@patch("next.pages.inspect.currentframe")
def test_context_decorator_detection(mock_frame):
    """Test context decorator file path detection."""
    # setup mock frame
    mock_frame.return_value.f_back.f_globals = {"__file__": "/test/path/page.py"}
    
    # test implementation
    result = page._get_caller_path()
    assert result == Path("/test/path/page.py")
```

#### Django Test Client

Use Django REST Framework test client for testing HTTP responses:

```python
def test_page_renders_correctly(client):
    """Test that pages render correctly with expected content."""
    response = client.get("/test-page/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Expected Content" in content
```

### Test Coverage Requirements

> [!IMPORTANT]
> **100% test coverage is mandatory for all code**

**Coverage Requirements:**
- **Main codebase: 100% coverage requirement** - All framework code must have complete test coverage
- **Examples: 100% coverage requirement** - All example code must have complete test coverage
- **CI validation**: Both main codebase and examples are automatically validated for 100% coverage
- **All new code must include comprehensive tests** - No code can be merged without proper test coverage

> [!NOTE]
> **Coverage tools used**: The project uses `pytest-cov` with `--cov-fail-under=100` to enforce 100% coverage. HTML reports are generated in `htmlcov/` directory for detailed analysis.

> [!WARNING]
> **CI will fail if coverage is below 100%** - Make sure to run `make test` locally before pushing to verify coverage requirements.

### Test File Naming

- Main tests: `test_<module>.py` (e.g., `test_pages.py`)
- Example tests: `tests.py` in each example directory
- Test classes: `Test<ClassName>` (e.g., `TestPageRendering`)

## Example Development

### New Feature Requirements

> [!IMPORTANT]
> **Every new feature must include a working example with 100% test coverage**

**Mandatory Requirements:**
- Each new feature requires a complete, working example in the `examples/` directory
- Examples must be self-contained and demonstrate the specific feature in isolation
- Examples must include comprehensive `tests.py` with 100% code coverage
- CI automatically validates that all examples have 100% test coverage
- Examples without proper tests will cause CI to fail

> [!WARNING]
> **Pull requests without examples will be rejected** - The maintainers will not review code that doesn't include proper examples and tests.

### Example Structure

Each example should be self-contained and demonstrate specific features:

```
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
```

### Example Configuration

Examples must include proper Django configuration in `conftest.py`:

```python
import sys
from pathlib import Path

import django
import pytest
from django.conf import settings
from django.test import Client

# add project root to python path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

# configure django settings for example
if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.admin",
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "next",
            "myapp",
        ],
        ROOT_URLCONF="config.urls",
        NEXT_PAGES=[
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": True,
            },
        ],
        USE_TZ=True,
        TIME_ZONE="UTC",
        ALLOWED_HOSTS=["testserver"],
    )
    django.setup()


@pytest.fixture
def client():
    """Django test client fixture."""
    return Client()
```

### Example Testing Requirements

Every example with Python code must include a `tests.py` file with 100% coverage:

```python
import pytest
from django.test import Client


@pytest.mark.parametrize(
    "url,expected_status",
    [
        ("/example/", 200),
        ("/example/param/123/", 200),
    ],
)
def test_example_pages_accessible(client, url, expected_status):
    """Test that example pages are accessible."""
    response = client.get(url)
    assert response.status_code == expected_status


def test_example_renders_correctly(client):
    """Test that example pages render with correct content."""
    response = client.get("/example/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Example Content" in content
```

## Documentation Standards

### Code Documentation

- **Module docstrings**: Provide comprehensive technical descriptions
- **Function docstrings**: Focus on purpose and usage, not parameter descriptions
- **Class docstrings**: Explain the class's role in the system architecture

### README Files

Each example should include a README.md explaining:
- Purpose and features demonstrated
- Setup instructions
- Usage examples
- Key concepts illustrated

### Inline Documentation

Use comments to explain complex logic and business rules:

```python
def _compose_layout_hierarchy(
    self, template_content: str, layout_files: list[Path]
) -> str:
    """
    Compose layout hierarchy by nesting layouts and inserting content.
    
    Processes layout files in order, with local layouts taking precedence
    over additional layouts from other NEXT_PAGES directories.
    """
    result = template_content

    # process all layout files in order (local layouts come first due to
    # how _find_layout_files builds the list)
    for layout_file in layout_files:
        with contextlib.suppress(OSError, UnicodeDecodeError):
            layout_content = layout_file.read_text(encoding="utf-8")
            result = layout_content.replace(
                "{% block template %}{% endblock template %}", result
            )
    return result
```

## Architecture Principles

### Design Patterns

The codebase extensively uses design patterns for maintainability and extensibility. Understanding these patterns is crucial for effective contribution to the framework.

**Core Design Patterns:**

- **Strategy Pattern**: Used for template loaders and router backends, allowing different implementations to be used interchangeably without changing client code. This enables runtime selection of different algorithms or behaviors.

- **Factory Pattern**: Used for creating router backends and other components, enabling runtime selection of different implementations based on configuration. This centralizes object creation logic and makes the system more flexible.

- **Facade Pattern**: Used in RouterManager for unified interface, providing a simple interface to complex subsystems. This hides the complexity of multiple router backends behind a single, easy-to-use interface.

- **Observer Pattern**: Implemented through Django's signal system and custom event handling, allowing components to react to changes in other parts of the system without tight coupling.

- **Template Method Pattern**: Used in abstract base classes like `TemplateLoader` and `RouterBackend`, defining the skeleton of algorithms while allowing subclasses to override specific steps.

- **Dependency Injection**: Used throughout the system to pass dependencies rather than hard-coding them, making components more testable and flexible.

- **Registry Pattern**: Used for managing template loaders, router backends, and other pluggable components, allowing dynamic registration and discovery of implementations.

- **Builder Pattern**: Used in complex object construction scenarios, particularly in URL pattern generation and template composition.

- **Singleton Pattern**: Used for global instances like the main `Page` and `RouterManager` objects, ensuring single instances across the application.

- **Command Pattern**: Used in the context management system, encapsulating requests as objects and allowing for queuing, logging, and undo operations.

### Plugin Architecture

The system supports comprehensive extensibility through a sophisticated plugin architecture that allows developers to extend functionality without modifying core code.

**Extensible Components:**

- **Template Loaders**: Can be registered and extended for custom template sources (database, API, file system, etc.). The system automatically tries loaders in order of registration, allowing custom loaders to override default behavior.

- **Router Backends**: Can be added dynamically for different URL generation strategies (file-based, database-driven, API-based, etc.). Multiple backends can be active simultaneously, each handling different URL patterns.

- **Context Processors**: Integrate seamlessly with Django's context processor system, allowing custom context data to be injected into templates. Both global and page-specific context processors are supported.

- **Custom Components**: All major components can be replaced with custom implementations through the registry system. This includes template loaders, router backends, context managers, and URL parsers.

- **Middleware Integration**: Custom middleware can be registered to extend request/response processing, enabling features like caching, authentication, and custom routing logic.

- **Signal Handlers**: Custom signal handlers can be registered to react to framework events, enabling features like automatic cache invalidation, logging, and custom processing.

The plugin system uses a registry-based approach where components register themselves at startup, allowing for dynamic discovery and instantiation. This design enables third-party packages to extend the framework without requiring modifications to the core codebase.

### Performance Considerations

The framework is designed with performance as a primary concern, implementing several optimization strategies:

**Lazy Loading Strategy:**
- Configurations are loaded only when first accessed, reducing startup time
- Templates are loaded on-demand rather than at startup, saving memory
- Router backends are instantiated only when needed, allowing for efficient resource usage

**Caching Mechanisms:**
- Template content is cached after first load to avoid repeated file system access
- URL patterns are cached to prevent repeated generation
- Context data is cached when possible to reduce computation overhead
- Router configurations are cached to avoid repeated settings parsing

**Efficient File System Operations:**
- Directory scanning uses optimized algorithms to minimize I/O operations
- File watching is implemented efficiently to detect changes without polling
- Template inheritance is resolved once and cached for subsequent requests

**Memory Management:**
- Large datasets are processed using generators to avoid memory bloat
- Template registries use weak references where appropriate to prevent memory leaks
- Context managers are designed to clean up resources automatically

**Database Optimization:**
- Query optimization through Django's ORM best practices
- Efficient pagination for large result sets
- Connection pooling and query caching where applicable

### Error Handling and Resilience

The framework implements comprehensive error handling strategies:

**Graceful Degradation:**
- Template loading failures fall back to alternative sources
- Router backend failures don't crash the entire system
- Missing context processors are handled gracefully

**Comprehensive Logging:**
- All major operations are logged with appropriate levels
- Error conditions are logged with sufficient context for debugging
- Performance metrics are logged for monitoring and optimization

**Validation and Checks:**
- Django's check framework is used extensively for configuration validation
- Runtime checks ensure system integrity
- Custom checks validate framework-specific requirements

### Security Considerations

Security is built into the framework architecture:

**Input Validation:**
- All user inputs are validated before processing
- Template content is sanitized to prevent XSS attacks
- URL parameters are validated against expected types and ranges

**Access Control:**
- Integration with Django's authentication and authorization systems
- Custom permission systems can be easily integrated
- Context processors can implement custom access control logic

**Safe Defaults:**
- All components have secure default configurations
- Dangerous operations require explicit opt-in
- Sensitive data is handled with appropriate care

## Pull Request Process

### Before Submitting

> [!IMPORTANT]
> **Complete all checks before submitting**

1. Run all quality checks: `make ci`
2. Ensure 100% test coverage for new code
3. Update documentation as needed
4. Follow the coding standards outlined above

> [!WARNING]
> **Incomplete pull requests will be closed** - Make sure all requirements are met before submitting.

### Pull Request Requirements

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

> [!NOTE]
> **Use draft pull requests for work in progress** - Mark your PR as draft if it's not ready for review yet.

### Code Review Process

**Review Focus Areas:**
- Maintainers will review for adherence to standards
- Focus on code quality, test coverage, and documentation
- Architecture and design pattern compliance
- Performance implications
- Security considerations

**Review Timeline:**
- Initial review within 2 business days
- Follow-up reviews within 1 business day
- Address feedback promptly and professionally

> [!TIP]
> **Respond to feedback constructively** - Use code review feedback as a learning opportunity and ask questions if something is unclear.

## Development Workflow

> [!IMPORTANT]
> **Follow this workflow for all contributions**

1. **Setup**: Run `make dev-setup` to configure your environment
2. **Develop**: Create feature branches and follow coding standards
3. **Test**: Run `make test-all` to ensure everything works
4. **Quality**: Run `make ci` to check code quality
5. **Submit**: Create pull request with comprehensive description

> [!WARNING]
> **Never commit directly to main branch** - Always create feature branches for your work.

### Branch Naming Convention

Use descriptive branch names that indicate the type of change:
- `feat/description` - New features
- `fix/description` - Bug fixes
- `docs/description` - Documentation updates
- `refactor/description` - Code refactoring
- `test/description` - Test improvements

### Commit Message Format

Follow conventional commit format:
```
type(scope): description

[optional body]

[optional footer]
```

Examples:
- `feat: refactor pages router`
- `feat(templates): add support for custom template loaders`
- `fix(routing): resolve URL pattern conflicts`
- `docs(api): update template loader documentation`

## Getting Help

> [!TIP]
> **Multiple ways to get help**

- Check existing issues and discussions
- Review the codebase for similar implementations
- Ask questions in pull request comments
- Follow the established patterns in the codebase

> [!NOTE]
> **The codebase is the source of truth** - When in doubt, examine how similar functionality is implemented and follow those patterns.

### Common Issues and Solutions

**Coverage Issues:**
- Use `make test-coverage` to see detailed coverage reports
- Check `htmlcov/index.html` for visual coverage analysis
- Ensure all code paths are tested

**Import Errors:**
- Verify all imports are at the top of files
- Check for circular import issues
- Use `uv run python -c "import module"` to test imports

**Test Failures:**
- Run `make test-fast` for quick feedback
- Check test output for specific error messages
- Ensure test data is properly set up
