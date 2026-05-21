.. _intro-tutorial01:

Building the First Page
=======================

Goal
----

By the end of this part the Notes application has a model, a fixture, and an index page at ``/`` that lists notes from the database.
The work uses the file router and a single ``@context`` function.

Prerequisites
-------------

You have followed :doc:`install` and you can serve a placeholder page at ``http://127.0.0.1:8000/``.
You have a Django application named ``notes`` registered in ``INSTALLED_APPS``.
From :doc:`install` your ``config/urls.py`` already forwards URLs to next.dj through ``include("next.urls")``.
If ``/`` does not respond, revisit :doc:`install`.

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
   uv run python manage.py shell <<'PY'
   from notes.models import Note
   Note.objects.create(title='First note', body='Hello, next.dj.')
   Note.objects.create(title='Second note', body='Pages are directories.')
   PY

Add the Index Page
~~~~~~~~~~~~~~~~~~

The file router treats the ``notes/pages/`` directory as the page root for the application.
The directory that contains a ``page.py`` becomes a URL.
``notes/pages/page.py`` therefore answers the empty path ``/``.
The router scans every application listed in ``INSTALLED_APPS`` for a ``pages/`` directory.

Create the page module.
A captioned code block holds the complete content of the named file unless the prose explicitly says to append to it.

.. code-block:: python
   :caption: notes/pages/page.py

   from notes.models import Note
   from next.pages import context

   @context("notes")
   def recent_notes() -> list[Note]:
       return list(Note.objects.all())

Create the template.

.. code-block:: jinja
   :caption: notes/pages/template.djx

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

Run the Server
~~~~~~~~~~~~~~

Start the development server.

.. code-block:: bash
   :caption: shell

   uv run python manage.py runserver

Visit ``http://127.0.0.1:8000/`` and you should see the two seeded notes.
The development server restarts itself on every Python edit, so reloading the browser page shows the change without a manual restart.

Trace the URL Name
~~~~~~~~~~~~~~~~~~

Every file-routed page receives a stable URL name in the ``next`` namespace.
For ``notes/pages/page.py`` that name is ``next:page_``.
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

next.dj contributes Django system checks for the page configuration.
They confirm each ``page.py`` has a render function or a paired template, that parameter directories carry a ``page.py``, and that the request context processor is installed.
See :doc:`/content/ref/system-checks` for the full catalog.
Run them and confirm no warnings remain.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

Checkpoint
----------

At the end of this part the project layout looks like this.

.. code-block:: text
   :caption: notes/ layout

   notes/
     models.py
     migrations/
       0001_initial.py
     pages/
       page.py
       template.djx

The index page lists notes from the database.
The URL ``/`` responds with HTML and the URL name ``next:page_`` reverses cleanly.

Common Pitfalls
---------------

Page module is not discovered.
   Confirm that ``NEXT_FRAMEWORK["DEFAULT_PAGE_BACKENDS"][0]["APP_DIRS"]`` is ``True``.

Template renders without the notes loop.
   Make sure ``notes/pages/template.djx`` sits next to ``notes/pages/page.py``.
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
   :doc:`/content/topics/pages` documents the rules behind ``pages/``.
   :doc:`/content/ref/pages` lists every public API on ``next.pages``.
