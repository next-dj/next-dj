.. _intro-tutorial01:

Tutorial Part 1 First Page
==========================

Goal
----

By the end of this part the Notes application has a model, a fixture, and an index page at ``/`` that lists notes from the database.
The work uses the file router and a single ``@context`` function.

Prerequisites
-------------

You have followed :doc:`install` and you can serve a placeholder page at ``http://127.0.0.1:8000/``.
You have a Django application named ``notes`` registered in ``INSTALLED_APPS``.

Walkthrough
-----------

Model the Note
~~~~~~~~~~~~~~

Create a single :doc:`Django model <django:topics/db/models>` that represents a note.
Keep the fields minimal so the tutorial stays focused on the framework.

.. code-block:: python
   :caption: notes/models.py

   from django.db import models


   class Note(models.Model):
       title = models.CharField(max_length=120)
       body = models.TextField(blank=True)
       created_at = models.DateTimeField(auto_now_add=True)

       class Meta:
           ordering = ("-created_at",)

       def __str__(self) -> str:
           return self.title

Apply the :doc:`migration <django:topics/migrations>` and seed two rows so the index page has something to render.

.. code-block:: bash
   :caption: shell

   uv run python manage.py makemigrations notes
   uv run python manage.py migrate
   uv run python manage.py shell -c "
   from notes.models import Note
   Note.objects.create(title='First note', body='Hello, next.dj.')
   Note.objects.create(title='Second note', body='Pages are directories.')
   "

Add the Index Page
~~~~~~~~~~~~~~~~~~

The file router treats the ``notes/routes/`` directory as the page root for the application.
The directory that contains a ``page.py`` becomes a URL.
``notes/routes/page.py`` therefore answers the empty path ``/``.

Create the page module.

.. code-block:: python
   :caption: notes/routes/page.py

   from notes.models import Note

   from next.pages import context


   @context("notes")
   def recent_notes() -> list[Note]:
       return list(Note.objects.all())

Create the template.

.. code-block:: jinja
   :caption: notes/routes/template.djx

   <!doctype html>
   <html>
     <body>
       <h1>Notes</h1>
       <ul>
         {% for note in notes %}
           <li>
             <strong>{{ note.title }}</strong>
             {% if note.body %}<p>{{ note.body }}</p>{% endif %}
             <small>{{ note.created_at|date:"Y-m-d H:i" }}</small>
           </li>
         {% endfor %}
       </ul>
     </body>
   </html>

The framework composes the body of ``template.djx`` with any ancestor ``layout.djx``.
You do not have a layout yet, so the page renders standalone.

Wire the Static App
~~~~~~~~~~~~~~~~~~~

The router needs to know that ``notes`` ships pages.
The ``APP_DIRS`` flag from :doc:`install` already does the discovery, so no extra change to ``settings.py`` is required.
The router will scan every application listed in ``INSTALLED_APPS`` for a ``routes/`` directory.

Run the Server
~~~~~~~~~~~~~~

Start the development server.

.. code-block:: bash
   :caption: shell

   uv run python manage.py runserver

Visit ``http://127.0.0.1:8000/`` and you should see the two seeded notes.

Trace the URL Name
~~~~~~~~~~~~~~~~~~

Every file-routed page receives a stable URL name in the ``next`` namespace.
For ``notes/routes/page.py`` that name is ``next:page_``.
Open a Django shell and reverse it to confirm.

.. code-block:: bash
   :caption: shell

   uv run python manage.py shell -c "
   from django.urls import reverse
   print(reverse('next:page_'))
   "

The shell prints ``/``.
You will use these names from templates through the standard ``{% url %}`` tag.

Inspect Through System Checks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

next.dj contributes a Django system check that confirms each page module is loadable.
Run it and confirm no warnings remain.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check notes

Checkpoint
----------

At the end of this part the project layout looks like this.

.. code-block:: text
   :caption: notes/ layout

   notes/
     models.py
     migrations/
       0001_initial.py
     routes/
       page.py
       template.djx

The index page lists notes from the database.
The URL ``/`` responds with HTML and the URL name ``next:page_`` reverses cleanly.

Common Pitfalls
---------------

Page module is not discovered.
   Confirm that ``notes`` is in ``INSTALLED_APPS`` and that ``NEXT_FRAMEWORK["DEFAULT_PAGE_BACKENDS"][0]["APP_DIRS"]`` is ``True``.

Template renders without the notes loop.
   Make sure ``notes/routes/template.djx`` sits next to ``notes/routes/page.py``.
   The framework pairs a ``page.py`` with the ``template.djx`` in the same directory.

ImportError for ``Note``.
   The ``notes`` app must be installed and migrated.
   Re-run ``uv run python manage.py migrate``.

Next Steps
----------

You have a page that renders dynamic data.
The next part wraps every page in a layout and shares site-wide context.

.. seealso::

   :doc:`tutorial02` adds a layout and shared context.
   :doc:`/content/topics/file-router` covers route shapes, captured parameters, and virtual routes.
   :doc:`/content/ref/pages` lists every public API on ``next.pages``.
