.. _topics-access:

Page access
===========

A ``page.py`` declares who may reach its page, and with ``inherit=True`` every page below it, in the words of :func:`~django.contrib.auth.decorators.login_required` and :class:`~django.contrib.auth.mixins.PermissionRequiredMixin`.
The framework checks the declarations on the page view, on every action posted from that part of the tree, and on every zone request of its pages.
The model follows ``@context("name", inherit_context=True)``, which passes a value down the tree only when the ``page.py`` asks for it, see :doc:`context`.

.. contents::
   :local:
   :depth: 2

The declarations
----------------

A ``page.py`` declares through the ``page`` object of ``next`` at module level.

.. code-block:: python
   :caption: library/pages/library/book/[int:book]/page.py

   from library.models import Book

   from next import page
   from next.deps import DObject

   page.login_required(inherit=True)
   page.permission_required("library.change_book", obj=DObject[Book])

   template = "<h1>Edit the book</h1>"

``page.login_required(*, inherit=False)`` requires a logged-in user.
``page.permission_required(perm, *, obj=None, inherit=False)`` requires every permission in ``perm``, a name or a tuple of names, and with ``obj`` checks them on one row.
``page.public(*, inherit=False)`` lifts what the ``page.py`` files above require, see `Overriding with public()`_.

Each declaration binds the page of its own ``page.py``.
With ``inherit=True`` it binds every page below the directory as well, so the example requires a login on the book page and on every page under it, and the permission on the book page alone.

A refused request gets the answer of :func:`~django.contrib.auth.decorators.login_required` and :class:`~django.contrib.auth.mixins.PermissionRequiredMixin`.
An anonymous user is redirected to the login page with the full path of the request as ``next``, and a user short of a permission gets HTTP 403.
The order of the checks is described in :ref:`topics-permissions-tree`.

The object of a permission
~~~~~~~~~~~~~~~~~~~~~~~~~~

``obj=DObject[Model]`` names the row the permission is checked on.
The row loads by the URL kwarg named after the ``model_name`` of the model, so ``DObject[Book]`` reads ``book`` and the tree captures it as ``[int:book]`` or through the route a ``pages()`` include sits under.
The kwarg is the class name in lower case, so ``library.Book`` and ``shop.Book`` both read ``book``, and the declaration decides which model a captured ``book`` loads.
It loads through ``visible_to(scope)`` of the default manager, as a ``DObject`` parameter does, see :ref:`topics-di-dobject`.

A row the scope does not see answers 404 before the permission on that row is asked, so a refused user never tells a hidden row from a missing one.
The row shares its cache entry with a ``DObject`` parameter of the same model and primary key, so a page that also shows the book reads it with one query.
``obj`` takes ``DObject[Model]`` alone, and a string, a ``DObject`` with a parent, or a bare model raises ``TypeError`` where the call is written, since the check runs before any context exists.

``manage.py check`` reports a page the declaration binds whose route does not capture the kwarg as ``next.E162``, because every request to that page would raise ``UnboundObjectError``.

Inheritance
-----------

The rule of a page joins what every ``page.py`` above it declares with ``inherit=True``, from the root of its page tree down to the parent directory, then everything its own ``page.py`` declares, each requirement once, in that order.
A declaration without ``inherit`` stays on its own page, and nothing else reaches down the tree.

.. code-block:: text
   :caption: a tree with two inherited rules

   shop/pages/
     page.py                   login_required(inherit=True)              login
     orders/
       page.py                                                           login
       [int:order]/
         page.py               permission_required("shop.view_order",
                                 obj=DObject[Order], inherit=True)       login, perm shop.view_order on shop.Order
         invoice/
           page.py                                                       login, perm shop.view_order on shop.Order
         notes/
           page.py             permission_required("shop.note_order")    login, perm shop.view_order on shop.Order, perm shop.note_order

The right column is what ``manage.py permissions`` prints in its ``Access`` column for each page.
The permission of ``notes/`` binds that page alone, so ``invoice/`` never asks it.

.. _topics-access-declaring-page:

A directory without a page
~~~~~~~~~~~~~~~~~~~~~~~~~~

A directory without a ``page.py`` declares nothing, so the pages below it answer to the ``page.py`` files above it alone.
A directory that needs a rule of its own for its subtree but serves no page of its own gets a ``page.py`` that holds the declarations and nothing else.

.. code-block:: python
   :caption: shop/pages/account/page.py

   from next import page

   page.login_required(inherit=True)

Such a ``page.py`` has no ``render``, no ``template``, and no template a loader finds, and it declares at least one thing with ``inherit=True``.
It routes no URL, even beside or under a ``layout.djx``, while its ``inherit=True`` declarations bind every page below it.
``next.E012`` does not report it for its missing body, ``manage.py routes``, ``manage.py permissions``, and the sitemap leave it out, and a request to its directory answers 404 unless another tree routes that URL.
``manage.py check`` warns with ``next.W128`` when no page and no action sits below it, since it then guards nothing.
A declaration without ``inherit=True`` in such a file binds only its own page, which it does not serve, and the actions of its directory.
``manage.py check`` reports ``next.E166`` for it, alone or beside ``inherit=True`` declarations, when the directory declares no action, since the declaration then protects nothing.
The fix is ``inherit=True`` on the declaration or a template that makes the file a page.
A ``page.py`` without a body that only registers a ``@context(..., inherit_context=True)`` serves its subtree the same way, see :doc:`context`.
One that only declares an ``@action`` serves that action, which answers to its rule, and one that passes nothing below it and serves no action is ``next.E012``.

The same file may carry ``metadata``, such as a ``breadcrumb`` for the crumb trail of the pages below, see :doc:`seo/breadcrumbs`.

Overriding with public()
~~~~~~~~~~~~~~~~~~~~~~~~

``page.public()`` lifts every requirement inherited from above for its own page.
``page.public(inherit=True)`` lifts them for its page and every page below it.
A lift is always explicit, and nothing else removes an inherited requirement.

A booking receipt that opens from a direct link under a tree that requires a login reads like this.

.. code-block:: text
   :caption: a lifted subtree

   bookings/pages/
     page.py                   login_required(inherit=True)              login
     booking/[int:booking]/
       page.py                                                           login
       receipt/
         page.py               public(inherit=True)                      public
         admin/
           page.py             login_required(),
                               permission_required("bookings.manage")    login, perm bookings.manage

A requirement declared below the lift binds again, so ``receipt/admin/`` requires a login and a permission.
The requirements lifted above stay lifted there, so the page answers to what ``receipt/admin/page.py`` declares and to nothing of ``bookings/pages/page.py``.
``public()`` beside a ``login_required()`` or a ``permission_required()`` of the same file opens nothing and lifts nothing, whatever ``inherit`` each carries, so ``manage.py check`` reports it as ``next.E163``.

A lift is allowed and stays visible.
``manage.py permissions`` shows ``public`` in the ``Access`` column of every page under the lift and names the lifting file as ``public (bookings/pages/booking/[int:booking]/receipt/page.py)`` in the ``Sources`` column, see :doc:`/content/ref/management`.

.. _topics-access-default-login:

Login by default
----------------

A project that closes every page unless it opens it turns the default login on.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "LOGIN_REQUIRED": True,
   }

The key defaults to ``False``.
Listing :class:`~django.contrib.auth.middleware.LoginRequiredMiddleware` or a subclass of it in ``MIDDLEWARE`` turns the default login on the same way, with or without the key.
Under either one every page, action, and zone request whose rule asks nothing and is not public requires a logged-in user, and a component follows the page or the zone it renders in.
The answer is the same in both modes, a redirect to ``LOGIN_URL`` with the full path as ``next``, or to the login URL and the field name a listed subclass of the middleware gives through ``get_login_url()`` and ``get_redirect_field_name()``.
A login URL on another host or scheme gets an absolute ``next``, as the middleware gives it.

The key closes the page trees and nothing else.
A Django view of the project, such as a view in a ``views.py`` or one that calls ``render_component()``, stays open under the key, and the middleware or :func:`~django.contrib.auth.decorators.login_required` on the view closes it.
A project that routes its own views beside the trees therefore keeps the middleware, and a project whose views all live in page trees needs the key alone.

``manage.py check`` warns with ``next.W130`` about ``"LOGIN_REQUIRED": False`` written beside the middleware, which asks the login anyway, and with ``next.W131`` about ``"LOGIN_REQUIRED": True`` without ``django.contrib.auth`` or its ``AuthenticationMiddleware``, since no request then carries a signed-in user.

public() and login_not_required
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Django's :func:`~django.contrib.auth.decorators.login_not_required` marks one view that ``LoginRequiredMiddleware`` lets through, and ``page.public()`` does that and more.
The framework sets that mark on the view of every page whose rule is public, so the middleware and the framework never disagree.

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Concern
     - ``login_not_required``
     - ``page.public()``
   * - What it covers
     - The one view it decorates.
     - Its own page, or with ``inherit=True`` every page below it as well.
   * - The default login
     - Lifts the middleware check of that view.
     - Lifts the default login of the framework and of the middleware alike.
   * - Requirements of a ``page.py`` above
     - Not applicable, a view has no tree.
     - Lifted, so neither the login nor the permissions of a parent bind.
   * - Actions declared beside the page
     - Not covered, each needs a decorator of its own.
     - As public as the page.
   * - Zone requests of the page
     - Not applicable.
     - Covered, since a zone request travels the page view.
   * - ``manage.py permissions``
     - A ``view`` row reading ``public``.
     - ``public`` with the file that opened the rule.
   * - ``next.W129``
     - Not applicable.
     - Counts as an access declaration that opens its actions on purpose, so they draw no warning.

Actions, zones, and components
------------------------------

The rule of a page binds every way that page delivers content.

- An action declared in a file of the tree answers to the rule of the page in the directory of that file, joined with the rule of the posted origin page, see :doc:`/content/security/actions`.
- A zone request travels the URL of its page through the same view, so it gets the same redirect, 403, or 404 before any zone renders.
- A component renders inside a page or a zone whose rule has already passed.

An action and a zone under a lift therefore behave as the page of their subtree does, and an action under ``receipt/admin/`` requires what that page requires.

A ``component.py`` declares for its own component alone, through ``component.login_required()`` and ``component.permission_required(perm, *, obj=None)``.
It has no ``inherit`` and no ``public()``, and a refused request renders the component as an empty string, with no redirect and no 403, see :ref:`topics-permissions-tree`.
An ``@action`` in a ``component.py`` answers to the rule of its directory joined with what the component declares, so a user the component hides from cannot post to it either.
A POST renders no component, so a permission on a context, ``obj="name"``, finds no object there, the action refuses every user, and ``manage.py check`` reports it as ``next.E165``.

Loading
-------

The router reads the access of every page while it builds the URL patterns, so each ``page.py`` of a chain runs once before the first request.
The ``login_required`` mark the middleware reads is set at that point, while the framework reads the rule itself on every request.
In a process that watches template edits an edited ``page.py`` runs again on the next request, and its old declarations are dropped before the new ones register.
In a process that does not, a new ``page.py`` above a page, or an edit to one, applies after the process restarts, as any new module does.

A declaration binds to the module that calls it.
A call made from a helper module guards nothing, and ``manage.py check`` reports it as ``next.E164``.

.. _topics-access-tree-boundary:

The tree boundary
~~~~~~~~~~~~~~~~~

The ``page.py`` chain of a page stops at the root of its page tree.
An application tree a ``pages()`` include places under a prefix of another tree inherits no declaration of the host, so each tree declares its own access in a ``page.py`` at its root with ``inherit=True``, see :ref:`topics-file-router-trees` and :doc:`/content/howto/serve-an-application-under-a-prefix`.
A shell that every application shares lives in a project-level ``layout.djx``, see :doc:`layouts`.

A broken page.py
~~~~~~~~~~~~~~~~

A ``page.py`` that raises while importing closes its own page and every page below it, since what it would have declared with ``inherit=True`` is unknown.
Their views answer 404, or raise ``PageModuleImportError`` when ``next.conf.fail_loudly`` holds, and the actions of that part of the tree are refused the same way.
Under the default login an anonymous request meets the login redirect first, the answer ``LoginRequiredMiddleware`` gives before any view runs, so the key and the middleware answer alike and the redirect tells nothing about the broken file.
A ``Patches.morph`` onto a page under a broken ``page.py`` raises ``PageModuleImportError`` in production too, since a 404 would name the URL of the request in flight rather than the page.
The chain is read again on every request while a file in it is broken, so a fixed ``page.py`` serves on the next request.
``manage.py check --deploy`` reports the failure as ``next.E017``.

See also
--------

.. seealso::

   :doc:`permissions` for the permission vocabulary, the order of the check, and ``{% has_perm %}``.
   :doc:`/content/howto/require-login-on-pages` for a login on every page and the ``manage.py permissions`` table.
   :doc:`context` for ``inherit_context=True``.
   :doc:`/content/ref/pages` for the ``page`` object.
   :doc:`/content/ref/settings` for ``LOGIN_REQUIRED``.
   :doc:`/content/ref/system-checks` for ``next.E162`` to ``next.E164``, ``next.E166``, and ``next.W128`` to ``next.W131``.
