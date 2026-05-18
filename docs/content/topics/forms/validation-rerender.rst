.. _topics-forms-validation-rerender:

Validation and Re-render
========================

A failing POST does not produce an error page.
The dispatcher re-renders the origin page with the bound form, the cached dependencies, and a fresh CSRF token.
This page explains every step of the validation and re-render flow, why the origin page must be discoverable, and how to influence the re-render output from a context function.

.. contents::
   :local:
   :depth: 2

.. _topics-forms-validation-rerender-origin:

The Origin Page
---------------

Every rendered ``{% form %}`` tag emits a hidden ``_next_form_page`` field that contains the absolute path to the current ``page.py``.
The dispatcher reads that field to know which page rendered the form.

The dispatcher rejects a submission when the field is missing or blank, when its basename is not ``page.py``, or when resolving the path raises ``OSError``.
It also rejects the submission when ``BASE_DIR`` is unset or when the resolved path falls outside ``BASE_DIR``.
A path whose ``page.py`` does not exist is rejected too, unless a sibling ``template.djx`` stands in for it.
Each rejection returns HTTP 400 with a structured error so it is easy to spot in logs.

Virtual routes are fully supported as origin pages.
A directory that has only a ``template.djx`` and no ``page.py`` is a virtual route (see :doc:`/content/topics/file-router` for the routing rules).
The ``{% form %}`` tag on such a page emits ``_next_form_page`` pointing at a non-existent ``page.py`` path.
The dispatcher accepts this: if ``page.py`` does not exist but a sibling ``template.djx`` does, the path is considered valid.
On validation failure the re-render composes the body from the template loader exactly as the initial render did, with no page module involved.

The Render Pipeline
-------------------

A request to ``/_next/form/<uid>/`` follows a fixed pipeline.

1. The dispatcher resolves the action UID to its handler and form class.
2. The form is constructed with POST data and the captured URL kwargs.
3. ``form.is_valid()`` runs.
4. On valid form, the handler is called with the dependency-resolved parameters and its return value goes to the client.
5. On invalid form, the dispatcher loads the origin page, reattaches the cached dependencies, and re-renders the template with ``form`` set to the bound failing form.

The pipeline stays inside the same request.
A failing form does not redirect, the user stays on the same URL.

What Survives Re-render
-----------------------

One important thing carries over from the initial render.

Dependency cache.
   Every value produced by the resolver during dispatch is stored on the request under ``REQUEST_DEP_CACHE_ATTR``.
   The re-render reuses each cached value without rerunning the provider.
   A custom DI provider must therefore be idempotent across a render cycle.

.. tip::

   Shared dependencies between the action handler and page context functions are resolved only once per request.
   If your handler and a ``@context`` function both declare ``Depends("current_user")``, the provider runs on the first resolution and the cached value is returned on every subsequent call within the same request.
   This means that a failed form validation does not re-query the database for values that were already produced during initial dispatch.
   Register expensive lookups (tenant queries, permission checks) as named dependencies to get this caching for free.

The frozen ``FormSpec`` descriptors in :doc:`serializers` are a separate user-facing tool.
The dispatcher does not attach a ``FormSpec`` to the request on its own.

What Restarts on Re-render
--------------------------

Other parts of the render restart from scratch.

Page module body source.
   The ``render`` function or the ``template.djx`` body runs again.
   The bound form is in scope this time.

Layout chain.
   Every ancestor ``layout.djx`` renders again.
   Layout level context functions still run, with the cached values where the cache hit, with fresh evaluation where it did not.

Static collector.
   The collector restarts so the rendered HTML contains the right set of asset links.

The Bound Form Variable
-----------------------

On the re-rendered page the variable ``form`` is the bound form with errors.
The template can render error messages inline with each field.

A page may publish its own ``form`` through ``@context("form")``.
On re-render the dispatcher replaces that value with the bound failing form so the user sees the input that triggered the failure.

Multiple Forms On The Same Page
-------------------------------

A page that hosts several actions only re-renders the failing form.
Other forms render as unbound, with their original ``@context("...")`` values intact.

This works because every action has its own UID and only one URL fires the re-render.
The dispatcher does not rerun the validation of any other form.

Influencing the Re-render
-------------------------

One hook lets a page customise the re-render output.

Custom ``form`` context.
   Override ``@context("form")`` to construct the form in a particular way on the initial render.
   The dispatcher reuses the same key when populating the bound failing form on re-render.

Redirecting Back to the Origin
------------------------------

A handler that succeeds usually returns an ``HttpResponseRedirect``.
When the action can be submitted from more than one page, hardcoding that target sends every caller to the same place.
The ``redirect_to_origin`` helper sends the user back to whichever page rendered the form.

.. code-block:: python
   :caption: notes/routes/page.py

   from django.http import HttpRequest

   from next.forms import action, redirect_to_origin
   from next.urls import DUrl


   @action("toggle_favourite")
   def toggle_favourite(note_id: DUrl["id", int], request: HttpRequest):
       Note.objects.filter(pk=note_id).update(favourite=True)
       return redirect_to_origin(request, fallback="/notes/")

``redirect_to_origin(request, fallback="/")`` reads the hidden ``_next_form_origin`` field that the ``{% form %}`` tag emits with the request path of the rendering page.
It accepts the value only when it is a string that starts with a single ``/``.
A protocol-relative input beginning with ``//`` is rejected, which blocks open-redirect input.
When the field is absent or fails validation the helper redirects to ``fallback`` instead.

Origin Versus Page Fields
~~~~~~~~~~~~~~~~~~~~~~~~~

Two hidden fields travel with every submission and serve different roles.

``_next_form_page``.
   The absolute filesystem path to the ``page.py`` that rendered the form.
   The dispatcher uses it to locate the origin module for the re-render.
   It is a disk path, never a URL.

``_next_form_origin``.
   The request path the form was rendered under, such as ``/notes/42/``.
   It is consumed only by ``redirect_to_origin`` on the success path.

The re-render path uses ``_next_form_page``. The success-redirect path uses ``_next_form_origin``.
A failing form ignores ``_next_form_origin`` because the re-render stays on the same URL without a redirect.

Server Side Effects Before Validation
-------------------------------------

Side effects belong inside the handler, after ``form.is_valid()`` returns true.
The dispatcher does not call any side effect when validation fails.
This guarantee makes it safe to put database writes and external calls inside the handler.

.. warning::

   A page level context function runs again on every re-render, so any write or external call it makes happens a second time on every validation failure.
   Keep context functions read only. Put writes inside the action handler where they run once on success, or in a custom provider whose result is cached on the request.

Signals
-------

Two signals fire during the validation pipeline.

``form_validation_failed``.
   Fires when ``form.is_valid()`` returns false.
   Payload carries ``action_name``, ``error_count``, and ``field_names``.

``action_dispatched``.
   Fires after the handler returns successfully.
   Payload carries ``action_name``, ``form``, ``url_kwargs``, ``duration_ms``, ``response_status``, and ``dep_cache``.

See :doc:`signals` for the full list and payload shapes.

Edge Cases
----------

Missing ``_next_form_page`` field.
   The dispatcher returns HTTP 400.
   Plain HTML forms must include the field explicitly.

Origin page renamed or deleted.
   The dispatcher returns HTTP 400 when the path no longer exists.
   Schedule a router reload after restructuring page directories to keep current renders consistent.

Form class renamed.
   Renaming the form class has no effect on the UID.
   The UID is hashed from the action name.
   A submission only fails when the origin ``page.py`` path becomes invalid.

Pre dispatch redirect from handler.
   A handler that returns ``HttpResponseRedirect`` skips the re-render path entirely.
   Use this on success, never on validation failure.

Virtual page origin.
   Covered in detail under :ref:`The Origin Page <topics-forms-validation-rerender-origin>` above.
   Routes backed only by a sibling ``template.djx`` (no ``page.py``) still resolve an origin path for re-render.

CSRF token on re-render.
   The re-render runs the ``{% form %}`` tag again, which calls Django's ``get_token`` and emits a fresh ``csrfmiddlewaretoken`` hidden input.
   The token the browser already holds in its cookie stays valid, so the resubmission after a correction passes CSRF without a page reload.
   A re-render never reuses the token string from the failed POST. It always emits the current one.

File uploads.
   An invalid submission does not preserve uploaded files.
   The HTTP spec does not let a server re-populate an ``<input type="file">``, so the bound form on the re-render has no file data and the user must pick the file again.
   The bound form still reports a missing-file error on the field when the upload was required.
   Always set ``enctype="multipart/form-data"`` on the ``{% form %}`` tag so the dispatcher receives ``request.FILES`` in the first place.

Partial form state.
   Every text, select, checkbox, and textarea value survives the re-render because the dispatcher binds the failing form to ``request.POST`` and the template renders the bound widgets.
   ``cleaned_data`` holds only the fields that passed validation. Fields that failed keep their raw submitted value on the widget so the user can correct them in place.
   Password inputs are the exception. Django widgets clear them on render unless ``render_value=True`` is set on the widget.

Wrong-origin re-render.
   When a re-render shows the wrong page, the cause is almost always a stale or hand-built ``_next_form_page`` field.
   The dispatcher trusts that field for the origin, so a form copied between pages or a hand-crafted ``<form>`` with a hardcoded path re-renders against the wrong module.
   Let the ``{% form %}`` tag emit the field. It writes the path of the page that actually rendered the form.
   A hand-built form must set ``_next_form_page`` to ``{{ current_page_module_path }}``, which the framework publishes on every rendered page.
   When the field points outside ``BASE_DIR`` or at a path that no longer exists, the dispatcher returns HTTP 400 rather than rendering the wrong page.

Common Patterns
---------------

Inline Errors
~~~~~~~~~~~~~

Render ``{{ form.field.errors }}`` next to each input.
The re-render shows the previous value and the error in one place.

Cross Field Validation
~~~~~~~~~~~~~~~~~~~~~~

Use Django ``clean`` and ``clean_<field>`` methods on the form class.
The dispatcher treats these failures the same as field validation failures.

Audit Trail
~~~~~~~~~~~

Subscribe to ``form_validation_failed`` to log every rejected attempt.
The signal fires once per failed submission so log volume scales with failure rate, not request rate.

See Also
--------

.. seealso::

   :doc:`actions` for handler patterns.
   :doc:`templates` for the ``{% form %}`` tag and ``_next_form_page``.
   :doc:`backends` for swapping the validation backend.
   :doc:`/content/internals/action-dispatch` for the full pipeline.
