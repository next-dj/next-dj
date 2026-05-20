.. _faq-troubleshooting:

Troubleshooting
===============

This page lists the most common errors and warnings plus the actions that resolve them.

.. contents::
   :local:
   :depth: 2

Pages
-----

Page does not appear at the expected URL
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Confirm that the directory contains a ``page.py`` plus at least one body source: a ``render`` function, a ``template`` attribute, a ``template.djx``, or a sibling ``layout.djx``.
Confirm that the application is listed in ``INSTALLED_APPS`` and ``APP_DIRS=True`` in the page backend.

Run ``uv run python manage.py check`` and resolve every warning. The command runs Django's :doc:`system check framework <django:ref/checks>` together with the next.dj checks.

Page renders without layout
~~~~~~~~~~~~~~~~~~~~~~~~~~~

A layout must contain the placeholder block ``{% block template %}{% endblock template %}`` or its short form ``{% block template %}{% endblock %}``.
Without the placeholder the framework drops the body at render time without an error, and ``manage.py check`` reports :ref:`next.W001 <ref-system-checks>`.

Confirm that ``layout.djx`` sits in the same directory as ``page.py`` or in an ancestor directory.

next.W043 warning
~~~~~~~~~~~~~~~~~

A page module declares more than one body source.
Keep exactly one body source.
The choices are a ``render`` function, a ``template`` module attribute, or a sibling ``template.djx`` file.

``render`` raised ``TypeError``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``render`` must return ``str`` or a Django :class:`~django.http.HttpResponseBase` subclass.
Other values raise ``TypeError`` naming the ``page.py`` path.
See :doc:`/content/topics/pages`.

Forms
-----

HTTP 400 from form submission
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The dispatcher rejected the request because ``_next_form_page`` is missing or invalid.
Always render the form through ``{% form @action="name" %}`` or include both ``csrf_token`` and the ``_next_form_page`` field by hand.

HTTP 403 on POST
~~~~~~~~~~~~~~~~

CSRF token is missing or stale.
The ``{% form %}`` tag injects the token automatically.
Manual forms need ``{% csrf_token %}`` plus a fresh cookie.

next.E041 collision
~~~~~~~~~~~~~~~~~~~

Two actions are registered under the same name by different handlers.
Rename one of them or change its namespace to avoid the collision.

Components
----------

next.E020 or next.E034 collision
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Two components share the same name in the same scope.
Rename one or move one to a different page tree.

Component does not render
~~~~~~~~~~~~~~~~~~~~~~~~~

Confirm that ``COMPONENTS_DIR`` is set on ``DEFAULT_COMPONENT_BACKENDS``.
Confirm that the component folder name matches the string argument to ``{% component %}``.

Component prop does not resolve
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``{% component "card" title=some_var %}`` resolves ``some_var`` from the parent template context.
``{% component "card" title="some_var" %}`` is a literal string.
Pick the form that matches the value you want to pass.

Static
------

CSS or JS not loaded
~~~~~~~~~~~~~~~~~~~~

Confirm that ``{% collect_styles %}`` sits in the layout ``<head>`` and ``{% collect_scripts %}`` sits at the bottom of ``<body>``.
Confirm that the asset filename matches a registered stem and a registered kind.

Hashed URL does not change
~~~~~~~~~~~~~~~~~~~~~~~~~~

Restart the development server.
The watcher picks up file content changes but a hash computed at startup can stale during long sessions.

next.W030 empty static backends
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``manage.py check`` warns when ``DEFAULT_STATIC_BACKENDS`` is empty.
The framework falls back to the bundled ``StaticFilesBackend``, but you should either restore an explicit backend entry or accept that no custom chain is configured.

next.E038 duplicate BACKEND entries
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Two identical ``BACKEND`` dotted paths appear in ``DEFAULT_STATIC_BACKENDS``.
Remove or rename one entry so each backend class appears once.

next.W042 unusable JS_CONTEXT_SERIALIZER
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``JS_CONTEXT_SERIALIZER`` is set but does not resolve to a class that implements the ``JsContextSerializer`` protocol (a ``dumps`` method).
Fix the dotted path or install optional dependencies such as ``pydantic`` when using ``PydanticJsContextSerializer``.

Dependency Injection
--------------------

DependencyCycleError
~~~~~~~~~~~~~~~~~~~~~~

The resolver raises ``DependencyCycleError`` when two providers depend on each other.
Read the chain printed on the exception, remove one ``Depends`` edge, or merge providers.
See :doc:`/content/topics/dependency-injection` for request-cache interactions during form re-renders.

DI parameter resolves to None
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Three common causes explain this.

- The parameter annotation is a forward-reference string (often from ``from __future__ import annotations`` in modules where the resolver cannot evaluate it).
  Drop that import in ``page.py``, the ``page.py`` modules that declare inherited context, ``component.py``, and provider modules if markers stop resolving.

- No registered provider covers the marker type.
  The resolver leaves the parameter unset.
  Depending on the callable signature this surfaces as ``None`` or a normal Python ``TypeError``.

- The callable asks for data that is not in the request-scoped cache yet (for example the wrong phase of a form re-render).
  Compare your scenario with the lifecycle discussion in :doc:`/content/topics/dependency-injection`.

To inspect what the resolver would actually inject, use ``resolve_call`` from ``next.testing`` in a shell or test.
The snippet below assumes a non-bracketed ``notes/pages/notes/`` page module and a custom ``DTenant`` provider declared in the project.

.. code-block:: python

   from next.testing import resolve_call, make_resolution_context
   from notes.providers import DTenant
   from notes.pages.notes.page import notes

   resolved = resolve_call(notes, url_kwargs={"tenant_slug": "acme"})
   print(resolved)

``resolve_call`` returns the kwargs dict the resolver would pass to the callable.
Use ``make_resolution_context`` when you need finer control over the request, form, URL kwargs, or context data supplied to the resolver.

Custom marker not handled
~~~~~~~~~~~~~~~~~~~~~~~~~

Confirm that the provider class is imported during ``AppConfig.ready``.
``RegisteredParameterProvider`` registers at class creation, so the import must happen before the resolver caches the provider list.

Testing with custom providers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``reset_registries()`` clears the provider list as well as the dependency registry.
A custom provider registered in ``AppConfig.ready`` disappears after a ``reset_registries()`` call, which isolation tests often run in ``setUp`` or a fixture.
Re-register the provider inside the test or in a ``setUp`` method that runs after ``reset_registries()``.

.. code-block:: python

   from next.testing import reset_registries
   from myapp.providers import TenantProvider

   class TenantProviderTests(TestCase):
       def setUp(self):
           reset_registries()
           TenantProvider()  # re-registers at class creation

See :doc:`/content/howto/test-a-component-in-isolation` for the full isolation-test setup.

URL Resolution
--------------

Virtual routes and bracket directories
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A plain directory that contains only a ``template.djx`` and no ``page.py`` is a virtual route.
The router still maps it to a URL.

Captured-parameter directories (names in brackets) must contain ``page.py``, ``layout.djx``, ``template.djx``, or a child directory whose subtree includes ``page.py``.
Otherwise ``manage.py check`` reports :ref:`next.E010 <ref-system-checks>`.

URL name not found
~~~~~~~~~~~~~~~~~~

Run ``uv run python manage.py shell`` and print ``reverse("next:page_<name>")``.
If it raises ``NoReverseMatch``, verify that the directory contains at least one of ``page.py``, ``template.djx``, or a child page, and that it sits under an active ``PAGES_DIR`` root configured in ``DEFAULT_PAGE_BACKENDS``.

Captured parameter name differs from directory name
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The router normalises hyphens in directory names to underscores.
A directory named ``[my-id]`` produces the parameter ``my_id``, not ``my-id``.
Access it as ``DUrl[str]`` annotated ``my_id`` in your context function.
Rename the directory to ``[my_id]`` to avoid confusion.

Two pages collide under the same URL pattern
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The system check :ref:`next.E015 <ref-system-checks>` reports when the same Django URL pattern is produced from multiple sources.
This happens when two backends each walk a directory that maps to the same logical path.
Verify that ``DIRS`` and ``APP_DIRS`` in your page backends do not overlap.

Routes do not refresh
~~~~~~~~~~~~~~~~~~~~~

The bundled ``FileRouterBackend`` refreshes automatically when the dev server detects a filesystem change.
The manual ``router_manager.reload()`` call is only for a custom backend that reads routes from a non-filesystem source such as a database.
See :doc:`/content/howto/reload-routes-from-code`.

Template tags look undefined
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The framework registers ``{% form %}``, ``{% component %}``, ``{% collect_styles %}``, and related tags as Django builtins during ``AppConfig.ready``.
You normally **do not** ``{% load %}`` the ``next.templatetags.*`` libraries.
If the template engine reports ``Invalid block tag`` on one of these names, confirm ``next.apps.NextFrameworkConfig`` is listed in ``INSTALLED_APPS`` and run ``manage.py check`` before chasing import paths.

``template.djx`` edits and hot reload
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The dev watcher restarts the process when Python entrypoints such as ``page.py`` change.
Editing only ``template.djx`` or other DJX files refreshes rendered output without a full restart, but long sessions occasionally benefit from a manual server bounce if templates look stale.

Settings Behaviour
------------------

STRICT_CONTEXT causes unexpected exceptions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When ``STRICT_CONTEXT = True`` in ``NEXT_FRAMEWORK``, any context processor that raises ``TypeError``, ``ValueError``, ``AttributeError``, or ``KeyError`` re-raises the exception instead of logging a warning and continuing.
This is recommended in production to surface misconfigured processors immediately, but can expose exceptions in processors you did not write.
To debug, disable ``STRICT_CONTEXT`` temporarily and read the logged warning to identify the offending processor.
See :ref:`ref-settings` for the full description of this key.

Components are not available at startup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default ``LAZY_COMPONENT_MODULES = False``, which means ``component.py`` modules under configured component roots are imported during ``AppConfig.ready``.
If a module raises an import error at startup, the server does not start.
Set ``LAZY_COMPONENT_MODULES = True`` in ``NEXT_FRAMEWORK`` to defer imports for those roots until first resolve.
Components beside page routes may still load earlier when URL patterns are constructed.
This hides some startup errors but surfaces them when the failing component or route branch is first touched.

System Checks
-------------

``uv run python manage.py check`` runs every framework check and prints each one that fired with its code and a hint.
The check codes referenced above are defined in full in :doc:`/content/ref/system-checks`.

See Also
--------

.. seealso::

   :doc:`/content/ref/system-checks` for the full check catalog.
   :doc:`/content/topics/index` for in depth guides.
