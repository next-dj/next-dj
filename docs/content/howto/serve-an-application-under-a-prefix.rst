.. _howto-serve-an-application-under-a-prefix:

Serve an application under a prefix
===================================

Problem
-------

The pages of an application should answer under a URL prefix such as ``/wallets/``, without repeating that prefix as the first directory of its page tree.
A second tree of the same application needs a prefix that captures a parameter.

Solution
--------

Include the tree from the root URLconf through ``pages()`` of ``next.urls`` under the route it should answer at.
Every tree no ``pages()`` names keeps routing at the root through ``include("next.urls")``.
:ref:`topics-file-router-trees` describes the rules in full.

Walkthrough
-----------

Place the tree
~~~~~~~~~~~~~~

Name the application in ``pages()`` and include the result under the prefix.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   from next.urls import pages

   urlpatterns = [
       path("wallets/", pages("wallets")),
       path("", include("next.urls")),
   ]

``wallets/pages/page.py`` now answers ``/wallets/`` and ``wallets/pages/[int:pk]/page.py`` answers ``/wallets/<int:pk>/``.
The tree leaves ``next.urls``, so it no longer answers at the root as well.
Without a second argument ``pages()`` places the ``PAGES_DIR`` tree of the application, ``pages/`` with the default setting.

Add a second tree with a parameter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A directory beside ``pages/`` holds a page tree of its own, which only a ``pages()`` include routes.
The second argument names that directory, and the ``namespace`` keyword gives it a namespace apart from the first tree.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   from next.urls import pages

   urlpatterns = [
       path("wallets/", pages("wallets")),
       path(
           "wallets/<int:wallet>/operations/",
           pages("wallets", "operations", namespace="wallet_operations"),
       ),
       path("", include("next.urls")),
   ]

The ``wallet`` value the route captures reaches each page of that tree as a URL kwarg, as it does for any :func:`~django.urls.include`.
The layout and the contexts of ``pages/`` do not reach the ``operations`` tree, while its breadcrumbs start with those of the nearest page above it in the URL that the user may open, such as ``wallets/pages/[int:pk]/page.py`` for ``/wallets/1/operations/``.

.. code-block:: python
   :caption: wallets/operations/page.py

   from wallets.models import Operation, Wallet

   from next import context
   from next.urls import DUrl

   @context("wallet")
   def current_wallet(wallet: DUrl[int]) -> Wallet:
       return Wallet.objects.get(pk=wallet)

   @context("operations")
   def wallet_operations(wallet: DUrl[int]) -> list[Operation]:
       return list(Operation.objects.filter(wallet_id=wallet))

A directory under ``operations/`` must not capture ``wallet`` again, which ``manage.py check`` reports as :ref:`next.E152 <ref-system-checks>`.

Reverse the placed pages
~~~~~~~~~~~~~~~~~~~~~~~~

The pages of a ``pages()`` tree are named in its namespace, the application label unless ``namespace`` sets another, and without the prefix.

.. code-block:: python
   :caption: reversing a pages() tree

   from next.urls import page_reverse

   page_reverse("", namespace="wallets")                       # "/wallets/"
   page_reverse("", namespace="wallet_operations", wallet=1)   # "/wallets/1/operations/"

Verification
------------

List the routes with their names.

.. code-block:: bash
   :caption: shell

   uv run python manage.py routes

The ``URL`` column shows each prefix and the ``Name`` column the namespace of each tree.
Run ``uv run python manage.py check`` to confirm that no page captures a name of its prefix and no route conflicts with another.

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the route order and the names of every tree.
   :doc:`/content/topics/project-layout` for where prefixes fit as a project grows.
   :doc:`/content/ref/management` for the columns of ``manage.py routes``.
