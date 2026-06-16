.. _topics-partial-rendering-scenarios:

Partial Rendering by Scenario
=============================

This page is a tutorial, not a reference.
Each scenario starts from a task, shows the markup and the handler that satisfy it, and ends with the wire traffic so the behaviour is observable.
Every scenario degrades to a full page cycle without the runtime.

.. contents::
   :local:
   :depth: 1

Three Neighbouring Forms
------------------------

A board settings page carries three independent forms: rename the board, add a column, archive the board.
A validation error in one form must not touch the DOM of the other two, so text typed but not yet submitted in a neighbour survives.

This works with no changes at all.
The ``{% form %}`` tag already emits ``data-next-action="<uid>"`` and the hidden origin field, so the dispatcher can rebuild the source page on a failure.

.. code-block:: jinja
   :caption: settings/template.djx

   {% form "rename_board_form" %}…{% endform %}
   {% form "create_column_form" %}…{% endform %}
   {% form "archive_board_form" %}…{% endform %}

The default shape of an invalid submission is an extract-morph of the failed form addressed by its uid.
The server re-renders the whole origin page and the client trims out the one form by ``data-next-action``.
The neighbouring forms are named by no operation, so the runtime never touches them.
This costs a full page render on the server, which the next page explains and how to avoid.

Wrapping a form in a zone trades the full render for a single-zone render.
The zone is an optional optimisation, not required markup.

.. code-block:: jinja
   :caption: settings/template.djx with a zone

   {% zone "rename-board" %}
     {% form "rename_board_form" zone="rename-board" %}…{% endform %}
   {% endzone %}

The ``zone="rename-board"`` argument on the tag compiles to ``data-next-target`` on the ``<form>``, so the runtime sends the zone name with the submission and the server re-renders only that zone with the bound form.

Submitting an empty title posts to the form endpoint with the partial switch set.

.. code-block:: http
   :caption: request

   POST /_next/form/3f9ac21d75e04b88/ HTTP/1.1
   X-Next-Request: 1
   Accept: application/vnd.next.patches+json, text/html;q=0.9

   title=&_next_form_origin=/board/7/settings/

The response is a 200 carrying the existing invalid-form headers and a patch envelope.

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "morph", "target": {"form": "3f9ac21d75e04b88"}, "extract": true,
        "html": "<!doctype html>…the whole settings page…"}
     ],
     "form": {"uid": "3f9ac21d75e04b88", "valid": false,
              "errors": {"title": ["This field is required."]}}
   }

Without the runtime the same submission returns the full page with errors, the current behaviour byte for byte.

Inline Validation on Blur
-------------------------

A wizard step carries an email field.
The user should see a format error the moment focus leaves the field, before submitting, and with no server code.

One Python parameter on the existing tag turns it on.

.. code-block:: jinja
   :caption: request/[step]/template.djx

   {% form "access_request_wizard" validate="blur" %}
     {% component "progress_bar" %}
     {% component "step_section" %}
     …
   {% endform %}

The tag renders ``data-next-validate="blur"``.
The page module does not change.
The validate branch lives inside the dispatcher right after the access guard and the origin resolve, so the handler never runs and the wizard storage is never touched.

A blur on the email field with an invalid value posts the validate request.

.. code-block:: http
   :caption: request

   POST /_next/form/7c1de982aa341f05/ HTTP/1.1
   X-Next-Request: 1
   X-Next-Validate: email

   full_name=Ada&email=ada%40&team=&_next_form_origin=/request/identity/

The response is always 200 to an authorised caller.

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "morph", "target": {"form": "7c1de982aa341f05"},
        "html": "<form data-next-action=\"7c1de982aa341f05\"…>…</form>"}
     ],
     "form": {"uid": "7c1de982aa341f05", "valid": false,
              "errors": {"email": ["Enter a valid email address."]}}
   }

Errors are filtered to the field the request named, so the empty ``team`` field does not earn a premature required error.
Cross-field non-field errors are always dropped, because a ``clean()`` belongs to the submit, not to a per-field blur.
A file field is excluded from a validate request so a multipart upload is not re-sent on every blur.
The morph keeps the caret and any value typed in a neighbouring field during the round trip.

The guard runs before validation, so an anonymous caller on a protected action gets a denial, not an envelope.
A uniqueness validator never becomes an enumeration oracle for an unauthorised request.

Without the runtime nothing happens on blur and validation runs on submit, the current behaviour.

An Auto-Submitting Filter
-------------------------

A product catalogue is filtered by a panel form: search, brands, price, sort.
Today an Apply button triggers a full reload.
The results should update as the user types, the URL should stay shareable, and the providers should not change.

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
     <p>{{ page_obj.total }} products</p>
   </header>

   {% zone "catalog-results" %}
     <ul>
       {% for product in page_obj.products %}
         <li data-next-key="{{ product.pk }}">{% component "product_card" %}</li>
       {% endfor %}
     </ul>
     {% component "pagination" %}
   {% endzone %}

The page module does not change.
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

Without the runtime nothing happens until the Apply button submits a plain GET that reloads the page.

Pagination and Infinite Scroll
------------------------------

The same catalogue appends its next page of results to the list when the user scrolls to the end, with no reload and no duplicate rows.
Without the runtime pagination stays honest links.

A zone declared with ``tag="ul"`` becomes the ``<ul>`` itself.
A wrapper ``<div>`` inside a ``<ul>`` would be dropped by the HTML parser, so a list zone names the list element directly.

.. code-block:: jinja
   :caption: catalog/template.djx with infinite scroll

   {% load catalog_qs %}
   {% zone "catalog-results" tag="ul" %}
     {% for product in page_obj.products %}
       <li data-next-key="{{ product.pk }}">{% component "product_card" %}</li>
     {% endfor %}
     {% if page_obj.has_next %}
       <li id="results-sentinel">
         <a href="?{% querystring page=page_obj.page|add:'1' %}"
            data-next-target="catalog-results" data-next-merge="append"
            data-next-trigger="revealed">
           Show more
         </a>
       </li>
     {% endif %}
   {% endzone %}

A table uses ``tag="tbody"`` for the same reason.

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

When the sentinel enters the viewport the runtime sends the merge intent.

.. code-block:: http
   :caption: request

   GET /catalog/?q=ban&page=2 HTTP/1.1
   X-Next-Request: 1
   X-Next-Zone: catalog-results
   X-Next-Merge: append

The server reads the merge intent and answers with an ``append`` patch instead of a morph.

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "append", "target": {"zone": "catalog-results"}, "dedupe": "key",
        "html": "<li data-next-key=\"41\">…</li>…<li id=\"results-sentinel\">…</li>"}
     ]
   }

Dedup by ``data-next-key`` and ``id`` replaces the old sentinel with the new one and guards against duplicate rows under a race.
The response carries ``Vary: X-Next-Request, X-Next-Zone, X-Next-Merge`` so a shared cache never hands an append envelope to a client that asked for a morph.
Changing the search query is the morph of the same zone from the previous scenario, which resets the accumulated list on its own.
Switching ``data-next-trigger="revealed"`` to ``"click"`` gives button pagination from the same code.

Without the runtime the sentinel is a plain link and the click navigates to ``?page=2`` through the existing pagination component.

A Live Stream
-------------

A poll page shows live results to every open tab.
The same patch envelope rides Server-Sent Events, the fan-out carries no foreign HTML, and the voter does not receive an echo of their own vote.

The stream page is a neighbour of the vote page and uses the page render escape hatch.

.. code-block:: python
   :caption: polls/[int:id]/stream/page.py

   from collections.abc import Iterator

   from django.http import HttpRequest
   from polls.broker import broker
   from polls.models import Poll
   from polls.providers import DPoll

   from next.partial import Patches, PatchEventStream


   def patch_source(request: HttpRequest, poll_id: int) -> Iterator[Patches]:
       """Yield one refresh envelope for every poll change."""
       for change in broker.changes(poll_id):
           yield Patches(request, echo_of=change.request_id).refresh(zone="poll-results")


   def render(request: HttpRequest, poll: DPoll[Poll]) -> PatchEventStream:
       """Open the patch event stream for one poll."""
       return PatchEventStream(request, patch_source(request, poll.pk))

The vote page wraps its results in a zone and connects the stream with a ``data-next-sse`` element.

.. code-block:: jinja
   :caption: polls/[int:id]/template.djx

   {% zone "poll-results" %}
     {% component "poll_chart" %}
   {% endzone %}
   <div data-next-sse="/polls/{{ poll.pk }}/stream/"></div>

The vote form lives inside the chart component and does not change.
The broker carries one extra field: each change event ships the request id of the mutation that produced it.
Threading that id is the application channel's job, the framework does not smuggle it.

A vote from the initiating tab posts with a request id.

.. code-block:: http
   :caption: request

   POST /_next/form/9e4ab17c52d03f66/ HTTP/1.1
   X-Next-Request: 1
   X-Next-Request-Id: 1c9f…-r1

The initiator gets a morph of its zone in the POST response.
Then every subscriber receives the same envelope over the stream.

.. code-block:: text
   :caption: stream event

   event: next-patches
   data: {"version":"9f3c2e1b","request_id":"1c9f…-r1","ops":[{"op":"refresh","zone":"poll-results"}]}

The initiator finds ``1c9f…-r1`` in its ring buffer of recent request ids and drops the echo, its own POST already brought the fresh zone.
Every other tab executes the ``refresh`` and re-fetches the zone with its own cookies.

.. code-block:: http
   :caption: each other tab

   GET /polls/7/ HTTP/1.1
   X-Next-Request: 1
   X-Next-Zone: poll-results

Authorization runs in the page view on every tab, so the stream never broadcasts one user's HTML to another.
The Vue island that owns the chart re-reads ``window.Next.context`` on the zone morph and keeps its state.
See :doc:`sse` for the WSGI and ASGI contract behind the stream.

Without the runtime the page is current at load time and a refresh is a manual reload.

A Modal Wizard That Refreshes a List
------------------------------------

A home page carries a Start a new request button and a list of recent requests.
Clicking the button opens a modal, the step content loads on open, and inside is an existing three-step wizard.
A step error keeps the modal open, steps advance with no reload, drafts live in wizard storage.
On the last step the request is created, the modal closes, and the list beneath it refreshes.
There is no user JavaScript.

The opening link names a layer and the zone to refresh on accept.
The list is a zone.

.. code-block:: jinja
   :caption: views/template.djx

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

The step template is the existing one, wrapped in a zone.

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

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "morph", "target": {"zone": "access-wizard"},
        "html": "<div data-next-zone=\"access-wizard\">…identity step…</div>"}
     ],
     "assets": [{"kind": "css", "url": "/static/access/progress_bar.css"}]
   }

A step submitted with an error answers 200 with a morph of the wizard zone and the form meta.
No operation names the layer, so the modal lives on.
The dialog carries the ``data-next-dialog`` attribute so project CSS can target it without relying on tag or internal structure.
A valid non-last step advances without a 302: the dispatcher builds the next wizard and the unbound form of the next step, and morphs the wizard zone to the next step.

Wizard steps inside a layer are not pushed to history by default.
The per-uid mutation lock makes a double-click on Submit request impossible by construction, it lets exactly one fetch through.
A session that expires mid-wizard returns a non-envelope response, and the runtime performs a full navigation to the login page.

The two choreographies differ only in the last step, and :doc:`done-choreographies` compares them.
The default ``done`` closes the layer with a result and asks the opening link to refresh its accepted zone.

.. code-block:: python
   :caption: request/[step]/page.py

   def done(self, request: HttpRequest, cleaned_data: dict[str, Any]) -> PatchResponse:
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

.. code-block:: http
   :caption: re-GET of the list

   GET / HTTP/1.1
   X-Next-Request: 1
   X-Next-Zone: request-list

The wizard never knows about the list, the list never knows about the wizard, and the opening link binds them.
Without the runtime the button navigates to the full step page, steps advance with a 302 between step pages, and the last step redirects to the ``fallback`` audit page.

Lazy Zones
----------

A heavy audit table does not need to render on the first page paint.
A lazy zone renders only its placeholder up front and fetches its body after load or when it enters the viewport, so the expensive query never runs for nothing.

.. code-block:: jinja
   :caption: admin/audit/template.djx

   {% zone "audit-table" lazy="revealed" %}
     <table>
       <thead>…</thead>
       <tbody>
         {% for entry in entries %}
           {% component "audit_row" %}
         {% endfor %}
       </tbody>
     </table>
   {% placeholder %}
     {% component "skeleton" rows=6 %}
   {% endzone %}

The body of a lazy zone does not run on the full render, only the placeholder branch does.
Guard the expensive data with ``zone_requested`` so the query runs only when the lazy body is actually rendered.

.. code-block:: python
   :caption: admin/audit/page.py

   from next.partial import zone_requested


   @context("entries")
   def entries(request: HttpRequest) -> list[AuditEntry] | None:
       """Load audit rows only when the lazy zone is rendered."""
       if not zone_requested(request, "audit-table"):
           return None
       return list(AuditEntry.objects.all()[:100])

The context collector is all-or-nothing, so the laziness of the data is manual but honest.
A forgotten guard means an extra query, which the :doc:`zones` page calls out.

A ``lazy="load"`` zone is batched into one GET on ``ready``.
A ``lazy="revealed"`` zone waits for the viewport.

.. code-block:: http
   :caption: request

   GET /admin/audit/ HTTP/1.1
   X-Next-Request: 1
   X-Next-Zone: audit-table

The response morphs the zone and carries the co-located assets of the components inside it.
CSS loads before the morph, JS after, so the table cannot arrive without behaviour.

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "morph", "target": {"zone": "audit-table"},
        "html": "<div data-next-zone=\"audit-table\">…the table…</div>"}
     ],
     "assets": [{"kind": "css", "url": "/static/access/audit_row.css"}]
   }

Critical content must not be marked lazy, because the placeholder is all a no-JavaScript client ever sees.

See Also
--------

.. seealso::

   :doc:`zones` for why zones are optional and what the extract default costs.
   :doc:`done-choreographies` for the two ways the modal wizard refreshes the list.
   :doc:`co-located-js` for keeping co-located JavaScript alive across a morph.
   :doc:`reference` for the verbs, headers, attributes, and settings.
