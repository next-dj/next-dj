.. _intro-tutorial02:

Tutorial Part 2 Layouts and Context
===================================

Goal
----

This part wraps every page in a shared layout, adds a detail page at ``/notes/<id>/``, and uses ``inherit_context`` to publish data once for the entire page tree.
By the end the index links to each note and the detail page renders a single note pulled from the URL.

Prerequisites
-------------

You have finished :doc:`tutorial01`.
The index page at ``/`` lists two seeded notes from the database.

Walkthrough
-----------

Add a Root Layout
~~~~~~~~~~~~~~~~~

A ``layout.djx`` placed in any directory wraps every page below it.
For the Notes application the most common layout sits next to the page tree at ``notes/routes/layout.djx``.

.. code-block:: jinja
   :caption: notes/routes/layout.djx

   <!doctype html>
   <html>
     <head>
       <title>{{ site_name }}</title>
     </head>
     <body>
       <header>
         <a href="{% url 'next:page_' %}"><h1>{{ site_name }}</h1></a>
         <p>{{ tagline }}</p>
       </header>
       <main>
         {% block template %}{% endblock template %}
       </main>
     </body>
   </html>

The ``{% block template %}`` placeholder is mandatory.
The framework substitutes the body of each page into that block.

Remove the now redundant HTML envelope from ``notes/routes/template.djx`` and keep only the page body.

.. code-block:: jinja
   :caption: notes/routes/template.djx

   <ul>
     {% for note in notes %}
       <li>
         <a href="{% url 'next:page_notes_id' id=note.id %}">{{ note.title }}</a>
         <small>{{ note.created_at|date:"Y-m-d H:i" }}</small>
       </li>
     {% endfor %}
   </ul>

Refresh ``http://127.0.0.1:8000/`` to confirm that the layout renders the title and the list now uses anchor tags.

Share Site Context
~~~~~~~~~~~~~~~~~~

The layout references ``site_name`` and ``tagline``, but no page produces them yet.
Add a ``layout.py`` next to ``layout.djx`` to publish those values.

.. code-block:: python
   :caption: notes/routes/layout.py

   from next.pages import context


   @context("site_name", inherit_context=True)
   def site_name() -> str:
       return "Notes"


   @context("tagline", inherit_context=True)
   def tagline() -> str:
       return "A small tutorial application."

``inherit_context=True`` makes both values visible to every descendant page, not only to the layout itself.
Without that flag the layout would still render but pages further down the tree would not see them.

Add the Detail Page
~~~~~~~~~~~~~~~~~~~

Create a new directory ``notes/routes/notes/[id]/``.
The bracketed segment is a URL parameter that the file router captures as ``id``.

.. code-block:: python
   :caption: notes/routes/notes/[id]/page.py

   from django.shortcuts import get_object_or_404
   from notes.models import Note

   from next.pages import context
   from next.urls.markers import DUrl


   @context("note")
   def fetch_note(note_id: DUrl[int]) -> Note:
       return get_object_or_404(Note, pk=note_id)

The ``DUrl[int]`` marker tells the :doc:`dependency injector </content/topics/dependency-injection>` to pull the captured ``id`` value from the URL, coerce it to an integer, and pass it to the function.
The :func:`~django.shortcuts.get_object_or_404` shortcut is the standard Django way to fetch a row or return a 404 response.
The parameter name in the signature can be anything, here ``note_id`` reads better than ``id`` which shadows the Python builtin.

Add the matching template.

.. code-block:: jinja
   :caption: notes/routes/notes/[id]/template.djx

   <article>
     <h2>{{ note.title }}</h2>
     {% if note.body %}<p>{{ note.body }}</p>{% endif %}
     <small>{{ note.created_at|date:"Y-m-d H:i" }}</small>
     <p><a href="{% url 'next:page_' %}">Back to all notes</a></p>
   </article>

Click a note from the index and confirm that the detail page renders the captured note.
The URL name ``next:page_notes_id`` reverses with a single keyword argument ``id`` and is generated from the directory shape.

Trace the Layout Stack
~~~~~~~~~~~~~~~~~~~~~~

The detail page renders inside the same root layout because every ancestor ``layout.djx`` wraps every descendant page.
Drop a second layout inside ``notes/routes/notes/`` to wrap only the detail pages.

.. code-block:: jinja
   :caption: notes/routes/notes/layout.djx

   <section>
     <nav>
       <a href="{% url 'next:page_' %}">All notes</a>
     </nav>
     {% block template %}{% endblock template %}
   </section>

Reload ``/notes/1/`` and you should see both layouts at once.
The root layout wraps the inner layout which wraps the detail template.

How Context Reaches the Template
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Context functions resolve once per request.
A ``@context("note")`` declared in ``notes/routes/notes/[id]/page.py`` runs only when a request hits that page.
A ``@context("site_name", inherit_context=True)`` declared in a layout runs for every descendant page, but only one time per request even if a sub-layout also references it.

Use Counts Across Pages
~~~~~~~~~~~~~~~~~~~~~~~

Add a small bit of inherited context that demonstrates DI between context functions.

.. code-block:: python
   :caption: notes/routes/layout.py

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

Reference the count in the layout.

.. code-block:: jinja
   :caption: notes/routes/layout.djx

   <header>
     <a href="{% url 'next:page_' %}"><h1>{{ site_name }}</h1></a>
     <p>{{ tagline }} ({{ note_count }} notes)</p>
   </header>

Every page now shows the running count without any per-page wiring.

Checkpoint
----------

Your project tree looks like this.

.. code-block:: text
   :caption: notes/routes layout

   routes/
     layout.djx
     layout.py
     page.py
     template.djx
     notes/
       layout.djx
       [id]/
         page.py
         template.djx

The index links to each note.
The detail page renders a single note pulled from the URL.
Inherited context flows from the root layout down to every page.

Common Pitfalls
---------------

Layout shows but page body does not.
   The layout must contain ``{% block template %}{% endblock template %}``.
   Without the placeholder the framework still renders the layout but cannot inject the page body.

DUrl returns a string instead of an int.
   Annotate the parameter as ``DUrl[int]``, not ``DUrl[str]``.
   The marker takes the conversion type from the generic argument and falls back to ``str`` when omitted.

Inherited context not available in a descendant.
   Make sure the layout that publishes the context sits above the page that consumes it, and the context function declares ``inherit_context=True``.

Next Steps
----------

Pages are still inline HTML.
The next part extracts the note rendering into a reusable component with its own CSS and JS.

.. seealso::

   :doc:`tutorial03` adds components and static assets.
   :doc:`/content/topics/layouts` covers layout composition rules.
   :doc:`/content/topics/context` covers ``inherit_context`` and serialization options.
