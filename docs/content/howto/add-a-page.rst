.. _howto-add-a-page:

Add a Page
==========

Problem
-------

You want to add a new URL to a running next.dj project without editing ``urls.py``.

Solution
--------

Create a directory under the page root and place a ``page.py`` plus a ``template.djx`` inside it.
The file router picks the directory up automatically.

Walkthrough
-----------

Pick the URL.
   Decide the URL path.
   For ``/about/`` the page lives at ``notes/routes/about/``.

Create the page module.

.. code-block:: python
   :caption: notes/routes/about/page.py

   from next.pages import context


   @context("body")
   def about_body() -> str:
       return "Hello from the about page."

Create the template.

.. code-block:: jinja
   :caption: notes/routes/about/template.djx

   <article>
     <h2>About</h2>
     <p>{{ body }}</p>
   </article>

Reload the server.
   If the development server is running, the autoreloader picks the new files up within a second.
   Otherwise restart ``uv run python manage.py runserver``.

Visit the URL.
   Open ``http://127.0.0.1:8000/about/`` and confirm the page renders.

Verification
------------

Reverse the URL name.

.. code-block:: bash
   :caption: shell

   uv run python manage.py shell -c "
   from django.urls import reverse
   print(reverse('next:page_about'))
   "

The shell prints ``/about/``.

Run system checks.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

The output reports no warnings.

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the URL name format.
   :doc:`/content/topics/pages` for the body source priority.
   :doc:`/content/intro/tutorial01` for a guided version of this recipe.
