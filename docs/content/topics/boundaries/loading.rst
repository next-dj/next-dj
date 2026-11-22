.. _topics-boundaries-loading:

Loading UI and delivery modes
=============================

A ``loading.djx`` declares a deferred boundary.
The response renders it first and fills the boundary once its job settles, in the same response for a ``stream`` boundary and on a later request for a client-trip one.

.. contents::
   :local:
   :depth: 1

Page boundaries
---------------

A page looks for ``loading.djx`` and ``error.djx`` in its own directory and in every directory above it inside the page tree, the walk ``layout.djx`` follows.
Past the page tree the files follow the layouts, so the ``loading.djx`` and ``error.djx`` beside a ``layout.djx`` that wraps the page from another tree, such as the root of a ``DIRS`` tree around an app's ``pages/``, guard the page too, nearest first.
A ``loading.djx`` stands in for everything below its directory, the inner layouts and the page body, so the layouts above it render in the shell and the loading UI sits where their content goes.

.. code-block:: text

   shop/pages/orders/
       layout.djx        the orders chrome, in the shell
       loading.djx       shown in its slot while the job runs
       error.djx         shown there when the job fails
       [order_id]/
           page.py
           template.djx

The job of the boundary runs the ``@context`` callables of the directories below it and of the page, then renders what the loading UI stood in for.
The callables of the boundary's directory and above run in the shell, and the ``loading.djx`` renders over their context.

A callable that can answer 404, 403, or 400 runs in the shell, with every callable before it, so the response keeps the status a plain render gives it.
A static scan finds one that calls ``get_object_or_404`` or ``get_list_or_404``, or raises ``Http404``, ``PermissionDenied``, ``BadRequest``, or ``SuspiciousOperation``, following calls three levels deep.
The scan reads the parameters too, so a callable that takes a ``DObject[Model]`` row, which answers 404 when the row is missing or hidden, runs in the shell, and so does one whose ``Depends`` factory or ``Context`` source answers one of those statuses.
A row the URL addresses therefore answers 404 before the first byte, never as an error UI after the shell.
A named dependency, ``Depends("name")``, is bound at request time, so the scan does not read it, and ``guard=True`` covers a callable that relies on one.
``@context(..., guard=True)`` runs a callable before the shell whatever the scan finds, and ``guard=False`` keeps it in the job.
``manage.py check`` names a guard the scan cannot follow as ``next.W169``, and a layout, a loading UI, or the page metadata that reads a key only the job provides as ``next.W152``.

``page.defer()`` at the top of the ``page.py`` tunes the job of the page boundary.

.. code-block:: python
   :caption: shop/pages/orders/[order_id]/page.py

   from next import page

   page.defer(timeout="3s", parallel=True)

``timeout`` is a number of seconds or a ``"3s"`` or ``"500ms"`` literal, at most one day, after which the boundary settles as ``timeout``.
``parallel=True`` pins the job to the pool, ``False`` keeps it on the request thread, and ``None`` leaves it to placement, see :doc:`async`.
A call inside a function, outside a ``page.py``, or with a value outside its shape declares nothing and is reported as ``next.E222``.

Component boundaries
--------------------

A ``loading.djx`` beside a component makes each call of it a ``stream`` boundary.
``component.defer()`` at the top of the ``component.py`` names another mode and the options of the boundary.

.. code-block:: python
   :caption: shop/components/recommendations/component.py

   from next import component

   component.defer("visible", props=("product_id",), timeout="2s")

.. code-block:: jinja
   :caption: shop/components/recommendations/loading.djx

   <p class="placeholder">Finding related products...</p>

A call site overrides the mode and the timeout with literal options, so one call of a streamed component may load on sight.

.. code-block:: jinja

   {% component "recommendations" product_id=product.pk defer="visible" timeout="2s" %}

The mode comes from the call site first, then from ``component.defer()``, then from the presence of a ``loading.djx``, which means ``stream``.
``defer=`` and ``timeout=`` take literals only, and ``reload=`` and ``delay=`` are options of ``{% zone %}`` only, so a component call that writes either raises ``TemplateSyntaxError`` at parse time and a component never receives a prop under any of the four names.
A ``@component.context`` callable that takes a parameter of one of those names is ``next.W158``.

``component.defer(mode, *, timeout, props, tag, parallel, tokens)`` declares the boundary.
``props`` names the props a reload or a retry renders the component from, and ``component.props(*names)`` declares the same list on its own.
``tag`` names the wrapper element, ``parallel`` pins the job the way ``page.defer`` does, and ``tokens`` overrides ``max_age``, ``bucket``, ``binding``, or ``max_bytes`` of the token settings for this component.
A deferred component without a ``loading.djx`` renders the template ``DEFER["LOADING"]`` names, or nothing, which ``next.W157`` reports.

A ``loading.djx`` renders over the context of the call site, with ``props`` holding the props of the call.
A component whose access rule refuses the visitor renders nothing at all, its loading UI included, and a rule with ``obj=`` is checked in the job, where the object is known.

The modes
---------

.. list-table::
   :header-rows: 1
   :widths: 14 86

   * - Mode
     - Delivery
   * - ``stream``
     - The shell carries the loading UI and the job runs during the same response.
       A streamed response writes the settled HTML as a chunk, and a response assembled inline splices it in before the first byte, see :doc:`streaming`.
   * - ``load``
     - The shell carries the loading UI and a token, and the browser fetches the component once the document is ready.
   * - ``visible``
     - The same, fetched once the wrapper scrolls into view.
   * - ``manual``
     - The same, fetched when a ``data-next-fetch`` control inside the loading UI is clicked.
   * - ``inline``
     - The component renders in place on the first paint, inside the wrapper and the error boundary of a deferred one, so a zone reload can show its loading UI.

A call inside a tag that stores rendered output, such as ``{% cache %}``, and a call inside a loading UI render in place, since a stored loading UI would outlive its job.
A deferred call inside a job queues a nested job, up to ``DEFER["MAX_DEPTH"]`` levels, and a deeper one renders in place.
The first ``MAX_PER_SITE`` calls of one call site run as jobs of their own while the response holds fewer than ``MAX_BOUNDARIES``, later ones run in groups, and every call past ``MAX_INSTANCES`` settles as ``timeout`` without running.

The wrapper element
-------------------

Each boundary renders inside a wrapper that carries its id, ``data-next-boundary``, and its state in ``data-next-state``.
The wrapper is a ``div`` unless ``component.defer(tag=...)`` names another element.
Inside a table, a list, a ``<select>``, or an ``<svg>``, where no ``div`` may stand, the boundary adopts the root element of its loading UI and of its content instead, and ``tag=None`` asks for that everywhere.
An adopted body must render exactly one root element.
A component body with several roots or loose text settles as an error, a loading or error UI with them leaves an empty ``<template>`` in its place, and each logs a warning.
A ``tag`` naming a void or raw-text element, such as ``img`` or ``script``, declares nothing and is reported as ``next.E222``.

Client-trip boundaries
----------------------

A ``load``, ``visible``, or ``manual`` component renders on its own request, from its declared props alone.
The call-site context, the children, and the slots never reach it, so the tag raises ``TemplateSyntaxError`` on an undeclared prop, a child, or a slot, and ``manage.py check`` reports the call as ``next.E224``.
Its ``@component.context`` callables and the context processors still run, and a template variable that is neither a prop nor one of their keys is ``next.W156``.

The wrapper carries a token that names the component, its props, the URL, and the permissions the call site rendered it under.
The default seal signs the token without hiding it, so the browser can read the props, and ``EncryptedSeal`` hides them, see ``DEFER["TOKENS"]`` in :doc:`/content/ref/settings`.
A token lives ``MAX_AGE`` seconds, ten minutes by default, and the server refuses an expired one with a 409 and ``X-Next-Reload: token-expired``, which reloads the page once.

The fetch is a GET of the page URL that carries the token in the ``X-Next-Boundary`` header by default.
The page refusal runs before the token is read, and the page guards, the access rule of the component, and the literal permissions of the ``{% has_perm %}`` tags around the call run before the component renders.
A guard counts wherever it opens, in a parent template the template of the call extends, in a template that includes it, or in the template of a component around the call.
A client-trip call under a ``{% has_perm %}`` whose permission is a variable or that names an object raises ``TemplateSyntaxError`` when it renders, since the token cannot carry that guard for the reload to check.
A refusal answers 403, after which a ``load`` or ``visible`` boundary reloads the page once and a ``manual`` one, or one refused again, settles as ``forbidden``.
An asset version the page no longer serves answers 409, which reloads the page.
A component whose access rule takes ``obj=`` must name a declared prop there, ``next.E225``, since the props are all the reload has to check it on.
A call shown under a template condition that the component does not repeat as an access rule of its own is ``next.W159``.

Prop types
~~~~~~~~~~

A reload renders the component with the same values, of the same types, that an inline render of the call receives.
A prop whose name a ``@component.context`` parameter takes travels as the annotation of that parameter, and any other prop travels as the JSON type of its own value.

.. list-table::
   :header-rows: 1
   :widths: 30 20 50

   * - Value
     - Unannotated
     - Annotated
   * - ``str``, ``int``, ``float``, ``bool``, ``None``
     - Round-trips with its type
     - Round-trips under the annotation of the same type, and ``float`` also takes an ``int``, which arrives as a ``float``
   * - ``list`` of those
     - Round-trips with each element's type
     - Round-trips under ``list[T]``, a ``tuple`` arriving as a ``list``
   * - ``Decimal``, ``date``, ``datetime``, ``UUID``
     - Refused
     - Round-trips through its canonical text
   * - A saved model instance
     - Refused
     - Travels as its primary key under the model class and is fetched again through its default manager, ``404`` once the row is gone
   * - ``dict``, ``tuple``, a nested list, an enum, any other object
     - Refused
     - Refused

``list[T]`` takes any scalar type of the table as ``T``, ``Decimal``, ``date``, ``datetime``, and ``UUID`` included, and never a model or an optional element.
``T | None`` accepts ``None`` as well, an ``int`` must fit in 64 bits, a ``float`` must be finite, and text is capped by ``MAX_BYTES``.
A string marked safe travels as plain text, so the deferred render autoescapes it.
An unannotated value that needs its annotation to come back typed, such as a ``date``, is refused rather than sent as its text.
Every refusal raises ``BoundaryPropsError`` when the token is minted, naming the prop and the type of its value.
``manage.py check`` reports an annotation no token carries, and a constant a call site passes that its annotation refuses, as ``next.E243``.

Retrying a boundary
~~~~~~~~~~~~~~~~~~~

A ``stream`` or ``inline`` component retries its own boundary when its ``error.djx`` offers a retry and it declares its props, otherwise the retry reloads the zone or the page around it.
``component.props()`` with no names declares an empty list, so a component that renders from its context functions alone retries on its own as well.
A component that reads ambient context from the call site, such as a loop variable, declares nothing, since a reload of it would render without that context.
A prop that cannot mint drops the retry and logs a warning on the ``next.suspense`` logger.

Binding the token
~~~~~~~~~~~~~~~~~

A token is bound to nobody by default, ``NoBinding``, so a shell that a CDN shares stays shareable and the same token serves every visitor of that shell.
The page refusal, the page guards, the access rule of the component, and the recorded ``{% has_perm %}`` permissions run again on every reload, so a client that obtains a token sees only what its own account may see.
Within ``MAX_AGE`` the token still opens the boundary for any client that holds it, so the props and the URL it carries reach whoever replays it.
``"user"`` binds the token to the primary key and the session auth hash of a signed-in user, so a logout or a password change revokes it, and ``"session"`` binds it to the session key.
A bound token makes the response ``Cache-Control: private`` and reads the user or the session on every mint and every reload.
An application with accounts sets ``DEFER["TOKENS"]["OPTIONS"]["BINDING"]`` to ``"user"``, or binds the components that show account data through ``component.defer(tokens={"binding": "user"})``, and ``manage.py check --deploy`` reports a project with ``django.contrib.auth`` that mints unbound tokens as ``next.W186``.

Without JavaScript
------------------

A reader without scripts never sends the fetch, so each idle wrapper carries a ``<noscript>`` link to the same page with ``_nojs=1`` added to its query string.

.. code-block:: html
   :caption: an idle wrapper on ``/shop/?q=lamp``

   <div id="b-…" data-next-boundary="b-…" data-next-boundary-key="…" data-next-defer="visible"
     data-next-token="…" data-next-state="idle"><i>Loading…</i><noscript><a href="?q=lamp&amp;_nojs=1"
     rel="nofollow" data-next-noscript>Show this content</a></noscript></div>

The page answers that request with every client-trip call of the render in place, as an ``inline`` call renders, under the same page guards and access rules.
The render is marked personal, so a page whose cache is shared sends it with ``Cache-Control: private``, since a client-trip component often holds the part of a shared shell that follows the visitor.
A wrapper that adopts the root of its loading UI is followed by the link instead of holding it, and a partial response carries no link, since only a script sends one.
The label goes through ``gettext``, and the ``data-next-noscript`` attribute is there for project CSS.

The link never carries the token.
The reload accepts a token only together with the ``X-Next-Request`` header, which a plain navigation cannot send and a cross-site fetch cannot send without a CORS preflight, so a token in a link would either be refused or open the reload path to any site.
A ``stream`` boundary of the same page still arrives as a chunk only a script applies, and :doc:`streaming` covers how ``DEFER["NOSCRIPT"]`` hides its loading UI.

.. seealso::

   :doc:`/content/topics/partial-rendering/zones` for ``loading.<zone>.djx``, the skeleton a ``reload="loading"`` zone shows while it reloads.
   :doc:`/content/ref/template-tags` for the options of the component tag.
   :doc:`/content/howto/show-a-loading-state` for the task from the first file to the test.
   :repo:`audit-forms <tree/main/examples/audit-forms>` for a component that loads when it scrolls into view with a prop, :repo:`observability <tree/main/examples/observability>` for a ``load`` component, a ``manual`` one behind a button, and a page boundary, and :repo:`search-catalog <tree/main/examples/search-catalog>` for streamed components with a timeout.
