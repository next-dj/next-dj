.. _howto-group-pages-by-section:

Group pages by section without changing URLs
============================================

Problem
-------

A site mixes pages with different rules.
The sign-in pages are public and write a document of their own, while the rest of the site sits behind a login and shares one chrome.
Putting each set under a directory of its own would add a prefix to every URL.

Solution
--------

Name a directory with an identifier in parentheses, such as ``(auth)`` and ``(app)``.
The router treats it as a route group, so it adds nothing to the URL and nothing to the URL name, while its ``layout.djx`` and its ``page.py`` still scope everything below it, see :ref:`topics-file-router-route-groups`.

Walkthrough
-----------

Lay out the groups
~~~~~~~~~~~~~~~~~~

The sign-in pages go in ``(auth)`` and the rest of the site in ``(app)``.
Their URLs read ``/login/``, ``/logout/``, ``/`` and ``/notes/``.

.. code-block:: text
   :caption: accounts/pages/

   page.py
   (auth)/
     layout.djx
     page.py
     login/
       page.py
       template.djx
     logout/
       page.py
       template.djx
   (app)/
     layout.djx
     page.py
     template.djx
     notes/
       page.py
       template.djx

Gate the site and open the sign-in pages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An ``inherit=True`` rule in a ``page.py`` reaches every page below it.
The root ``page.py`` closes the whole tree, and the ``page.py`` of ``(auth)`` lifts its own pages out again.
A group ``page.py`` without a body routes nothing, and its declarations still fold into the pages below.

.. code-block:: python
   :caption: accounts/pages/page.py

   from next import page

   page.login_required(inherit=True)

.. code-block:: python
   :caption: accounts/pages/(auth)/page.py

   from next import page

   page.public(inherit=True)

Give each group its own chrome
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``layout.djx`` in a group wraps every page below the group.
Opening it with ``{% layout root %}`` cuts the layout chain, so the sign-in pages write a document of their own, see :doc:`give-a-section-its-own-document-shell`.

.. code-block:: jinja
   :caption: accounts/pages/(auth)/layout.djx

   {% layout root %}
   <!doctype html>
   <html>
     <head>
       {% metadata %}
       {% collect_styles %}
     </head>
     <body class="signin">
       {% template %}
       {% collect_scripts %}
     </body>
   </html>

Know what a group does not hide
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Moving a page into or out of a group changes neither its URL nor its URL name.
Static files stay co-located, so ``(auth)/login/page.css`` is named with ``(auth)`` in its path and a move renames the asset.
A page of a group beside a page of its parent that serves the same URL reports ``next.E015``.
A breadcrumb declared in the ``page.py`` of a group reports ``next.W136``, since a trail holds URL segments only.

Verification
------------

List the routes and read the access column.

.. code-block:: bash
   :caption: shell

   uv run python manage.py routes

``/login/`` and ``/logout/`` show as public and ``/notes/`` shows the login rule, with no ``(auth)`` or ``(app)`` in any URL.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

``next.W137`` names a group that holds one routed directory and nothing else, since such a group changes nothing.

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the ranking and flattening rules of a group.
   :doc:`/content/topics/access` for inherited access rules.
   :doc:`/content/topics/layouts` for root layouts.
   :doc:`/content/ref/system-checks` for ``next.E015``, ``next.W136``, and ``next.W137``.
