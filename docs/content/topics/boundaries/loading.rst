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

Before the first byte only a callable declared ``@context(..., guard=True)`` runs, and one with a parameter whose provider answers a status by contract, such as a ``DObject`` row that answers 404, and everything else under the ``loading.djx`` is deferred.
Such a guard runs in the shell with every callable before it, so its 404, 403, or 400 keeps the status a plain render gives it.
A deferred callable that raises ``Http404``, ``PermissionDenied``, or ``BadRequest`` renders the error UI of the boundary with the matching kind under the ``200`` the stream already sent, adds ``noindex`` to the page for a 404, and logs a ``WARNING`` that names ``guard=True``, see :doc:`errors`.
A ``SuspiciousOperation`` renders the same error UI and is logged as an ``ERROR`` on the ``django.security`` logger of its class, where Django logs one.

The status is honest whenever the page does not stream, since an inline assembly runs the jobs before the response, which is what a bot, a ``POST``, and a buffering server get.
A boundary reload runs the guards before its component the way the shell runs them, so a status check of the page in a callable without ``guard=True`` does not hold on a reload of a client-trip boundary, which ``next.W169`` reports for such a page too.
``manage.py check`` reports a deferred callable that a static scan sees answer a status as ``next.W169``, and a layout, a loading UI, or the page metadata that reads a key only the job provides as ``next.W152``.

.. code-block:: python
   :caption: shop/pages/orders/[order_id]/page.py

   from shop.models import Order

   from next.deps import DObject
   from next.pages import context

   @context("order")
   def order(order_id: DObject[Order]) -> Order:
       return order_id


   @context("history")
   def history(order: Order) -> list[str]:
       return order.slow_history()


   @context("invoice", guard=True)
   def invoice(order: Order) -> str:
       return order.invoice_or_404()

``page.defer()`` at the top of the ``page.py`` tunes the job of the page boundary.

.. code-block:: python
   :caption: shop/pages/orders/[order_id]/page.py

   from next import page

   page.defer(timeout="3s", parallel=False)

``timeout`` is a number of seconds or a ``"3s"`` or ``"500ms"`` literal, at most one day, after which the boundary settles as ``timeout``.
``parallel=False`` keeps the job on the request thread, and the default ``True`` lets it take the pool when no request-lane rule holds, see :doc:`async`.
A value outside its shape raises :exc:`~django.core.exceptions.ImproperlyConfigured` and a call inside a function raises ``DeclarationOutsideModuleError`` when the module runs, and a call outside a ``page.py`` declares nothing and is reported as ``next.E222``.
A page that calls ``page.defer()`` while no ``loading.djx`` defers it is ``next.W190``, since the call tunes nothing there.

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
It is the one list of what the token carries to the browser, so nothing reaches the client by a parameter name alone, and ``manage.py check`` holds the call sites and the ``@component.context`` callables to it.
``tag`` names the wrapper element, ``parallel=False`` keeps the job on the request thread the way ``page.defer`` does, and ``tokens`` overrides ``max_age``, ``binding``, or ``max_bytes`` of the token settings for this component.
A deferred component without a ``loading.djx`` renders the template ``BOUNDARIES["LOADING_TEMPLATE"]`` names, or nothing, which ``next.W157`` reports.

A ``loading.djx`` renders over the context of the call site, and the props of the call sit at its top level, so it reads a prop by the same name as ``component.djx``, ``{{ title }}`` for ``title="..."``.
A component that declares its props shows the declared ones alone.
A component whose access rule refuses the visitor renders nothing at all, its loading UI included, and a rule with ``obj=`` is checked in the job, where the object is known, so until then its loading UI reads the call-site context without the props.

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
A ``tag`` naming a void or raw-text element, such as ``img`` or ``script``, raises :exc:`~django.core.exceptions.ImproperlyConfigured` when the ``component.py`` runs.

Client-trip boundaries
----------------------

A ``load``, ``visible``, or ``manual`` component renders on its own request, from its declared props alone.
The call-site context, the children, and the slots never reach it, so the tag raises ``TemplateSyntaxError`` on an undeclared prop, a child, or a slot, and ``manage.py check`` reports the call as ``next.E224``.
Its ``@component.context`` callables and the context processors still run, and a template variable that is neither a prop nor one of their keys is ``next.W156``, as is a variable of its ``error.djx`` outside the props and ``error``, and a parameter of a callable without a default that only a context key of its name fills.

The wrapper carries a token that names the component, its props, the URL, and the permissions the call site rendered it under.
The default seal signs the token without hiding it, so the browser can read the props, and ``EncryptedSeal`` hides them, see ``DEFER["TOKENS"]`` in :doc:`/content/ref/settings`.
A token lives ``MAX_AGE`` seconds, ten minutes by default, and the server refuses an expired one with a 409 and ``X-Next-Reload: token_expired``, which reloads the page once.

The fetch is a GET of the page URL that carries the token in the ``X-Next-Boundary`` header by default.
The page refusal runs before the token is read, and the page guards, the access rule of the component, and the literal permissions of the ``{% has_perm %}`` tags around the call run before the component renders.
A guard counts wherever it opens, in a parent template the template of the call extends, in a template that includes it, or in the template of a component around the call.
A client-trip call under a ``{% has_perm %}`` whose permission is a variable or that names an object raises ``TemplateSyntaxError`` when it renders, since the token cannot carry that guard for the reload to check.

A refusal answers 403, after which a ``load`` or ``visible`` boundary reloads the page once and a ``manual`` one, or one refused again, settles as ``forbidden``.
An asset version the page no longer serves answers 409, which reloads the page.
A component whose access rule takes ``obj=`` must name a declared prop there, ``next.E225``, since the props are all the reload has to check it on.
A call shown under a template condition that the component does not repeat as an access rule of its own is ``next.W159``.

A reload never renders the page template, so a condition around the call, such as ``{% if user.is_staff %}`` or a ``{% for %}`` over the visitor's own rows, holds only when the token is minted.
A visitor who loses that access keeps loading the component with the token already in the page until it expires after ``MAX_AGE``.
The access a client-trip boundary needs is therefore written where a reload checks it, as an access rule of the component or as a page callable declared ``@context(..., guard=True)``, and each of them refuses the reload of a visitor who no longer passes it.

What reaches the client
~~~~~~~~~~~~~~~~~~~~~~~

The props in the token are a snapshot of the call, taken when the page rendered, and anyone who holds the token holds them too.
Three rules keep that safe.

- No secret goes into a prop under the default ``SignedSeal``, which signs the props but leaves them readable in the page, while ``EncryptedSeal`` or ``CacheStorage`` keeps them out of sight.
- A prop that names an object of an anonymous session, such as a cart or a draft, needs ``tokens={"binding": "session"}``, since under ``"auto"`` every anonymous visitor proves the same subject and one such token opens the boundary for whoever holds it.
- The right to see an object is an access rule of the component with ``obj=`` naming the prop, which a reload checks on the row fetched again, and never the fact that the page passed the prop.

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
``T | None`` accepts ``None`` as well, while an ``int`` outside 64 bits, a ``float`` that is not finite, and text over ``MAX_BYTES`` are not refused but delivered another way, as described below.
An optional prop is written ``T | None`` and a list ``list[T]``, while ``typing.List[T]`` is refused, and so are ``Optional[T]`` and ``Union[T, None]`` on Python 3.12 and 3.13, where they are not the ``T | None`` object they are from Python 3.14.

A string marked safe travels as plain text, so the deferred render autoescapes it.
An unannotated value that needs its annotation to come back typed, such as a ``date``, is refused rather than sent as its text.
Every refusal raises ``BoundaryPropsError`` when the token is minted, naming the prop and the type of its value.

Props of one request that outgrow ``MAX_BYTES``, such as a long search query, or an ``int`` outside 64 bits, such as ``int(request.GET["page"])`` for a forty-digit number, raise ``BoundarySizeError`` instead, and the page renders that boundary as a ``stream`` boundary in the same response and logs a warning to ``next.defer``.
A ``float`` or ``Decimal`` that is NaN or infinite, such as ``float(request.GET["ratio"])`` for ``?ratio=inf``, raises ``BoundaryValueError`` and is delivered the same way, with a warning that names the value.
Under ``DEBUG`` the ``X-Next-Boundaries`` header lists such a boundary as ``stream``.
``manage.py check`` reports an annotation no token carries, and a constant a call site passes that its annotation refuses, as ``next.E243``.

Retrying a boundary
~~~~~~~~~~~~~~~~~~~

A ``stream`` or ``inline`` component retries its own boundary when its ``error.djx`` offers a retry and it declares its props, otherwise the retry reloads the zone or the page around it.
``component.props()`` with no names declares an empty list, so a component that renders from its context functions alone retries on its own as well.
A component that reads ambient context from the call site, such as a loop variable, declares nothing, since a reload of it would render without that context.
A prop that cannot mint drops the retry and logs a warning on the ``next.defer`` logger.

Binding the token
~~~~~~~~~~~~~~~~~

A token is bound to the visitor's user by default, ``"auto"``, wherever ``AuthenticationMiddleware`` sets ``request.user``, and to nobody on a request without one.
Every anonymous visitor proves one subject, so a shell minted for anonymous visitors stays shareable and one token serves all of them, while a token minted for a signed-in user opens the boundary for that user alone.
The page refusal, the page guards, the access rule of the component, and the recorded ``{% has_perm %}`` permissions run again on every reload, so a client that obtains a token sees only what its own account may see.
Within ``MAX_AGE`` the token still opens the boundary for any client that holds it, so the props and the URL it carries reach whoever replays it, see `What reaches the client`_.

``"auto"`` and ``"user"`` bind the token to the primary key and the session auth hash of a signed-in user, so a logout or a password change revokes it, and ``"session"`` binds it to the session key.
A token bound to one visitor makes the response ``Cache-Control: private``, and every binding but ``"none"`` reads the user or the session on every mint and every reload, which marks the session read, so the response varies on ``Cookie``.
``"none"`` binds nobody, so a token minted for one signed-in user opens the boundary for whoever holds it, and ``manage.py check`` reports it in a project running ``AuthenticationMiddleware`` as ``next.W186``.

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
The same request assembles every ``stream`` boundary of the page in place too, and :doc:`streaming` covers how ``DEFER["NOSCRIPT"]`` sends a reader without scripts there from a streamed page.

.. seealso::

   :doc:`/content/topics/partial-rendering/zones` for ``loading.<zone>.djx``, the skeleton a ``reload="loading"`` zone shows while it reloads.
   :doc:`/content/ref/template-tags` for the options of the component tag.
   :doc:`/content/howto/show-a-loading-state` for the task from the first file to the test.
   :repo:`audit-forms <tree/main/examples/audit-forms>` for a component that loads when it scrolls into view with a prop, :repo:`observability <tree/main/examples/observability>` for a ``load`` component, a ``manual`` one behind a button, and a page boundary, and :repo:`search-catalog <tree/main/examples/search-catalog>` for streamed components with a timeout.
