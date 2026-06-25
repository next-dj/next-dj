.. _intro-tutorial06:

Live Updates with Partial Rendering
===================================

Goal
----

This part makes the Notes index update in place.
The list of notes becomes a zone, a search box filters it as the visitor types with no handler at all, and creating a note refreshes the list without a full reload.
Every behaviour falls back to the page cycle from :doc:`tutorial04` when JavaScript is off, so the same code serves both paths.

Prerequisites
-------------

You have finished :doc:`tutorial05`.
The Notes application creates, edits, and deletes notes through registered actions.

The layout from :doc:`tutorial03` already pulls ``{% collect_scripts %}`` into ``<head>``, and the static pipeline injects the client runtime through that tag.
There is nothing new to install.

Partial rendering layers on top of the ``POST`` then ``303`` then ``GET`` flow the framework already serves, covered in depth in :doc:`/content/topics/partial-rendering/index`.

Walkthrough
-----------

Wrap the Note List in a Zone
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``{% zone %}`` block marks a slice of a template the server can re-render on its own.
A zone is an optimisation rather than required markup.
The page renders identically without one, and naming the region lets a response carry only that slice instead of the whole document.

Wrap the list in ``notes/pages/template.djx`` and give each row a stable key.

.. code-block:: jinja
   :caption: notes/pages/template.djx

   {% zone "note-list" tag="ul" %}
     {% for note in notes %}
       <li data-next-key="{{ note.id }}">{% component "note_card" %}</li>
     {% endfor %}
   {% endzone %}

The ``tag="ul"`` argument makes the zone the ``<ul>`` element itself.
A wrapper ``<div>`` inside a ``<ul>`` would be dropped by the HTML parser, so a list zone names the list element directly.
Each ``data-next-key`` keeps a row stable when the list re-renders, so the morph reuses the node a row already owns instead of rebuilding it.

Reload ``/`` and confirm the list looks unchanged.
The zone is invisible until something targets it.

Filter the List as You Type
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Add a search box above the list.
It is a plain ``GET`` form that names the zone it updates.

.. code-block:: jinja
   :caption: notes/pages/template.djx

   <form method="get" action="{% url 'next:page_' %}"
         data-next-target="note-list"
         data-next-trigger="input" data-next-debounce="300">
     <input type="search" name="q" value="{{ query }}" placeholder="Filter notes">
   </form>

``data-next-target`` names the zone to update.
``data-next-trigger="input"`` auto-submits the form as the visitor types.
``data-next-debounce="300"`` waits 300 ms after the last keystroke before sending one request.

The page reads the query and filters the list.
Update ``notes/pages/page.py`` so the ``notes`` context honours ``q``, and publish the current query for the input value.

.. code-block:: python
   :caption: notes/pages/page.py

   from django.http import HttpRequest
   from notes.models import Note
   from next.pages import context

   @context("site_name", inherit_context=True)
   def site_name() -> str:
       return "Notes"

   @context("tagline", inherit_context=True)
   def tagline() -> str:
       return "A small tutorial application."

   @context("note_count", inherit_context=True)
   def note_count() -> int:
       return Note.objects.count()

   @context("query")
   def query(request: HttpRequest) -> str:
       return request.GET.get("q", "").strip()

   @context("notes")
   def recent_notes(request: HttpRequest) -> list[Note]:
       q = request.GET.get("q", "").strip()
       qs = Note.objects.all()
       if q:
           qs = qs.filter(title__icontains=q)
       return list(qs)

Both callables read ``request.GET`` the same way, so the zone fetch and the full page agree on the filter.
The ``query`` context only feeds the input value, and the ``notes`` context drives the list.
See :doc:`/content/topics/context` for how a page publishes named values.

There is no handler and no JavaScript.
Typing ``gro`` debounces, then sends one zone ``GET``.

.. code-block:: http
   :caption: request

   GET /?q=gro HTTP/1.1
   X-Next-Request: 1
   X-Next-Zone: note-list

The server re-renders only the ``note-list`` zone and answers with a single morph operation.

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "morph", "target": {"zone": "note-list"},
        "html": "<ul data-next-zone=\"note-list\">…matching notes…</ul>"}
     ]
   }

The runtime syncs the address bar with ``history.replaceState``, so ``/?q=gro`` stays shareable.
A new keystroke aborts an in-flight request, and a stale response that arrives after a fresher one is discarded.

Without the runtime the same form is a plain ``GET`` that reloads the whole page with the filtered list.
The provider reads ``request.GET`` either way, so the zone fetch reuses the exact query parsing the full page uses.

Create a Note in Place
~~~~~~~~~~~~~~~~~~~~~~~

The create form from :doc:`tutorial04` already posts through a registered action.
Teach its handler to answer a partial request with a patch instead of a redirect.

.. code-block:: python
   :caption: notes/forms.py, the create form

   from django.http import HttpRequest, HttpResponse
   from notes.models import Note
   from next.forms import ModelForm
   from next.partial import Patches, is_partial_request

   class CreateNoteForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

       def on_valid(self, request: HttpRequest) -> HttpResponse:
           if is_partial_request(request):
               self.save()
               return Patches(request).morph(zone="note-list").response()
           return super().on_valid(request)

``is_partial_request`` is ``True`` only when the runtime made the submission.
On that path the handler saves the note and returns a morph of the ``note-list`` zone, which re-renders the list from the ``notes`` context with the new note included.
On the no-JavaScript path ``super().on_valid`` keeps the inherited behaviour: it saves and redirects to origin, and the reload shows the new note.

The form tag itself does not change.

.. code-block:: jinja
   :caption: notes/pages/template.djx

   {% form "create_note_form" %}
     <label>Title {{ form.title }}</label>
     <label>Body {{ form.body }}</label>
     <button type="submit">Create</button>
   {% endform %}

The form carries no ``zone=`` argument, so a validation failure still re-renders the form in place with its errors, the default re-render from :doc:`tutorial04`.
The handler drives the list update explicitly, only on success and only for a partial request.
The morph keeps the caret in the title field, so a visitor can add several notes in a row without the page jumping.

Submit a note with a title and watch it appear at the top of the list with no reload.
Submit with an empty title and the form re-renders its error in place, the list untouched.

How It Degrades
~~~~~~~~~~~~~~~

Turn JavaScript off and exercise the same page.
The search box submits a full ``GET`` that reloads the filtered list.
The create form posts and redirects to origin, and the reloaded page lists the new note.
The handler reaches its ``super().on_valid`` branch because ``is_partial_request`` is ``False`` without the runtime.

The page that works without the runtime keeps working with it, and gains the partial behaviour for free.

Checkpoint
----------

The Notes index is live.
Filtering re-renders one zone as the visitor types, creating a note refreshes the list in place, and both paths still work with JavaScript off.

.. code-block:: text
   :caption: files touched in this part

   notes/
     forms.py            # CreateNoteForm.on_valid branches on is_partial_request
     pages/
       page.py           # query context, notes context filters by q
       template.djx      # search form, {% zone "note-list" %}

No new models, no new URLs, and no client code.
The behaviour rides the action dispatch and the file router the application already had.

Common Pitfalls
---------------

The filter reloads the whole page instead of swapping the list.
   Check that ``data-next-target`` names the same string as the ``{% zone %}`` tag, and that ``{% collect_scripts %}`` sits in the layout ``<head>`` so the runtime loads.

Creating a note redirects instead of updating in place.
   The handler only returns a patch inside the ``is_partial_request`` branch.
   A submission from a page without the runtime takes the ``super().on_valid`` redirect by design.

The new note appears twice for a moment.
   Confirm each ``<li>`` carries ``data-next-key="{{ note.id }}"``.
   The key lets the morph match a row to the node it already owns instead of duplicating it.

See :doc:`/content/faq/troubleshooting` for the full catalog of errors and fixes.

Next Steps
----------

The Notes application is complete and live.

.. seealso::

   :doc:`whatsnext` lists where to go next, by topic.
   :doc:`/content/topics/partial-rendering/index` covers zones, patches, modals, lazy zones, and the SSE bridge.
   :doc:`/content/topics/partial-rendering/scenarios` walks six more partial-rendering tasks from markup to handler.
