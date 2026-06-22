.. _faq-troubleshooting:

Troubleshooting
===============

This page lists the most common errors and warnings plus the actions that resolve them.

.. contents::
   :local:
   :depth: 2

Pages
-----

Page Does Not Appear at the Expected URL
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Confirm that the directory contains a ``page.py`` plus at least one body source, a ``render`` function, a ``template`` attribute, a ``template.djx``, or a sibling ``layout.djx``.
Confirm that the application is listed in ``INSTALLED_APPS`` and ``APP_DIRS=True`` in the page backend.

Run ``uv run python manage.py check`` and resolve every warning.
The command runs Django's :doc:`system check framework <django:ref/checks>` together with the next.dj checks.

Page Renders Without Layout
~~~~~~~~~~~~~~~~~~~~~~~~~~~

A layout must contain the placeholder block ``{% block template %}{% endblock template %}`` or its short form ``{% block template %}{% endblock %}``.
Without the placeholder the framework drops the body at render time without an error, and ``manage.py check`` reports :ref:`next.W001 <ref-system-checks>`.

Confirm that ``layout.djx`` sits in the same directory as ``page.py`` or in an ancestor directory.

next.W043 Warning
~~~~~~~~~~~~~~~~~

A page module declares more than one body source.
Keep exactly one body source.
The choices are a ``render`` function, a ``template`` module attribute, or a sibling ``template.djx`` file.

``render`` Raised ``TypeError``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``render`` must return ``str`` or a Django :class:`~django.http.HttpResponseBase` subclass.
Other values raise ``TypeError`` naming the ``page.py`` path.
See :doc:`/content/topics/pages`.

Forms
-----

HTTP 400 From Form Submission
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The dispatcher rejected the request because ``_next_form_origin`` is missing or does not resolve against the URLconf.
Always render the form through ``{% form "name" %}`` or include both ``csrf_token`` and the ``_next_form_origin`` field by hand, set to the URL path of the page.
A form rendered by a hand-written view re-renders only when that view carries a ``next_page_path`` attribute, see :ref:`topics-forms-templates-handwritten-views`.
Under :func:`django.conf.urls.i18n.i18n_patterns` the same 400 appears when the user switches the language between the render and the submit, because the posted origin keeps the old language prefix and no longer resolves.

HTTP 403 on POST
~~~~~~~~~~~~~~~~

CSRF token is missing or stale.
The ``{% form %}`` tag injects the token automatically.
Manual forms need ``{% csrf_token %}`` plus a fresh cookie.

When the token is fine, the 403 can come from an access guard.
An authenticated user missing a ``Meta.permission_required`` permission gets ``PermissionDenied``.
A dynamic permission hook, ``check_permissions`` or ``has_object_permission``, returning ``False`` or raising ``PermissionDenied`` produces the same bare 403, see :ref:`topics-forms-actions-dynamic-guards`.
See :ref:`topics-forms-actions-guards`.

Form POST Redirects to the Login Page
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The action declares ``Meta.login_required`` or ``login_required=True`` on ``@action``, and the submission came from an anonymous session.
The dispatcher answers with a 302 to ``LOGIN_URL`` carrying ``next`` set to the origin page, before any POST data reaches the handler.
This is the declared behaviour, not an error.
Sign in, or hide the form from anonymous visitors in the template, since the guard protects the mutation and not the markup.

``MessageFailure`` on a Valid Submission
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The form declares ``Meta.success_message`` but the messages framework is not fully installed.
Add ``django.contrib.messages`` to ``INSTALLED_APPS`` and ``django.contrib.messages.middleware.MessageMiddleware`` to ``MIDDLEWARE``.
The framework raises rather than dropping the requested message silently, and ``manage.py check`` reports the gap upfront as :ref:`next.W061 <ref-system-checks>`.

next.E041 Collision
~~~~~~~~~~~~~~~~~~~

Two actions are registered under the same name by different handlers.
Rename one of them or move one to a different scope to avoid the collision.

Unknown Form Action
~~~~~~~~~~~~~~~~~~~

``{% form "name" %}``, ``{% action_url "name" %}``, ``NextClient.post_action``, ``resolve_action_url``, and ``build_form_for`` raise ``next.forms.FormActionNotFoundError`` when no registered action matches the name.
The message ends with ``Closest matches: ...`` listing the nearest registered names, so a typo is usually visible in the error itself.
Check the name against the suggestions, confirm the declaring module was imported before the lookup, and remember that a page-scoped action resolves only from its own page.

Wizard Draft Disappears Between Steps
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The default ``SessionFormWizardBackend`` stores drafts in the session, so a lost draft means the session itself did not survive.
Confirm that ``django.contrib.sessions`` is in ``INSTALLED_APPS`` and ``SessionMiddleware`` is enabled.
``manage.py check`` reports :ref:`next.W056 <ref-system-checks>` when sessions are missing while wizards are registered.
With ``CacheFormWizardBackend`` the usual cause is a local-memory cache under a multi-worker server, where each worker holds its own copy of the draft.
Point that backend at a shared cache such as Redis, and check that its ``TIMEOUT`` does not expire mid-flow.

``ImproperlyConfigured`` From a Wizard Step Save
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Both bundled wizard backends raise ``ImproperlyConfigured`` when a step is saved on a request without session support, see the previous entry.
``SessionFormWizardBackend`` also raises it when the step's ``cleaned_data`` holds a value its codec cannot encode, such as an unsaved model instance or a file object, and the error names the offending type.
Switch to ``CacheFormWizardBackend`` or a custom backend for cleaned data that does not fit the codec.
See :doc:`/content/topics/forms/wizard-backend` for the codec rules.

Components
----------

next.E020 or next.E034 Collision
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Two components share the same name in the same scope.
Rename one or move one to a different page tree.

Component Does Not Render
~~~~~~~~~~~~~~~~~~~~~~~~~

Confirm that ``COMPONENTS_DIR`` is set on ``COMPONENT_BACKENDS``.
Confirm that the component folder name matches the string argument to ``{% component %}``.

Component Prop Does Not Resolve
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``{% component "card" title=some_var %}`` resolves ``some_var`` from the parent template context.
``{% component "card" title="some_var" %}`` is a literal string.
Pick the form that matches the value you want to pass.

Static
------

CSS or JS Not Loaded
~~~~~~~~~~~~~~~~~~~~

Confirm that ``{% collect_styles %}`` sits in the layout ``<head>`` and ``{% collect_scripts %}`` sits at the bottom of ``<body>``.
Confirm that the asset filename matches a registered stem and a registered kind.

Hashed URL Does Not Change
~~~~~~~~~~~~~~~~~~~~~~~~~~

Restart the development server.
The watcher picks up file content changes but a hash computed at startup can stale during long sessions.

next.W030 Empty Static Backends
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``manage.py check`` warns when ``STATIC_BACKENDS`` is empty.
The framework falls back to the bundled ``StaticFilesBackend``, but you should either restore an explicit backend entry or accept that no custom chain is configured.

next.E038 Duplicate BACKEND Entries
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Two identical ``BACKEND`` dotted paths appear in ``STATIC_BACKENDS``.
Remove or rename one entry so each backend class appears once.

next.W042 Unusable JS_CONTEXT_SERIALIZER
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``JS_CONTEXT_SERIALIZER`` is set but does not resolve to a class that implements the ``JsContextSerializer`` protocol (a ``dumps`` method).
Fix the dotted path or install optional dependencies such as ``pydantic`` when using ``PydanticJsContextSerializer``.

Dependency Injection
--------------------

DependencyCycleError
~~~~~~~~~~~~~~~~~~~~

The resolver raises ``DependencyCycleError`` when two providers depend on each other.
Read the chain printed on the exception, remove one ``Depends`` edge, or merge providers.
See :doc:`/content/topics/dependency-injection` for request-cache interactions during form re-renders.

DI Parameter Resolves to None
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
The snippet below uses ``fetch_note``, the ``@context("note")`` callable from the :doc:`tutorial </content/intro/tutorial02>` detail page.

.. code-block:: python

   from next.testing import resolve_call, make_resolution_context

   def fetch_note(note_id):
       return None

   resolved = resolve_call(fetch_note, url_kwargs={"id": "1"})
   print(resolved)

Import the real ``fetch_note`` directly when the page module sits at an importable path.
``resolve_call`` returns the kwargs dict the resolver would pass to the callable.
Use ``make_resolution_context`` when you need finer control over the request, form, URL kwargs, or context data supplied to the resolver.

Custom Marker Not Handled
~~~~~~~~~~~~~~~~~~~~~~~~~

Confirm that the provider class is imported during ``AppConfig.ready``.
``RegisteredParameterProvider`` registers at class creation, so the import must happen before the resolver caches the provider list.

Testing With Custom Providers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``reset_registries()`` resets the form-action and component backends.
It does not touch the provider list, so a custom provider registered in ``AppConfig.ready`` survives the call.
To swap a provider for the duration of a test, use ``override_provider`` from ``next.testing``.
The context manager prepends the provider to the resolver list on entry and removes it on exit.

.. code-block:: python

   from next.testing import override_provider
   from myapp.providers import TenantProvider

   class TenantProviderTests(TestCase):
       def test_resolves_tenant(self):
           with override_provider(TenantProvider()):
               response = self.client.get("/dashboard/")
           self.assertEqual(response.status_code, 200)

See :doc:`/content/howto/test-a-component-in-isolation` for the full isolation-test setup.

URL Resolution
--------------

Virtual Routes and Bracket Directories
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A plain directory that contains only a ``template.djx`` and no ``page.py`` is a virtual route.
The router still maps it to a URL.

Captured-parameter directories (names in brackets) must contain ``page.py``, ``layout.djx``, ``template.djx``, or a child directory whose subtree includes ``page.py``.
Otherwise ``manage.py check`` reports :ref:`next.E010 <ref-system-checks>`.

URL Name Not Found
~~~~~~~~~~~~~~~~~~

Run ``uv run python manage.py shell`` and print ``reverse("next:page_<name>")``.
If it raises ``NoReverseMatch``, verify that the directory contains at least one of ``page.py``, ``template.djx``, or a child page, and that it sits under an active ``PAGES_DIR`` root configured in ``PAGE_BACKENDS``.

Captured Parameter Name Differs From Directory Name
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The router normalises hyphens in directory names to underscores.
A directory named ``[my-id]`` produces the parameter ``my_id``, not ``my-id``.
Access it as ``DUrl[str]`` annotated ``my_id`` in your context function.
Rename the directory to ``[my_id]`` to avoid confusion.

Two Pages Collide Under the Same URL Pattern
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The system check :ref:`next.E015 <ref-system-checks>` reports when the same Django URL pattern is produced from multiple sources.
This happens when two backends each walk a directory that maps to the same logical path.
Verify that ``DIRS`` and ``APP_DIRS`` in your page backends do not overlap.

Routes Do Not Refresh
~~~~~~~~~~~~~~~~~~~~~

The bundled ``FileRouterBackend`` refreshes automatically when the dev server detects a filesystem change.
The manual ``router_manager.reload()`` call is only for a custom backend that reads routes from a non-filesystem source such as a database.
See :doc:`/content/howto/reload-routes-from-code`.

Template Tags Look Undefined
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The framework registers ``{% form %}``, ``{% component %}``, ``{% collect_styles %}``, and related tags as Django builtins during ``AppConfig.ready``.
You normally **do not** ``{% load %}`` the ``next.templatetags.*`` libraries.
If the template engine reports ``Invalid block tag`` on one of these names, confirm ``next.apps.NextFrameworkConfig`` is listed in ``INSTALLED_APPS`` and run ``manage.py check`` before chasing import paths.

``template.djx`` Edits and Hot Reload
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The dev watcher restarts the process when Python entrypoints such as ``page.py`` change.
Editing only ``template.djx`` or other DJX files refreshes rendered output without a full restart, but long sessions occasionally benefit from a manual server bounce if templates look stale.

Settings Behaviour
------------------

STRICT_CONTEXT Causes Unexpected Exceptions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When ``STRICT_CONTEXT = True`` in ``NEXT_FRAMEWORK``, any context processor that raises ``TypeError``, ``ValueError``, ``AttributeError``, or ``KeyError`` re-raises the exception instead of logging a warning and continuing.
This is recommended in production to surface misconfigured processors immediately, but can expose exceptions in processors you did not write.
To debug, disable ``STRICT_CONTEXT`` temporarily and read the logged warning to identify the offending processor.
See :ref:`ref-settings` for the full description of this key.

Components Are Not Available at Startup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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
