.. _topics-proxy:

proxy.py
========

A ``proxy.py`` declares who may reach a directory of a page tree and every page below it.
It holds access declarations and nothing else, no context and no data, and the framework checks them on the pages, the actions, and the zone requests of that part of the tree.

.. contents::
   :local:
   :depth: 2

The files of a segment
----------------------

A directory of a page tree can hold three files, and each has one job.

``layout.djx``
   The markup that wraps every page below the directory, see :doc:`layouts`.

``page.py``
   The logic of one page, its contexts, its actions, its metadata, and the access of that page alone.
   A value every page below needs comes from ``@context("name", inherit_context=True)`` in the ``page.py`` of the directory, see :doc:`context`.

``proxy.py``
   The access of the directory and every page below it, and the redirect a refused request gets.

A section that needs a shell, a shared value, and a login therefore keeps them in three files, and none of them reaches into the job of another.

The declarations
----------------

A ``proxy.py`` declares through the ``proxy`` object of ``next.pages`` at module level.

.. code-block:: python
   :caption: library/book_pages/proxy.py

   from library.models import Book

   from next.deps import DObject
   from next.pages import proxy

   proxy.login_required()
   proxy.permission_required("library.change_book", obj=DObject[Book])

``proxy.login_required()`` requires a logged-in user.
``proxy.permission_required(perm, obj=None)`` requires every permission in ``perm``, a name or a tuple of names, and with ``obj`` checks them on one row.
``proxy.public()`` lifts the requirements of every ``proxy.py`` above, see `Overriding with public()`_.
``page`` offers the same three calls in a ``page.py``, where they bind that page alone.

A refused request gets the answer of :func:`~django.contrib.auth.decorators.login_required` and :class:`~django.contrib.auth.mixins.PermissionRequiredMixin`.
An anonymous user is redirected to ``LOGIN_URL`` with the full path of the request as ``next``, and a user short of a permission gets HTTP 403.
The order of the checks and the places they apply are described in :ref:`topics-permissions-tree`.

The object of a permission
~~~~~~~~~~~~~~~~~~~~~~~~~~

``obj=DObject[Model]`` names the row the permission is checked on.
The row loads by the URL kwarg named after the ``model_name`` of the model, so ``DObject[Book]`` reads ``book`` and the tree captures it as ``[int:book]`` or through the prefix it is mounted under.
The kwarg belongs to one model, so ``library.Book`` and ``shop.Book`` both read ``book``, and a route that captures it serves only the model the declaration names.
It loads through ``visible_to(scope)`` of the default manager, as a ``DObject`` parameter does, see :ref:`topics-di-dobject`.

A row the scope does not see answers 404 before any permission is asked, so a refused user never tells a hidden row from a missing one.
The row shares its cache entry with a ``DObject`` parameter of the same model and primary key, so a page that also shows the book reads it with one query.
``obj`` takes ``DObject[Model]`` alone, and a string, a ``DObject`` with a parent, or a bare model raises ``TypeError`` where the call is written, since the check runs before any context exists.

``manage.py check`` reports a page under the declaration whose route does not capture the kwarg as ``next.E164``, because every request to that page would raise ``UnboundObjectError``.

Inheritance
-----------

The rule of a page joins every ``proxy.py`` from the root of its page tree down to the directory of the page, then the declarations of the ``page.py`` itself, each requirement once, in that order.
A ``proxy.py`` in the directory of the page counts, so a ``proxy.py`` beside a ``page.py`` binds that page and every page below it.

.. code-block:: text
   :caption: a tree with two proxy.py files

   shop/pages/
     proxy.py                  login_required()
     orders/
       page.py                 login
       [int:order]/
         proxy.py              permission_required("shop.view_order", obj=DObject[Order])
         page.py               login, perm shop.view_order on shop.Order
         invoice/
           page.py             login, perm shop.view_order on shop.Order

Overriding with public()
~~~~~~~~~~~~~~~~~~~~~~~~

``public()`` in a deeper ``proxy.py`` lifts every requirement declared above it for its directory and every page below it.
``page.public()`` does the same for its page alone.
A lift is always explicit, and nothing else removes an inherited requirement.

A booking receipt that opens from a direct link under a tree that requires a login reads like this.

.. code-block:: text
   :caption: a lifted subtree

   bookings/pages/
     proxy.py                  login_required()
     booking/[int:booking]/
       page.py                 login
       receipt/
         proxy.py              public()
         page.py               public
         admin/
           proxy.py            login_required(), permission_required("bookings.manage")
           page.py             login, perm bookings.manage

A requirement declared below the lift binds again, so ``receipt/admin/`` requires a login and a permission.
The requirements lifted above stay lifted there, so the page answers to what ``receipt/admin/proxy.py`` declares and to nothing of ``bookings/pages/proxy.py``.
``public()`` beside a ``login_required()`` or a ``permission_required()`` of the same file opens nothing and lifts nothing, so the requirements of the file and of every parent still bind, and ``manage.py check`` reports it as ``next.E165``.

A lift is allowed and never silent.
``manage.py check`` warns with ``next.W135`` once per file whose ``public()`` lifts the requirements of a guarded parent, naming the files it lifts.
``manage.py next permissions`` shows ``public`` in the ``Access`` column of every page under the lift and names the lifting file as ``public (bookings/pages/booking/[int:booking]/receipt/proxy.py)`` in the ``Sources`` column, see :doc:`/content/ref/management`.

A public rule also sets the ``login_required`` attribute of the page view to ``False``, so :class:`~django.contrib.auth.middleware.LoginRequiredMiddleware` lets the receipt through, see :doc:`/content/howto/require-login-on-pages`.

Actions, zones, and components
------------------------------

The rule of a page binds every way that page delivers content.

- An action declared in a file of the tree answers to the rule of the page in the directory of that file, joined with the rule of the posted origin page, see :doc:`/content/security/actions`.
- A zone request travels the URL of its page through the same view, so it gets the same redirect, 403, or 404 before any zone renders.
- A component renders inside a page whose rule has already passed, and its ``component.py`` adds declarations of its own.

An action and a zone under a lift therefore behave as the page of their subtree does, and an action under ``receipt/admin/`` requires what that page requires.

Loading
-------

The router reads the access of every page while it builds the URL patterns, so each ``proxy.py`` over a routed page runs once before the first request.
In a process that watches template edits an edited ``proxy.py`` runs again on the next request, and its old declarations are dropped before the new ones register.
``manage.py check`` warns with ``next.W132`` about a ``proxy.py`` that has no page in its directory or below it.

A declaration binds to the module that calls it.
A call made from a helper module guards nothing, and ``manage.py check`` reports it as ``next.E166``.

The tree boundary
~~~~~~~~~~~~~~~~~

The ``proxy.py`` chain of a page stops at the root of its page tree.
An application tree mounted under a prefix of another tree inherits no ``proxy.py`` of the host, so each tree declares its own access in a ``proxy.py`` at its root.
A shell that every application shares lives in a project-level ``layout.djx``, see :doc:`layouts`.

A broken proxy.py
~~~~~~~~~~~~~~~~~

A ``proxy.py`` that raises while importing closes every page below it.
Their views answer 404, or raise ``PageModuleImportError`` when ``next.conf.fail_loudly`` holds, as a broken ``page.py`` does, and the actions of that part of the tree are refused the same way.
A requirement the file would have declared is never silently lost, since nothing below it renders.
The chain is read again on every request while a file in it is broken, so a fixed ``proxy.py`` serves on the next request.
``manage.py check --deploy`` reports the failure as ``next.E017``.

Compared with proxy.ts in Next.js
---------------------------------

Next.js keeps one ``proxy.ts`` at the root of a project.
It runs before routing for every path its ``matcher`` selects, and the Next.js guides describe the check it runs as an optimistic one, such as reading a session cookie, with the authoritative check placed next to the data.

A ``proxy.py`` in next.dj belongs to one directory of a page tree, and the rule of a page joins every ``proxy.py`` above it.
The rule runs on every path that delivers the page, the page view, an action posted from the tree, and a zone request, and a component inside the page renders only once the rule has passed.
A permission with ``obj=DObject[Model]`` loads the row from the database through ``visible_to(scope)`` and asks ``user.has_perm(perm, row)`` before the page runs any context.

See also
--------

.. seealso::

   :doc:`permissions` for the permission vocabulary, the checks, and ``{% has_perm %}``.
   :doc:`layouts` for the ``layout.djx`` chain.
   :doc:`context` for ``inherit_context=True``.
   :doc:`/content/ref/pages` for the ``proxy`` object.
   :doc:`/content/ref/system-checks` for ``next.E164`` to ``next.E166`` and ``next.W131`` to ``next.W135``.
