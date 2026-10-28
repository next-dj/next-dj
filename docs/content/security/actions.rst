.. _security-actions:

Actions are public endpoints
============================

Every form action answers at ``/_next/form/<uid>/``, an address outside the page URL space that any client can POST to, whatever page renders the form.
This page states which rule an action answers to, why the posted origin cannot loosen it, and how ``manage.py check`` points at an action nothing guards.

.. contents::
   :local:
   :depth: 2

The owner rule
--------------

An action declared in a file of a page tree answers to the rule of the page in the directory of that file, its owner.
That rule joins the ``inherit=True`` declarations of every ``page.py`` above the directory and every declaration of its own ``page.py``, so an action in a ``page.py`` and an action in a sibling module of the same directory answer to the same rule.
A directory without a ``page.py`` contributes the inherited declarations alone.
A ``public()`` that lifts the requirements above a directory lifts them for its actions too, so an action answers exactly as the page of its directory does, see :ref:`topics-access`.
An action in a ``component.py``, in a page tree or outside every tree, also answers to what that component declares, so it refuses whom the component hides from.

.. code-block:: python
   :caption: notes/pages/notes/page.py

   from next import page

   page.login_required(inherit=True)

.. code-block:: python
   :caption: notes/pages/notes/[int:note]/delete/page.py

   from django.http import HttpResponseRedirect
   from notes.models import Note

   from next import action, context, page
   from next.deps import DObject

   page.permission_required("notes.delete_note", obj=DObject[Note])

   @context("note")
   def note(note: DObject[Note]) -> Note:
       return note

   @action("delete_note")
   def delete_note(note: DObject[Note]) -> HttpResponseRedirect:
       note.delete()
       return HttpResponseRedirect("/notes/")

``delete_note`` needs no guard of its own, because it answers to the login ``notes/page.py`` passes down and to the permission of its page.
The dispatcher checks that rule before the ``Meta`` guard, the ``render()`` of the origin page and of the owner page, and any form hook, with the same order and the same answers the page view gives, see :ref:`topics-permissions-tree`.
An anonymous POST is redirected to ``LOGIN_URL`` with the origin path as ``next``, a refusal answers 403 and sends ``form_access_denied`` with ``layer="page"`` and ``reason="denied"``, and a broken ``page.py`` over the owner refuses the action as it closes the pages.

A forged origin lifts nothing
-----------------------------

The submission names its origin page in the hidden ``_next_form_origin`` field, and a client can replace it with the path of any routed page.
A POST without the field, or with a value that does not resolve through the URLconf, answers HTTP 400 before any access check, guard, or handler runs.
The rule of the origin page joins the owner rule for that request and removes none of its requirements.
Posting ``delete_note`` with the origin of a public page therefore still meets the login of ``notes/page.py`` and the permission of the note page.

A guard written inside the ``render()`` of the owner page holds as well.
When the ``page.py`` that declares an action has a ``render()``, the dispatcher refuses a POST whose origin lies outside the directory of that page with HTTP 403 before the handler runs.
An origin below that directory runs the owner ``render()`` with the URL kwargs of the origin, and a response it returns answers the POST.
``manage.py check`` reports such an action with ``next.W150`` while the page declares no access, because a declaration holds for every origin.

The owner rule binds only an action declared inside a page tree.
An action declared elsewhere, such as a shared form in an ``app/forms.py``, has no owner and answers to the rule of its origin page alone, so without the default login a client that names a public page as the origin reaches it.
Give such an action a guard of its own through ``Meta.login_required`` and ``Meta.permission_required`` or the same keywords on ``@action``, see :ref:`topics-forms-actions-guards`.

Under the default login
------------------------

The action endpoint is marked with :func:`~django.contrib.auth.decorators.login_not_required`, because :class:`~django.contrib.auth.middleware.LoginRequiredMiddleware` cannot tell which page an action serves.
The dispatcher applies the default login itself, under ``NEXT_FRAMEWORK["LOGIN_REQUIRED"]`` and under the middleware alike, so an owner rule that declares nothing and is not public requires a login, whatever origin the submission names, see :ref:`topics-access-default-login`.
An origin page that declares nothing and is not public requires the login as well, so the origin can close an action and never opens one.
An action whose owner rule is public, through a ``page.public()`` of its directory or a ``page.public(inherit=True)`` above it, stays open to anonymous users when posted from a public origin, such as the login form of the site.
An action declared outside any page tree has no public page to stand on, so it requires a login under the default login at any origin.

The public action warning
-------------------------

``manage.py check`` warns with ``next.W129`` about a file of a page tree whose actions nothing guards, no declaration of its page, no ``inherit=True`` declaration of a ``page.py`` above it, no ``Meta`` guard, and no default login.
One message covers every such action of the file, and its hint names the declarations that close them.
The warning runs only in a project that declares access and keeps the default login off, because a project without any access declaration keeps every action open by design.
A project declares access once it holds a ``login_required()``, ``permission_required()``, or ``public()`` in a ``page.py`` or a ``component.py``, or a permission registered through ``add_perm``.
A ``public()`` states that the actions under it are open on purpose, so they draw no warning.
An action declared outside a page tree is not judged, because its rule depends on the origin each request posts.

See also
--------

.. seealso::

   :ref:`topics-permissions-tree` for the declarations and the order of the check.
   :doc:`/content/internals/action-dispatch` for where the check sits in the dispatch pipeline.
   :doc:`di-and-untrusted-input` for the other values the posted origin feeds.
   :doc:`overview` for the rest of the access control surface.
