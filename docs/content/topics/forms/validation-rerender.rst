.. _topics-forms-validation-rerender:

Validation and Re-render
========================

When a submission fails validation, users keep what they typed and you write no re-render code.
A failing POST does not produce an error page.
The dispatcher re-renders the origin page with the bound form, the cached dependencies, and a fresh CSRF token, so the page comes back with the entered values and field errors in place.
This page explains the validation and re-render flow end to end.

.. contents::
   :local:
   :depth: 2

.. _topics-forms-validation-rerender-origin:

The Origin Page
---------------

Every rendered ``{% form %}`` tag emits a hidden ``_next_form_origin`` field that carries the URL path of the page that rendered the form, such as ``/notes/42/``.
On the re-render branch the dispatcher resolves that path against the URLconf with :func:`django.urls.resolve`.
The file router stamps every routed view with a ``next_page_path`` attribute, so the match yields the origin page module, and the URL kwargs come through the real URL converters.
An ``[int:id]`` capture therefore arrives as ``int`` on the re-render exactly as on the canonical GET, and a ``uuid`` capture arrives as ``UUID``.

On a validation failure the dispatcher rejects the submission and returns ``HTTP 400 Missing or invalid _next_form_origin`` when any of the following holds.

- The field is missing or blank.
- The value is not a same-site path, one that starts with a single ``/``.
- The path does not resolve against the URLconf.
- The resolved view carries no ``next_page_path`` attribute, which is the case for a hand-written view that has not opted in (see :ref:`topics-forms-templates-handwritten-views`).

The resolution respects the deployment context.
A script prefix is stripped through :func:`django.urls.get_script_prefix` before the lookup, and a per-request URLconf set on ``request.urlconf`` is passed to ``resolve``, so projects mounted under a prefix and multi-tenant URLconfs both work.

Virtual routes are fully supported as origin pages.
A directory that has only a ``template.djx`` and no ``page.py`` is a virtual route (see :doc:`/content/topics/file-router` for the routing rules).
The router stamps the synthesised ``page.py`` location on the virtual route's view as well, so its origin resolves like any other page.
On validation failure the re-render composes the body from the template loader exactly as the initial render did, with no page module involved.

The Render Pipeline
-------------------

A request to ``/_next/form/<uid>/`` follows a fixed pipeline.

1. The dispatcher resolves the action UID to its handler and form class.
2. The form is constructed with POST data, uploaded files, and the initial data that ``get_initial`` returns.
3. ``form.is_valid()`` runs.
4. On valid form, the handler is called with the dependency-resolved parameters and its return value goes to the client.
5. On invalid form, the dispatcher loads the origin page, reattaches the cached dependencies, and re-renders the template with ``form`` set to the bound failing form.

The pipeline stays inside the same request.
A failing form does not redirect, the user stays on the same URL.

One Response Funnel
-------------------

Every outcome of the pipeline leaves through one funnel.
The active backend's ``shape_response`` turns the dispatch outcome — a handler return value, a wizard advance, or an invalid form — into the ``HttpResponse`` sent to the client.
For an invalid form the default envelope asks the backend's ``render_invalid_page`` to produce the re-rendered page HTML.
A custom backend overrides that one method to change how validation errors render, without touching the rest of the pipeline.

The default backend answers an invalid submission with HTTP 200, the full origin page, and the headers ``X-Next-Form: invalid`` and ``X-Next-Action: <uid>``.
A success re-render — a handler that returned ``None`` — carries no such headers, so a client can branch on the headers without scraping the HTML.
The status and the headers are behaviour of the default backend, not a guarantee of the endpoint, and the ``X-Next-*`` header namespace is reserved for the framework.
See :doc:`backends` for the override signature and the bundled implementation.

What Survives Re-render
-----------------------

One thing carries over from the initial render.

Dependency cache.
   Read the per-request cache through ``next.deps.get_request_dep_cache(request)``.
   The dispatcher stores it on the request under the attribute named by ``REQUEST_DEP_CACHE_ATTR`` so the helper can find it.
   The re-render reuses each cached value without rerunning the provider.
   A custom DI provider must therefore be idempotent across a render cycle.

.. tip::

   A dependency shared between the action handler and a page context function resolves only once per request, so a failed validation never re-queries it.
   Register expensive lookups such as tenant queries and permission checks as named dependencies to get this caching for free.

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

On re-render the dispatcher always supplies the bound failing form under the action-named context key so the user sees the input that triggered the failure.
``get_initial`` still runs on the POST bind to seed the form's ``initial``, but the submitted values win in the rendered fields.

Multiple Forms On The Same Page
-------------------------------

A page that hosts several actions only re-renders the failing form.
Other forms render as unbound, with their original ``@context("...")`` values intact.

This works because every action has its own UID and only one URL fires the re-render.
The dispatcher does not rerun the validation of any other form.

Influencing the Re-render
-------------------------

One hook lets a page customise the form before it reaches the template.

The ``get_initial`` hook.
   Override ``get_initial`` on the form class to shape the initial data or the bound model instance.
   The hook runs on the initial GET render and again on every POST bind, where its return value seeds the bound form's ``initial`` and, for a ModelForm, its ``instance``.
   On the re-render the submitted POST values win over the seeded initial, so the user sees what they typed.
   The one path that skips ``get_initial`` on POST is a ``form_class`` factory that returns a ``(FormClass, init_kwargs)`` tuple, see :doc:`actions`.

Redirecting Back to the Origin
------------------------------

A handler that succeeds usually returns an ``HttpResponseRedirect``.
When the action can be submitted from more than one page, hardcoding that target sends every caller to the same place.
The ``redirect_to_origin`` helper sends the user back to whichever page rendered the form.

.. code-block:: python
   :caption: notes/pages/page.py

   from django.http import HttpRequest
   from next.forms import action, redirect_to_origin
   from next.urls import DUrl

   @action("toggle_favourite")
   def toggle_favourite(note_id: DUrl["id", int], request: HttpRequest):
       Note.objects.filter(pk=note_id).update(favourite=True)
       return redirect_to_origin(request, fallback="/notes/")

``redirect_to_origin(request, fallback="/")`` reads the hidden ``_next_form_origin`` field that the ``{% form %}`` tag sets to ``request.path`` verbatim at render time.
It accepts the value only when it is a string that starts with a single ``/``.
A protocol-relative input beginning with ``//`` is rejected, which blocks open-redirect input.
When the field is absent or fails validation the helper redirects to ``fallback`` instead.

One Field, Two Roles
~~~~~~~~~~~~~~~~~~~~

The single hidden ``_next_form_origin`` field serves both directions of the round trip.

Re-render path.
   The dispatcher resolves the field through the URLconf to recover the origin page module and the typed URL kwargs, as :ref:`topics-forms-validation-rerender-origin` describes.

Success-redirect path.
   ``redirect_to_origin`` reads the same field as the redirect target, without resolving it.

A failing form never redirects, the re-render stays on the dispatch URL.

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

- A missing or unresolvable ``_next_form_origin`` field returns HTTP 400 on the invalid branch. A plain HTML form must set the field to the URL path of its page, the value the tag emits.
- A route removed in a deploy between the render and the POST no longer resolves, so the invalid branch returns HTTP 400.
- Under :func:`django.conf.urls.i18n.i18n_patterns` the origin resolves under the active language of the POST. A user who switches the language between the render and the submit posts an origin whose language prefix no longer resolves, and the invalid branch returns HTTP 400. The success path never resolves the origin and is unaffected.
- The UID is hashed from the scope key and the action name, not the name alone. For a page-scoped form the scope key is the absolute ``page.py`` path, so moving the file or renaming the class changes the UID. For a shared form the scope key is the dotted module, so moving the module changes the UID. See :ref:`UID stability <topics-forms-actions-uid>` in :doc:`actions`.
- A handler that returns ``HttpResponseRedirect`` skips the re-render path entirely. Use this on success only.
- Virtual page origins backed by ``template.djx`` resolve through the template loader, as :ref:`topics-forms-validation-rerender-origin` explains above.
- The re-render emits a fresh ``csrfmiddlewaretoken`` through ``get_token``, so the browser cookie stays valid and the resubmission passes CSRF without a reload.
- File inputs reset on re-render because the HTTP spec does not let a server re-populate them. Set ``enctype="multipart/form-data"`` and re-prompt the user for the upload.
- Text, select, checkbox, and textarea widgets keep their raw submitted values because the dispatcher binds the failing form to ``request.POST``. Password widgets clear unless ``render_value=True``.

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
   :doc:`templates` for the ``{% form %}`` tag and ``_next_form_origin``.
   :doc:`backends` for swapping the dispatch backend.
   :doc:`/content/internals/action-dispatch` for the full pipeline.
