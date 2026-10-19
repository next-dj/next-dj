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

``wallets/pages/page.py`` now answers ``/wallets/`` and ``wallets/pages/[int:wallet]/page.py`` answers ``/wallets/<int:wallet>/``.
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
The layout and the contexts of ``pages/`` do not reach the ``operations`` tree, while its breadcrumbs start with those of the nearest page above it in the URL that the user may open, such as ``wallets/pages/[int:wallet]/page.py`` for ``/wallets/1/operations/``.

.. code-block:: python
   :caption: wallets/operations/page.py

   from wallets.models import Operation, Wallet

   from next import context
   from next.deps import DObject

   @context("wallet")
   def current_wallet(wallet: DObject[Wallet]) -> Wallet:
       return wallet

   @context("operations")
   def wallet_operations(wallet: DObject[Wallet]) -> list[Operation]:
       return list(Operation.objects.filter(wallet=wallet))

The route names the kwarg after the model, so ``DObject[Wallet]`` loads the row through ``visible_to(scope)`` and both contexts share one query, see :ref:`topics-di-dobject`.
A directory under ``operations/`` must not capture ``wallet`` again, which ``manage.py check`` reports as :ref:`next.E152 <ref-system-checks>`.

Declare the access of each tree
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A tree under a prefix inherits no ``proxy.py`` of the tree the prefix belongs to, so the ``operations`` tree keeps a ``proxy.py`` at its root, see :ref:`topics-proxy-tree-boundary`.

.. code-block:: python
   :caption: wallets/operations/proxy.py

   from wallets.models import Wallet

   from next.deps import DObject
   from next.pages import proxy

   proxy.login_required()
   proxy.permission_required("wallets.view_wallet", obj=DObject[Wallet])

``manage.py permissions`` lists the rule of every page of both trees, so a tree left without access shows in the table, see :doc:`/content/ref/management`.

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
   :doc:`/content/topics/proxy` for the access each tree declares.
   :doc:`/content/ref/management` for the columns of ``manage.py routes``.
