.. _intro-tutorial02:

Adding Layouts and Context
==========================

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
For the Notes application the most common layout sits next to the page tree at ``notes/pages/layout.djx``.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

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

Reverse names such as ``next:page_`` come from the file router.
See :doc:`/content/topics/file-router` for how directories become URLs and :doc:`/content/topics/url-reversing` for helpers such as ``page_reverse``.

Remove the now redundant HTML envelope from ``notes/pages/template.djx`` and keep only the page body.

.. code-block:: jinja
   :caption: notes/pages/template.djx

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

The layout references ``site_name`` and ``tagline``, but no page module produces them yet.
Add a ``page.py`` next to ``layout.djx`` to publish those values with ``inherit_context=True`` so every descendant page can read them.

.. code-block:: python
   :caption: notes/pages/page.py

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

Create a new directory ``notes/pages/notes/[id]/``.
The bracketed segment is a URL parameter that the file router captures as ``id``.

.. code-block:: python
   :caption: notes/pages/notes/[id]/page.py

   from django.shortcuts import get_object_or_404
   from notes.models import Note
   from next.pages import context
   from next.urls import DUrl

   @context("note")
   def fetch_note(note_id: DUrl["id", int]) -> Note:
       return get_object_or_404(Note, pk=note_id)

The ``DUrl["id", int]`` marker tells the :doc:`dependency injector </content/topics/dependency-injection>` to read the ``id`` segment captured by the ``[id]`` directory and coerce it to ``int``.
The segment name is given explicitly because the parameter ``note_id`` differs from the captured segment.
The :func:`~django.shortcuts.get_object_or_404` shortcut is the standard Django way to fetch a row or return a 404 response.

Add the matching template.

.. code-block:: jinja
   :caption: notes/pages/notes/[id]/template.djx

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
Drop a second layout inside ``notes/pages/notes/`` to wrap only the detail pages.

.. code-block:: jinja
   :caption: notes/pages/notes/layout.djx

   <section>
     <nav>
       <a href="{% url 'next:page_' %}">All notes</a>
     </nav>
     {% block template %}{% endblock template %}
   </section>

Reload ``/notes/1/`` and you should see both layouts at once.
The root layout wraps the inner layout which wraps the detail template.

Use Counts Across Pages
~~~~~~~~~~~~~~~~~~~~~~~

Add a small bit of inherited context that the layout reads on every page.
Append the ``note_count`` function to the existing ``notes/pages/page.py`` and add ``from notes.models import Note`` at the top of the file.

.. code-block:: python
   :caption: notes/pages/page.py

   @context("note_count", inherit_context=True)
   def note_count() -> int:
       return Note.objects.count()

Reference the count in the layout.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   <header>
     <a href="{% url 'next:page_' %}"><h1>{{ site_name }}</h1></a>
     <p>{{ tagline }} ({{ note_count }} notes)</p>
   </header>

Every page now shows the running count without any per-page wiring.

Checkpoint
----------

Your project tree looks like this.

.. code-block:: text
   :caption: notes/pages layout

   pages/
     layout.djx
     page.py
     template.djx
     notes/
       layout.djx
       [id]/
         page.py
         template.djx

The index links to each note.
The detail page renders a single note pulled from the URL.
Inherited context flows from the root ``page.py`` down to every page.

Common Pitfalls
---------------

Layout shows but page body does not.
   The layout must contain ``{% block template %}{% endblock template %}``.
   Without the placeholder the framework still renders the layout but cannot inject the page body.

``DUrl`` resolves to ``None`` when the captured segment is missing.
   ``DUrl[T]`` reads the segment whose name matches the parameter and coerces to ``T`` for ``int``, ``bool``, ``float``, and ``str`` as described in :doc:`/content/topics/dependency-injection`.
   ``DUrl["name"]`` returns the raw string for that segment.
   When the Python parameter name differs from the segment, use ``DUrl["id", int]`` for an ``[id]`` directory.

Inherited context not available in a descendant.
   Make sure the ``page.py`` that publishes the context sits in a directory above the page that consumes it, and the context function declares ``inherit_context=True``.

See :doc:`/content/faq/troubleshooting` for the full catalog of errors and fixes.

Next Steps
----------

Pages are still inline HTML.
The next part extracts the note rendering into a reusable component with its own CSS and JS.

.. seealso::

   :doc:`tutorial03` adds components and static assets.
   :doc:`/content/topics/layouts` covers layout composition rules.
   :doc:`/content/topics/context` covers ``inherit_context`` and serialization options.
