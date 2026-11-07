.. _howto-reverse-urls:

Reverse URLs
============

Problem
-------

You want to build a URL from Python code instead of hard coding a path.

Solution
--------

Use ``page_reverse`` to reverse a file-routed page URL.
Use ``with_query`` to add, replace, or remove query string parameters.

Walkthrough
-----------

Reverse a static page
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: static page URLs

   from next.urls import page_reverse

   url = page_reverse()             # "/"
   url = page_reverse("blog")       # "/blog/"
   url = page_reverse("about/team") # "/about/team/"

Call the helper from inside a request, not at the top level of a ``page.py``.
A page module executes while the router builds the URL patterns, so a module-level ``page_reverse()`` runs before the URLconf is complete and raises ``NoReverseMatch``.
Put the call in a ``@context`` callable, an action handler, or a ``render`` function, or reach for ``page_reverse_lazy`` when the value has to sit in a class body.

Reverse a captured page
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: captured params

   from next.urls import page_reverse

   # pages/notes/[int:id]/page.py
   url = page_reverse("notes/[int:id]", id=7)
   # "/notes/7/"

   # pages/posts/[slug]/comments/page.py
   url = page_reverse("posts/[slug]/comments", slug="hello")
   # "/posts/hello/comments/"

The path template spells each segment exactly as its directory name, converter included.
A ``notes/[int:id]`` directory answers to ``"notes/[int:id]"``, and ``"notes/[id]"`` raises ``NoReverseMatch`` because the converter is part of the URL name.
The parameter name in the template matches the keyword argument.

Reverse a page under a prefix
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: a pages() tree

   from next.urls import page_reverse

   url = page_reverse("log", namespace="audit")
   # "/staff/log/" for path("staff/", pages("audit"))

   url = page_reverse("", namespace="wallets", wallet=1)
   # "/wallets/1/operations/" for path("wallets/<int:wallet>/operations/", pages("wallets", "operations"))

A tree placed through ``pages()`` lives in the namespace of its application label, or the ``namespace`` the call passes, and its template starts below the prefix.
A converter in the route above the include travels as a keyword argument like any captured segment.

Add query parameters
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: search url

   from next.urls import page_reverse, with_query

   url = with_query(page_reverse("search"), query="next.dj", page=2)
   # "/search/?query=next.dj&page=2"

Pass ``None`` to remove a key.
Pass a list or tuple to repeat a key in the output.

Use inside an action handler
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: notes/pages/page.py

   from django.http import HttpRequest, HttpResponseRedirect
   from notes.models import Note

   from next.forms import ModelForm
   from next.urls import page_reverse

   class CreateNoteForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

       def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
           note = self.save()
           return HttpResponseRedirect(page_reverse("notes/[int:id]", id=note.id))

Use inside a component
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: _components/note_link/component.py

   from notes.models import Note

   from next import component
   from next.urls import page_reverse

   @component.context("href")
   def href(note: Note) -> str:
       return page_reverse("notes/[int:id]", id=note.id)

Verification
------------

Print the URL from a Django shell.

.. code-block:: bash
   :caption: shell

   uv run python manage.py shell -c "
   from next.urls import page_reverse
   print(page_reverse('notes/[int:id]', id=1))
   "

The shell prints ``/notes/1/``.

See also
--------

.. seealso::

   :doc:`/content/topics/url-reversing` for the topic guide.
   :doc:`/content/topics/file-router` for the URL name format.
