.. _topics-forms-validation-rerender:

Validation and Re-render
========================

A failing POST does not produce an error page.
The dispatcher re-renders the origin page with the bound form, the cached dependencies, and a fresh CSRF token.
This page explains every step of the validation and re-render flow, why the origin page must be discoverable, and how to influence the re-render output from a context function.

.. contents::
   :local:
   :depth: 2

The Origin Page
---------------

Every rendered ``{% form %}`` tag emits a hidden ``_next_form_page`` field that contains the absolute path to the current ``page.py``.
The dispatcher reads that field to know which page rendered the form.

The dispatcher rejects submissions when the field is missing, when the path does not exist on disk, or when the path is outside ``BASE_DIR``.
Each rejection returns HTTP 400 with a structured error so it is easy to spot in logs.

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

Server Side Effects Before Validation
-------------------------------------

Side effects belong inside the handler, after ``form.is_valid()`` returns true.
The dispatcher does not call any side effect when validation fails.
This guarantee makes it safe to put database writes and external calls inside the handler.

A page level context function that has external side effects runs on every render including re-render.
Move side effects into the handler or into a custom provider whose result is cached on the request.

.. note::

   A context function that writes to the database or calls an external service runs a second time on every validation failure.
   Keep context functions read only and put writes inside the action handler where they run once on success.

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

Form class changed.
   When a form class is renamed or replaced the dispatcher rejects the submission with HTTP 400.
   The action UID is hashed from the action name, not the form class name.

Pre dispatch redirect from handler.
   A handler that returns ``HttpResponseRedirect`` skips the re-render path entirely.
   Use this on success, never on validation failure.

Virtual page origin.
   A directory with a ``template.djx`` and no ``page.py`` is a virtual route.
   It re-renders correctly on validation failure because the dispatcher composes the body from the template loader, not from a page module.

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
