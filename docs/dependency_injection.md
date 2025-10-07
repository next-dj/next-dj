# dependency injection in next.dj

overview

the next.dj framework allows context and render functions to declare typed
parameters such as request, session, and user, which are injected automatically
at runtime. this enables clean, readable function signatures without manual
plumbing for common objects.

how it works

dependency injection is handled by the dependencyresolver class, which inspects
type hints on each function. when a function parameter is annotated with a
supported type, such as request, session, or user, next.dj supplies the
corresponding object from the current request context. this matching is based
entirely on type hints and does not require any special decorators or naming.

usage notes

developers can always pass explicit keyword arguments to context or render
functions. if a kwarg is provided, it will override any injected value for that
parameter, ensuring that manual overrides are always respected.

testing and coverage

all dependency injection logic is fully covered by tests in
tests/test_dependencies.py. these tests verify correct injection, override
precedence, caching, and error handling for unsupported parameters.

examples

a working example of dependency injection in a page module can be found in
examples/di_example.py. this demonstrates how to declare typed parameters and
how next.dj injects the appropriate objects automatically.
