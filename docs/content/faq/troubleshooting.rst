.. _faq-troubleshooting:

Troubleshooting
===============

This page lists the most common errors and warnings plus the actions that resolve them.

Run ``uv run python manage.py check --tag next`` to filter out the built-in Django and third-party checks and see only the next.dj diagnostics while investigating.

.. contents::
   :local:
   :depth: 2

Pages
-----

Page does not appear at the expected URL
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Confirm that the directory contains a ``page.py`` plus at least one body source, a ``render`` function, a ``template`` attribute, a ``template.djx``, or a sibling ``layout.djx``.
Confirm that the application is listed in ``INSTALLED_APPS`` and ``APP_DIRS=True`` in the page backend.

Run ``uv run python manage.py check`` and resolve every warning.
The command runs Django's :doc:`system check framework <django:ref/checks>` together with the next.dj checks.

Page renders without layout
~~~~~~~~~~~~~~~~~~~~~~~~~~~

A layout must contain the placeholder block ``{% block template %}{% endblock template %}`` or its short form ``{% block template %}{% endblock %}``.
Without the placeholder the framework skips that layout during composition, so its markup disappears from the rendered page while the body still renders through the remaining ancestor layouts.
``manage.py check`` reports :ref:`next.W001 <ref-system-checks>`.

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

next.E017 on a page.py that fails to import
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``page.py`` raised while importing, so the framework loads it as nothing.
Its ``render``, ``template``, and ``@context`` declarations never take effect, and a sibling ``template.djx`` can otherwise hide the failure.
The report names the exception type and message, so fix the named error and the module loads.
At request time the same failure raises under ``DEBUG`` or ``STRICT_LOADING``, see :doc:`/content/ref/pages`.

Page answers 404 although its files exist
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``page.py`` behind the URL raised while importing.
With ``DEBUG`` and ``STRICT_LOADING`` both off the framework answers 404 for the broken page while ``logger.exception`` records the traceback.
Read the server log for the ``Could not import page module`` record, or turn on ``DEBUG`` or ``NEXT_FRAMEWORK["STRICT_LOADING"]`` so the request raises with the real cause.
``uv run python manage.py check`` reports the same failure as :ref:`next.E017 <ref-system-checks>`, naming the exception type and message.
See :doc:`/content/ref/pages` for the full import-failure contract.

next.E018 on multiple keyless context functions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``page.py`` registers more than one keyless ``@context`` callable.
Keyless callables share one slot, so only the last one runs and the earlier ones are ignored.
Give each callable a key such as ``@context("name")``, or merge them into a single callable.

next.E029 on a keyless context function
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A keyless context callable must be annotated as returning a dict.
Keyless means the decorator carries no key, whether written as ``@context``, ``@page.context``, an aliased import, or an ``async def`` context function.
The check inspects every keyless form.

Two fixes clear the report.
Annotate the callable with a dict return type, either ``-> dict`` or a :class:`~typing.TypedDict`.
Alternatively give the decorator a key, for example ``@context("name")``, which makes the context keyed and exempt from the check.

Forms
-----

HTTP 400 from form submission
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

Form POST redirects to the login page
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The action declares ``Meta.login_required`` or ``login_required=True`` on ``@action``, and the submission came from an anonymous session.
The dispatcher answers with a 302 to ``LOGIN_URL`` carrying ``next`` set to the origin page, before any POST data reaches the handler.
This is the declared behaviour, not an error.
Sign in, or hide the form from anonymous visitors in the template, since the guard protects the mutation and not the markup.

``MessageFailure`` on a valid submission
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The form declares ``Meta.success_message`` but the messages framework is not fully installed.
Add ``django.contrib.messages`` to ``INSTALLED_APPS`` and ``django.contrib.messages.middleware.MessageMiddleware`` to ``MIDDLEWARE``.
The framework raises rather than dropping the requested message silently, and ``manage.py check`` reports the gap upfront as :ref:`next.W061 <ref-system-checks>`.

next.E041 collision
~~~~~~~~~~~~~~~~~~~

Two actions are registered under the same name by different handlers.
Rename one of them or move one to a different scope to avoid the collision.

Unknown form action
~~~~~~~~~~~~~~~~~~~

``{% form "name" %}``, ``{% action_url "name" %}``, ``NextClient.post_action``, ``resolve_action_url``, and ``build_form_for`` raise ``next.forms.FormActionNotFoundError`` when no registered action matches the name.
The message ends with ``Closest matches: ...`` listing the nearest registered names, so a typo is usually visible in the error itself.
Check the name against the suggestions, confirm the declaring module was imported before the lookup, and remember that a page-scoped action resolves only from its own page.

Wizard draft disappears between steps
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The default ``SessionFormWizardBackend`` stores drafts in the session, so a lost draft means the session itself did not survive.
Confirm that ``django.contrib.sessions`` is in ``INSTALLED_APPS`` and ``SessionMiddleware`` is enabled.
``manage.py check`` reports :ref:`next.W056 <ref-system-checks>` when sessions are missing while wizards are registered.
With ``CacheFormWizardBackend`` the usual cause is a local-memory cache under a multi-worker server, where each worker holds its own copy of the draft.
Point that backend at a shared cache such as Redis, and check that its ``TIMEOUT`` does not expire mid-flow.

``ImproperlyConfigured`` from a wizard step save
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Both bundled wizard backends raise ``ImproperlyConfigured`` when a step is saved on a request without session support, see the previous entry.
``SessionFormWizardBackend`` also raises it when the step's ``cleaned_data`` holds a value its codec cannot encode, such as an unsaved model instance or a file object, and the error names the offending type.
Switch to ``CacheFormWizardBackend`` or a custom backend for cleaned data that does not fit the codec.
See :doc:`/content/topics/forms/wizard-backend` for the codec rules.

Components
----------

next.E020 or next.E034 collision
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Two components share a name where the resolver has no rule to pick a winner, either under one route scope or at the root scope of two roots one template resolves against.
Rename one, or move one under a route scope so the deeper one wins where it applies.

Component does not render
~~~~~~~~~~~~~~~~~~~~~~~~~

Confirm that ``COMPONENTS_DIR`` is set on ``COMPONENT_BACKENDS``.
Confirm that the component folder name matches the string argument to ``{% component %}``.

Component renders as an empty string
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The name passed to ``{% component %}`` did not resolve from the rendering template, and with ``DEBUG`` and ``STRICT_LOADING`` both off the tag renders an empty string and logs a warning.
Turn on ``NEXT_FRAMEWORK["STRICT_LOADING"]`` to raise ``TemplateSyntaxError`` with a did-you-mean hint, or ``DEBUG`` to render a visible HTML comment in place of the component.
See :doc:`/content/ref/template-tags` for the three outcomes.

Component prop does not resolve
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``{% component "card" title=some_var %}`` resolves ``some_var`` from the parent template context.
``{% component "card" title="some_var" %}`` is a literal string.
Pick the form that matches the value you want to pass.

Component context returns a reserved key
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The render raises ``ValueError`` reading ``Component 'card' context returns 'title', reserved by the render path or by a prop of the calling tag``.
An unkeyed ``@component.context`` returned a dict whose key names a prop of the ``{% component %}`` call site being rendered, a reserved render key such as ``children``, ``request``, or ``csrf_token``, or any key starting with ``slot_``.
Rename the key, or register the value under an explicit ``@component.context("key")`` when overwriting is what you want.
The error leaves the render instead of degrading to an empty string, so neither ``STRICT_LOADING`` nor ``DEBUG`` suppresses it.
See :doc:`/content/topics/components` for the reserved set.

Static
------

CSS or JS not loaded
~~~~~~~~~~~~~~~~~~~~

Confirm that ``{% collect_styles %}`` sits in the layout ``<head>`` and ``{% collect_scripts %}`` sits at the bottom of ``<body>``.
Confirm that the asset filename matches a registered stem and a registered kind.

Hashed URL does not change
~~~~~~~~~~~~~~~~~~~~~~~~~~

Hashed URLs come from the staticfiles manifest, so re-run ``collectstatic`` after the file content changes.
The backend memoises each resolved asset URL for the life of the process, so restart the development server to pick up the new hash.

next.W030 empty static backends
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``manage.py check`` warns when ``STATIC_BACKENDS`` is empty.
The framework falls back to the bundled ``StaticFilesBackend``, but you should either restore an explicit backend entry or accept that no custom chain is configured.

next.E038 duplicate BACKEND entries
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Two identical ``BACKEND`` dotted paths appear in ``STATIC_BACKENDS``.
Remove or rename one entry so each backend class appears once.

next.W042 unusable JS_CONTEXT_SERIALIZER
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``JS_CONTEXT_SERIALIZER`` is set but does not resolve to a class that implements the ``JsContextSerializer`` protocol (a ``dumps`` method).
Fix the dotted path or install optional dependencies such as ``pydantic`` when using ``PydanticJsContextSerializer``.

Partial rendering
-----------------

Zone GET returns HTTP 400
~~~~~~~~~~~~~~~~~~~~~~~~~

A response body of ``unknown zone`` means the requested name matches no ``{% zone %}`` in the composed page.
Check that ``data-next-target`` names the same string as the zone tag on the page that serves the request.
A response body of ``zone in dynamic body`` means the page body comes from a ``render`` function returning a string, so there is no compiled template source to render the slice standalone.
Move the zone to a page whose body comes from a template source.

Zone GET returns HTTP 409
~~~~~~~~~~~~~~~~~~~~~~~~~

The client asserted an asset version that no longer matches the server's, which usually happens right after a deploy.
The empty-bodied 409 tells the runtime to perform a full visit of the current URL, so the page reloads once with fresh assets.
No action is needed beyond the reload the runtime already performs.

Inline script in a patch does not run
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The applier strips every ``<script>`` element from patch HTML before the markup reaches the document.
With the runtime's dev mode on, that is with Django ``DEBUG``, the runtime prints a ``console.warn`` for each neutralised script, so the removal is visible rather than silent.
Move the behaviour into a co-located module, see :doc:`/content/topics/partial-rendering/co-located-js`.

next.W071 extra PARTIAL_BACKENDS entries
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``PARTIAL_BACKENDS`` lists more than one entry.
Partial rendering activates only the first entry and ignores the rest, so remove the extra entries or merge their options into one.

next.E067 PARTIAL_BACKENDS is not a list
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``PARTIAL_BACKENDS`` holds a tuple, a bare dict, or a dotted path rather than a list of config dicts.
The framework merges the key only from a list, so the configured backend never loads and the default protocol backend serialises the wire format instead.
Wrap the entry in a list.

Dependency injection
--------------------

DependencyCycleError
~~~~~~~~~~~~~~~~~~~~

The resolver raises ``DependencyCycleError`` when two providers depend on each other.
Read the chain printed on the exception, remove one ``Depends`` edge, or merge providers.
See :doc:`/content/topics/dependency-injection` for request-cache interactions during form re-renders.

DI parameter resolves to None
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Three common causes explain this.

- The parameter annotation is a forward-reference string (often from ``from __future__ import annotations`` in modules where the resolver cannot evaluate it).
  Drop that import in ``page.py``, the ``page.py`` modules that declare inherited context, ``component.py``, and provider modules if markers stop resolving.

- No registered provider covers the marker type.
  The resolver passes ``None``, or the declared default when one exists, so the callable receives the parameter and fails only when its body cannot handle ``None``.

- The callable asks for data that is not in the request-scoped cache yet (for example the wrong phase of a form re-render).
  Compare your scenario with the lifecycle discussion in :doc:`/content/topics/dependency-injection`.

To inspect what the resolver would actually inject, use ``resolve_call`` from ``next.testing.deps`` in a shell or test.
The snippet below uses ``fetch_note``, the ``@context("note")`` callable from the :doc:`tutorial </content/intro/tutorial02>` detail page.

.. code-block:: python

   from next.testing.deps import resolve_call
   from next.urls import DUrl

   def fetch_note(note_id: DUrl["id", int]):
       return None

   resolved = resolve_call(fetch_note, url_kwargs={"id": "1"})
   print(resolved)

Import the real ``fetch_note`` directly when the page module sits at an importable path.
``resolve_call`` returns the kwargs dict the resolver would pass to the callable.
Use ``make_resolution_context`` from the same module when you need finer control over the request, form, URL kwargs, or context data supplied to the resolver.

Custom marker not handled
~~~~~~~~~~~~~~~~~~~~~~~~~

Confirm that the provider class is imported during ``AppConfig.ready``.
``RegisteredParameterProvider`` registers at class creation, so the import must happen before the resolver caches the provider list.

Testing with custom providers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``reset_registries()`` resets the form-action and component backends.
It does not touch the provider list, so a custom provider registered in ``AppConfig.ready`` survives the call.
To swap a provider for the duration of a test, use ``override_provider`` from ``next.testing.patching``.
The context manager prepends the provider to the resolver list on entry and removes it on exit.

.. code-block:: python

   from django.test import TestCase

   from next.testing.patching import override_provider
   from myapp.providers import TenantProvider

   class TenantProviderTests(TestCase):
       def test_resolves_tenant(self):
           with override_provider(TenantProvider()):
               response = self.client.get("/dashboard/")
           self.assertEqual(response.status_code, 200)

See :doc:`/content/howto/test-a-component-in-isolation` for the full isolation-test setup.

URL resolution
--------------

Virtual routes and bracket directories
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A plain directory that contains only a ``template.djx`` and no ``page.py`` is a virtual route.
The router still maps it to a URL.

Captured-parameter directories (names in brackets) must contain ``page.py``, ``layout.djx``, ``template.djx``, or a direct child directory that contains ``page.py``.
Otherwise ``manage.py check`` reports :ref:`next.E010 <ref-system-checks>`.

URL name not found
~~~~~~~~~~~~~~~~~~

Run ``uv run python manage.py shell`` and print ``reverse("next:page_<name>")``.
If it raises ``NoReverseMatch``, verify that the directory contains at least one of ``page.py``, ``template.djx``, or a child page, and that it sits under an active ``PAGES_DIR`` root configured in ``PAGE_BACKENDS``.

Captured parameter name differs from directory name
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The router normalises hyphens in directory names to underscores.
A directory named ``[my-id]`` produces the parameter ``my_id``, not ``my-id``.
Access it as ``DUrl[str]`` annotated ``my_id`` in your context function.
Rename the directory to ``[my_id]`` to avoid confusion.

Two pages collide under the same URL pattern
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The system check :ref:`next.E015 <ref-system-checks>` reports when the same Django URL pattern is produced from multiple sources.
This happens when two backends each walk a directory that maps to the same logical path.
Verify that ``DIRS`` and ``APP_DIRS`` in your page backends do not overlap.

Routes do not refresh
~~~~~~~~~~~~~~~~~~~~~

The bundled ``FileRouterBackend`` refreshes automatically when the dev server detects a filesystem change.
The manual ``router_manager.reload()`` call is only for a custom backend that reads routes from a non-filesystem source such as a database.
See :doc:`/content/howto/reload-routes-from-code`.

Template tags look undefined
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The framework registers ``{% form %}``, ``{% component %}``, ``{% collect_styles %}``, and related tags as Django builtins during ``AppConfig.ready``.
You normally **do not** ``{% load %}`` the ``next.templatetags.*`` libraries.
If the template engine reports ``Invalid block tag`` on one of these names, confirm ``next.apps.NextFrameworkConfig`` is listed in ``INSTALLED_APPS`` and run ``manage.py check`` before chasing import paths.

``template.djx`` edits and hot reload
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The dev watcher restarts the process when Python entrypoints such as ``page.py`` change.
Editing only ``template.djx`` or other DJX files refreshes rendered output without a full restart.
The composed template cache is invalidated by file mtime.

Settings behaviour
------------------

STRICT_CONTEXT causes unexpected exceptions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When ``STRICT_CONTEXT = True`` in ``NEXT_FRAMEWORK``, any context processor that raises ``TypeError``, ``ValueError``, ``AttributeError``, or ``KeyError`` re-raises the exception instead of logging a warning and continuing.
This is recommended in production to surface misconfigured processors immediately, but can expose exceptions in processors you did not write.
To debug, disable ``STRICT_CONTEXT`` temporarily and read the logged warning to identify the offending processor.
See :ref:`ref-settings` for the full description of this key.

Components are not available at startup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default ``LAZY_COMPONENT_MODULES = False``, which means ``component.py`` modules under configured component roots are imported during ``AppConfig.ready``.
If a module raises an import error at startup, the server does not start.
Set ``LAZY_COMPONENT_MODULES = True`` in ``NEXT_FRAMEWORK`` to defer imports for those roots until first resolve.
Components beside page routes may still load earlier when URL patterns are constructed.
This hides some startup errors but surfaces them when the failing component or route branch is first touched.

Merged NEXT_FRAMEWORK settings are immutable
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Appending to ``next_framework_settings.PAGE_BACKENDS``, assigning into it, or mutating a nested list or mapping raises ``TypeError`` reading ``Merged NEXT_FRAMEWORK settings are immutable``.
The merge hands out frozen containers so that one caller cannot reshape the configuration every other reader sees.
Change ``settings.NEXT_FRAMEWORK`` and call ``next_framework_settings.reload()``, or wrap the change in ``override_settings``, which reloads on its own.
Reading is untouched, so ``isinstance``, equality against a plain container, and ``json.dumps`` still work on a merged value.
See :ref:`ref-conf` for the rest of the contract.

System checks
-------------

``uv run python manage.py check`` runs every framework check and prints each one that fired with its code and a hint.
The check codes referenced above are defined in full in :doc:`/content/ref/system-checks`.

See also
--------

.. seealso::

   :doc:`/content/ref/system-checks` for the full check catalog.
   :doc:`/content/topics/index` for in depth guides.
