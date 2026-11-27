.. _topics-partial-rendering-scenarios:

Partial rendering by scenario
=============================

This page is a tutorial, not a reference.
Each scenario starts from a task, shows the markup and the handler that satisfy it, and ends with the wire traffic so the behaviour is observable.
Every scenario degrades to a full page cycle without the runtime.
The JSON examples on this page omit the empty ``assets`` and ``form`` keys, see :doc:`reference`.

.. contents::
   :local:
   :depth: 1

Three neighbouring forms
------------------------

A board settings page carries three independent forms, one to rename the board, one to add a column, one to archive the board.
A validation error in one form must not touch the DOM of the other two, so text typed but not yet submitted in a neighbour survives.

Three plain ``{% form %}`` tags are the whole markup.
The ``{% form %}`` tag already emits ``data-next-action="<uid>"`` and the hidden origin field, so the dispatcher can rebuild the source page on a failure.

.. code-block:: jinja
   :caption: settings/template.djx

   {% form "rename_board_form" %}…{% endform %}
   {% form "create_column_form" %}…{% endform %}
   {% form "archive_board_form" %}…{% endform %}

The default shape of an invalid submission is an extract-morph of the failed form addressed by its uid.
The server re-renders the whole origin page and the client trims out the one form by ``data-next-action``.
The neighbouring forms are named by no operation, so the runtime never touches them.
:doc:`zones` explains the full-page render this costs and the zone that avoids it.

A zone around the form is optional markup that renders that one zone instead.

.. code-block:: jinja
   :caption: settings/template.djx with a zone

   {% zone "rename-board" %}
     {% form "rename_board_form" zone="rename-board" %}…{% endform %}
   {% endzone %}

The ``zone="rename-board"`` argument on the tag compiles to ``data-next-target`` on the ``<form>``.
The runtime then sends the zone name with the submission and the server re-renders only that zone with the bound form.

Submitting an empty title in the unzoned form posts to the form endpoint with the partial switch set.

.. code-block:: http
   :caption: request without a zone

   POST /_next/form/3f9ac21d75e04b88/ HTTP/1.1
   X-Next-Request: 1
   Accept: application/vnd.next.patches+json, text/html;q=0.9

   title=&_next_form_origin=/board/7/settings/

The response is a 200 carrying the invalid-form headers and a patch envelope.

.. code-block:: json
   :caption: response body without a zone

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "morph", "target": {"form": "3f9ac21d75e04b88"}, "extract": true,
        "html": "<!doctype html>…the whole settings page…"}
     ],
     "form": {"uid": "3f9ac21d75e04b88", "valid": false,
              "errors": {"title": ["This field is required."]}}
   }

With the ``zone=`` form above the submission also carries ``X-Next-Zone: rename-board``, and the envelope morphs that zone instead of extracting the form out of a whole page, the shape :doc:`zones` shows.
Without the runtime the same submission returns the full page with its errors.

Inline validation on blur
-------------------------

A wizard step carries an email field.
The user should see a format error the moment focus leaves the field, before submitting, and with no server code.

One parameter on the ``{% form %}`` tag turns it on.

.. code-block:: jinja
   :caption: request/[step]/template.djx

   {% form "access_request_wizard" validate="blur" debounce="300" %}
     {% component "progress_bar" %}
     {% component "step_section" %}
     …
   {% endform %}

The tag renders ``data-next-validate="blur"`` and ``data-next-debounce="300"``, so a burst of blur probes collapses into one validate POST.
The page module needs nothing new.
The validate branch lives inside the dispatcher right after the access guard and the origin resolve, so the handler never runs and the wizard storage is never touched.

A blur on the email field with an invalid value posts the validate request.

.. code-block:: http
   :caption: request

   POST /_next/form/7c1de982aa341f05/ HTTP/1.1
   X-Next-Request: 1
   X-Next-Validate: email

   full_name=Ada&email=ada%40&team=&_next_form_origin=/request/identity/

The response is always 200 to an authorized caller.

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "morph", "target": {"form": "7c1de982aa341f05"}, "extract": true,
        "html": "<!doctype html>…the whole step page…"}
     ],
     "form": {"uid": "7c1de982aa341f05", "valid": false,
              "errors": {"email": ["Enter a valid email address."]}}
   }

The form here carries no ``zone=``, so the validate response is an extract-morph of the whole step page addressed by the form uid, the same default :doc:`zones` describes.
Errors are filtered to the field the request named, so the empty ``team`` field does not earn a premature required error.
Cross-field non-field errors are always dropped, because a ``clean()`` belongs to the submit, not to a per-field blur.
A file field is excluded from a validate request so a multipart upload is not re-sent on every blur.
The morph keeps the caret and any value typed in a neighbouring field during the round trip.

The guard runs before validation, so an anonymous caller on a protected action gets a denial, not an envelope.
A uniqueness validator never becomes an enumeration oracle for an unauthorized request.

Without the runtime nothing happens on blur and validation runs on submit.

An auto-submitting filter
-------------------------

A product catalogue is filtered by a panel form over search, brands, price, and sort.
A plain submit of that panel reloads the whole page.
The results should update as the user types, the URL should stay shareable, and the providers need nothing new.

The panel form gets three attributes, and the sort select gets one.

.. code-block:: jinja
   :caption: filter_panel/component.djx

   <form method="get" action="{{ submit_url }}"
         data-next-target="catalog-results"
         data-next-trigger="input" data-next-debounce="300">
     <input id="filter-q" name="q" type="search" value="{{ current_filters.q }}">
     …
     <select name="sort" data-next-trigger="change">…</select>
   </form>

The catalogue page wraps its results in a zone.

.. code-block:: jinja
   :caption: catalog/template.djx

   <header>
     <h1>All products</h1>
     <p>{{ page_obj.paginator.count }} products</p>
   </header>

   {% zone "catalog-results" %}
     <ul>
       {% for product in page_obj %}
         <li data-next-key="{{ product.pk }}">{% component "product_card" %}</li>
       {% endfor %}
     </ul>
     {% component "pagination" %}
   {% endzone %}

Here ``page_obj`` is a Django :class:`~django.core.paginator.Page`, published by the paginated context function of :ref:`topics-pages-pagination`.
The template reads the standard ``Page`` API, iterating the page for its rows and reaching through ``paginator`` for the total.
The page module needs nothing new.
The provider already reads ``request.GET``, so the zone GET reuses the same query parsing the full page uses.

Typing ``ban`` debounces for 300 ms, then sends one zone GET.

.. code-block:: http
   :caption: request

   GET /catalog/?q=ban HTTP/1.1
   X-Next-Request: 1
   X-Next-Zone: catalog-results

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "morph", "target": {"zone": "catalog-results"},
        "html": "<div data-next-zone=\"catalog-results\">…</div>"}
     ]
   }

The runtime syncs the address bar with ``history.replaceState`` so the URL stays shareable.
A new keystroke aborts the in-flight GET, an aborted request does not reach ``partial:error``, and a monotonic counter discards a stale response that arrives after a fresher one.
Each ``data-next-key`` on a row keeps the morph stable as the list changes.

Without the runtime the panel stays a plain GET form whose submit reloads the page.

Pagination and infinite scroll
------------------------------

The same catalogue appends its next page of results to the list when the user scrolls to the end, with no reload and no duplicate rows.
Without the runtime pagination stays honest links.

A pager cuts the product rows into the window the query string asks for.
A context callable names it in an ``Annotated[Paging, <pager>]`` parameter and returns the ``Window`` of ``page.paginate()``.

.. code-block:: python
   :caption: catalog/page.py

   from typing import Annotated

   from catalog.models import Product

   from next import context
   from next.partial import NumberPager, Paging, Window

   pager = NumberPager(per_page=20)

   @context("products")
   def products(page: Annotated[Paging, pager]) -> Window[Product]:
       return page.paginate(Product.objects.order_by("pk"))

A zone declared with ``tag="ul"`` becomes the ``<ul>`` itself.
A wrapper ``<div>`` inside a ``<ul>`` would be dropped by the HTML parser, so a list zone names the list element directly.
The template loops over the window, and the ``{% more %}`` tag after the zone takes the same window and renders the sentinel.

.. code-block:: jinja
   :caption: catalog/template.djx with infinite scroll

   {% zone "catalog-results" tag="ul" %}
     {% for product in products %}
       <li data-next-key="{{ product.pk }}">{% component "product_card" %}</li>
     {% endfor %}
   {% endzone %}

   {% more zone="catalog-results" next=products %}Show more{% empty %}End of results.{% endmore %}

The tag reads the token of the next batch off the window, here the number of the next page, and renders its ``{% empty %}`` branch when the window has none.
The tag declares a zone named ``catalog-results-more`` that holds a real link, so the zone is addressable like any ``{% zone %}`` of the template.

.. code-block:: html
   :caption: the rendered sentinel on ``/catalog/?q=ban``

   <div data-next-zone="catalog-results-more"><a href="?q=ban&amp;page=2"
     data-next-key="catalog-results-more"
     data-next-target="catalog-results,catalog-results-more"
     data-next-merge="append" data-next-defer="visible">Show more</a></div>

The link keeps the active query string and replaces only the page number.
It lives outside the results zone on purpose, since a merge adds incoming rows at the end of the zone and a marker that must stay last cannot ride inside the list.
On the last page the zone holds the ``{% empty %}`` branch in a ``<div>`` with the same ``data-next-key`` instead of the link.

A table uses ``tag="tbody"`` for the same reason a list uses ``tag="ul"``.

.. code-block:: jinja
   :caption: a table zone

   <table>
     <thead>…</thead>
     {% zone "audit-rows" tag="tbody" %}
       {% for entry in entries %}
         {% component "audit_row" %}
       {% endfor %}
     {% endzone %}
   </table>

When the sentinel enters the viewport the runtime sends the merge intent for both zones the link names.

.. code-block:: http
   :caption: request

   GET /catalog/?q=ban&page=2 HTTP/1.1
   X-Next-Request: 1
   X-Next-Zone: catalog-results,catalog-results-more
   X-Next-Merge: append

The server reads the merge intent and answers with one ``append`` patch per zone instead of a morph.

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "append", "target": {"zone": "catalog-results"}, "dedupe": "key",
        "html": "<li data-next-key=\"41\">…</li>…"},
       {"op": "append", "target": {"zone": "catalog-results-more"}, "dedupe": "key",
        "html": "<a href=\"?q=ban&amp;page=3\" data-next-key=\"catalog-results-more\" …>Show more</a>"}
     ]
   }

Dedup by ``data-next-key`` replaces the old link with the new one and guards against duplicate rows under a race.
The response carries ``Vary: X-Next-Request, X-Next-Zone, X-Next-Merge, X-Next-Version`` so a shared cache never hands an append envelope to a client that asked for a morph.
Changing the search query morphs both zones, which resets the accumulated list on its own.
The runtime tells the two apart by the link alone, as a request counts as a page of the list only when its URL is the ``href`` of the link and its zones are exactly the ``catalog-results,catalog-results-more`` of its ``data-next-target``.
The filter form may therefore list the same zones in any order beside others, and its request neither stamps rows nor prunes them nor writes ``_restore`` into the address.
``mode="manual"`` drops the ``data-next-defer="visible"`` attribute and leaves the same link click-driven, which gives button pagination from the same code.

``prune=`` and ``url=`` on the tag keep a long scroll bounded and its address shareable.

.. code-block:: jinja
   :caption: a pruned list that records its position

   {% more zone="catalog-results" next=products prune=40 url="replace" %}Show more{% empty %}End of results.{% endmore %}

After each batch the runtime writes ``?page=N&_restore=page`` into the address bar, and once the list holds more than 40 rows it drops whole batches from the top.
A shared address ``?page=8&_restore=page`` restores a scrolled list.
The window then runs from page ``max(1, 8 - max_restore + 1)`` to page 8, where ``max_restore`` is the argument of the pager, ten by default, so a large page number costs ten pages at most.
The pager reads the whole window in one slice that runs one row past it, which tells it whether a next batch follows without a count.
A header that shows the total builds the pager with ``count=True`` and reads ``products.count``.
The sentinel of a restored window carries the tokens of its batches as ``data-next-backs``, so the runtime knows which batch each restored row belongs to and the first prune drops the oldest batch alone.
A second tag above the list links the batch before the window.
On the first page it renders its ``{% empty %}`` branch with a hidden link beside it, which the runtime shows once a prune drops the first rows.

.. code-block:: jinja
   :caption: the link above the list

   {% more zone="catalog-results" previous=products %}Show earlier{% endmore %}

It declares the zone ``catalog-results-less`` and prepends the earlier page on a click, so the same link serves a restored window and a plain ``?page=3`` visit, with or without the runtime.
A list that gains rows while a visitor scrolls it pages by a cursor instead of a page number, see :doc:`/content/howto/page-a-list-by-cursor`.

Without the runtime the sentinel is a plain link and the click navigates to ``?q=ban&page=2``.

A live stream
-------------

A poll page shows live results to every open tab.
The topic carries refresh instructions rather than HTML, and the voter does not receive an echo of their own vote.

The guarded stream page authorizes each subscriber and returns a ``TopicStream``.

.. code-block:: python
   :caption: polls/pages/polls/[int:id]/stream/page.py

   from django.http import HttpRequest
   from django.shortcuts import get_object_or_404
   from polls.models import Poll
   from polls.realtime import can_subscribe_topic

   from next.realtime import TopicStream
   from next.urls import DUrl

   def render(request: HttpRequest, poll_id: DUrl["id", int]) -> TopicStream:
       poll = get_object_or_404(Poll, pk=poll_id)
       return TopicStream(request, f"poll:{poll.pk}", authorize=can_subscribe_topic)

``polls.realtime.can_subscribe_topic`` is the ``authorize`` callback that :doc:`/content/howto/stream-live-updates-with-sse` defines, together with the ``Poll`` model and the vote page.

The vote page wraps its results in a zone and connects the negotiated stream endpoint with a ``data-next-stream`` element.

.. code-block:: jinja
   :caption: polls/[int:id]/template.djx

   {% zone "poll-results" %}
     {% component "poll_chart" %}
   {% endzone %}
   <div data-next-stream="/polls/{{ poll.pk }}/stream/"></div>

The ``poll_chart`` component, its Vue island, and the vote signal receiver come from ``examples/live-polls``, see :doc:`/content/misc/examples`.
The vote signal publishes a ``refresh`` patch through ``next.realtime.publish`` after the transaction commits.
The framework adds the originating request id to the envelope, so the initiating tab drops its echo after its POST response already updated the zone.
Every other subscriber executes ``refresh`` and re-fetches the zone through its own guarded page request and cookies.
The stream never broadcasts one user's HTML to another.
The Vue island that owns the chart re-reads the server snapshot on the zone morph and keeps its state.
The transport can be SSE or the optional WebSocket transport, and both use the same topic envelope and authorization rules.
See :doc:`sse` and :doc:`../realtime` for transport and broker contracts.

Without the runtime the page is current at load time and a refresh is a manual reload.

A modal wizard that refreshes a list
------------------------------------

A home page carries a Start a new request button and a list of recent requests.
Clicking the button opens a modal, the step content loads on open, and inside is an existing three-step wizard.
A step error keeps the modal open, and steps advance with no reload while drafts live in wizard storage.
On the last step the request is created, the modal closes, and the list beneath it refreshes.
There is no user JavaScript.

The opening link names a layer and the zone to refresh on accept.
The list is a zone.

.. code-block:: jinja
   :caption: access/pages/template.djx

   <a href="{% url 'next:page_request_step' step='identity' %}"
      data-next-layer="access-wizard" data-next-accepted="request-list">
     Start a new request
   </a>

   {% zone "request-list" %}
     <ul>
       {% for r in recent_requests %}
         <li data-next-key="{{ r.pk }}">…</li>
       {% endfor %}
     </ul>
   {% endzone %}

The step template is the plain wizard step, wrapped in a zone.

.. code-block:: jinja
   :caption: request/[step]/template.djx

   {% zone "access-wizard" %}
     {% form "access_request_wizard" validate="blur" %}
       {% component "progress_bar" %}
       {% component "step_section" %}
       {% if wizard.is_last %}
         {% component "button" type="submit" text="Submit request" variant="default" %}
       {% else %}
         {% component "button" type="submit" text="Continue" variant="default" %}
       {% endif %}
     {% endform %}
   {% endzone %}

On click the runtime opens a native ``<dialog>``, builds the ``access-wizard`` zone container from the link's own ``data-next-layer`` value, and sends the first zone GET.

.. code-block:: http
   :caption: request

   GET /request/identity/ HTTP/1.1
   X-Next-Request: 1
   X-Next-Zone: access-wizard
   X-Next-Origin: /

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "morph", "target": {"zone": "access-wizard"},
        "html": "<div data-next-zone=\"access-wizard\">…identity step…</div>"}
     ],
     "assets": [{"kind": "css", "url": "/static/access/progress_bar.css", "load": "link"}]
   }

A step submitted with an error answers 200 with a morph of the wizard zone and the form meta.
No operation names the layer, so the modal lives on.
The dialog carries the ``data-next-dialog`` attribute so project CSS can target it without relying on tag or internal structure.
A valid non-last step advances without a 302.
The dispatcher builds the next wizard and the unbound form of the next step, and morphs the wizard zone to the next step.

A wizard advance pushes no history entry by default, in a layer or on a full page.
``PUSH_WIZARD_STEPS`` flips the global default and a wizard's ``Meta.push_steps`` overrides it per wizard, see :doc:`reference`.
The per-uid mutation lock lets exactly one fetch through, so a double-click on Submit request sends one request.
A session that expires mid-wizard returns a non-envelope response, and the runtime performs a full navigation to the login page.

The two choreographies differ only in the last step, and :doc:`done-choreographies` compares them.
The default ``done`` closes the layer with a result and asks the opening link to refresh its accepted zone.

.. code-block:: python
   :caption: request/[step]/page.py

   from typing import Any

   from access.models import AccessRequest
   from django.http import HttpRequest, HttpResponse

   from next.forms import FormWizard
   from next.partial import Patches

   class AccessRequestWizard(FormWizard):
       def done(
           self, request: HttpRequest, cleaned_data: dict[str, Any]
       ) -> HttpResponse:
           """Create the access request and close the layer with a result."""
           access_request = AccessRequest.objects.create(**cleaned_data)
           return (
               Patches(request)
               .layer_close(result={"id": access_request.pk})
               .toast("Request created", variant="success")
               .response(fallback=f"/request/{access_request.pk}/audit/")
           )

The last step answers with a close and a toast.

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "layer.close", "result": {"id": 42}},
       {"op": "toast", "text": "Request created", "variant": "success"}
     ]
   }

The runtime closes the layer, fires accept, and by ``data-next-accepted="request-list"`` sends a second GET for the list.
The attribute takes a comma-separated list of zones, so ``data-next-accepted="request-list,request-count"`` refreshes both in that one GET.

.. code-block:: http
   :caption: re-GET of the list

   GET / HTTP/1.1
   X-Next-Request: 1
   X-Next-Zone: request-list
   X-Next-Origin: /

The wizard never knows about the list, the list never knows about the wizard, and the opening link binds them.
Without the runtime the button navigates to the full step page, steps advance with a 302 between step pages, and the last step redirects to the ``fallback`` audit page.

A table that loads when it scrolls into view
--------------------------------------------

A heavy audit table does not need to render on the first page paint.
The table becomes a component that loads later, so the first paint ships its loading content and the expensive query runs only on the component's own request.
A kind filter above the table reloads it, so the call sits in a zone the filter addresses, and the zone itself always renders inline.

.. code-block:: jinja
   :caption: admin/audit/template.djx

   <form method="get" data-next-target="audit-table" data-next-trigger="change">…</form>
   {% zone "audit-table" %}
     {% component "audit_table" kind=active_kind %}
   {% endzone %}

The component declares how it is delivered and which props its later request renders from.
It reads its rows in its own context function, because a component that loads on a later request renders from its declared props alone.

.. code-block:: python
   :caption: _components/audit_table/component.py

   from audit.models import AuditEntry

   from next import component

   component.defer("visible", props=("kind",))


   @component.context("entries")
   def entries(kind: str) -> list[AuditEntry]:
       rows = AuditEntry.objects.all()
       return list((rows.filter(kind=kind) if kind else rows)[:100])

The ``loading.djx`` beside it holds what the first paint shows, and ``component.djx`` holds the table.

.. code-block:: jinja
   :caption: _components/audit_table/loading.djx

   {% component "skeleton" rows=6 %}

``visible`` waits until the wrapper enters the viewport, ``load`` fetches as soon as the runtime is ready, and ``manual`` waits for a ``data-next-fetch`` control inside the loading content.
The wrapper carries a signed token of the declared props, and the browser sends it back on the page URL.

.. code-block:: http
   :caption: request

   GET /admin/audit/ HTTP/1.1
   X-Next-Request: 1
   X-Next-Zone: b-3f9a2c
   X-Next-Boundary: …the token…

The response renders the component alone and swaps it into its wrapper with its co-located assets.
A change of the kind filter morphs the zone, which puts the loading content back with the new ``kind``, and the table fetches itself again.

The rows never run on the full render, so no ``zone_requested`` guard is needed.
Critical content does not belong in a component that loads later, because the loading content is all a no-JavaScript client ever sees.

See also
--------

.. seealso::

   :doc:`zones` for why zones are optional and what the extract default costs.
   :doc:`done-choreographies` for the two ways the modal wizard refreshes the list.
   :doc:`co-located-js` for keeping co-located JavaScript alive across a morph.
   :doc:`extending` for custom verbs and server-pushed context and events.
   :doc:`reference` for the verbs, headers, attributes, and settings.
