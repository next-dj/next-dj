.. _ref-permissions:

Permissions reference
=====================

Module summary
--------------

``next.permissions`` binds a predicate to each object permission ``app_label.codename`` and answers ``user.has_perm(perm, obj)`` through ``ObjectPermissionBackend``.
The package exports ``ObjectPermissionBackend``, ``PermissionAlreadyRegisteredError``, ``UnknownAccessObjectError``, ``add_perm``, and ``has_perm``.
``next.permissions.checks`` registers the system checks and stays out of the package namespace, so import it by its full path.
Nothing in it imports a model, so the package loads before the app registry is ready.
:doc:`/content/topics/permissions` covers the concepts, the access declarations, and the forms integration.

Predicates
----------

A predicate is a plain function that takes ``()``, ``(user)``, or ``(user, obj)``, at most two positional parameters with defaults counted, ``**kwargs`` allowed, and no ``*args`` or keyword-only parameter without a default.
``add_perm`` counts the positional parameters once, when it registers the function, and a check passes the function exactly that many of ``user`` and ``obj``, so a one-parameter function always receives the user.
It refuses no signature, so a module whose function breaks the rule still imports and ``next.E159`` reports it.

.. code-block:: python
   :caption: the three signatures

   from next.permissions import add_perm

   def maintenance_off():
       return True

   def is_staff(user):
       return user.is_staff

   def is_owner(user, obj=None):
       return obj is not None and obj.owner_id == user.pk

   add_perm("notes.publish_note", maintenance_off)
   add_perm("notes.moderate_note", is_staff)
   add_perm("notes.delete_note", is_owner)

A rule over several conditions combines them with ``and`` and ``or`` in the body of one function.

.. code-block:: python
   :caption: two conditions in one predicate

   def can_edit(user, obj=None):
       return user.is_staff or (obj is not None and obj.owner_id == user.pk)

Registration and questions
--------------------------

``add_perm(name, predicate)`` registers the function under the permission name.
A name outside ``app_label.codename`` raises ``ValueError``, the same function under a name already held changes nothing, and a different one raises ``PermissionAlreadyRegisteredError``.
The ``permissions.py`` module of every installed application is imported at startup, so that is where ``add_perm`` calls live.

``has_perm(user, perm, obj=None)`` answers from the registered predicates alone.
An inactive user holds nothing, an unregistered name answers ``False``, and the function neither asks another backend nor grants a superuser anything a predicate refuses.

.. automodule:: next.permissions.manager
   :members: add_perm, has_perm

Access declarations
-------------------

``login_required()``, ``permission_required(perm, *, obj=None)``, and ``public()`` record what a ``proxy.py``, a ``page.py``, or a ``component.py`` declares, see :ref:`topics-permissions-tree` and :doc:`pages` for where each is called.
Every page view, the action dispatcher, and a zone request check the declared rule before any application code runs, a login first, then every permission without an object, then each permission on the object, the order of ``AccessMixin`` and ``PermissionRequiredMixin``.
A refused login redirects to ``LOGIN_URL`` and a refused permission raises :exc:`~django.core.exceptions.PermissionDenied`.
A file executed again drops what it declared before, so a reload never keeps a stale requirement.
``manage.py permissions`` and the ``Access`` column of ``manage.py routes`` print the rule every page, action, and declaring component answers to, and ``manage.py permissions`` adds a ``view`` row for every other view of the URLconf, see :doc:`management`.

Backend
-------

``ObjectPermissionBackend`` belongs in ``AUTHENTICATION_BACKENDS`` next to :class:`~django.contrib.auth.backends.ModelBackend`.
It authenticates nobody and answers the authorization methods of :ref:`django:authorization_methods` from the registered predicates.
``has_module_perms`` asks the model-level question, ``obj`` set to ``None``, of every registered permission of the application.
The async twins run the predicates in a thread through ``sync_to_async``.

.. automodule:: next.permissions.backends
   :members: ObjectPermissionBackend

Errors
------

``PermissionAlreadyRegisteredError`` carries the permission ``name`` that was registered twice.
``UnknownAccessObjectError`` is a ``LookupError`` raised when the ``obj=`` of a component names no prop and no context the component produces, and it carries the context ``name`` and the ``endpoint`` file.
An ``obj=DObject[Model]`` of a page or a ``proxy.py`` whose route lacks the kwarg raises ``UnboundObjectError`` from ``next.deps`` instead, see ``next.E162``.

.. automodule:: next.permissions.errors

Template tag
------------

``{% has_perm perm [obj] %}`` … ``{% else %}`` … ``{% endhas_perm %}`` is a builtin from ``next.templatetags.permissions``, see :doc:`template-tags`.
A zone under the tag, or in the body of a component that decides what it shows, is refused to a partial request as an unknown zone, see :ref:`topics-permissions-zones`.

Management command
------------------

``manage.py permissions [url]`` prints the rule of every endpoint or of one page or view, see :doc:`management`.

System checks
-------------

``next.permissions.checks`` owns ``next.E159``, ``next.E160``, and ``next.W127``.
``next.W129`` lives with the forms checks and, like ``next.W127`` for a form guard, runs only in a project that declares access, ``next.E161`` with the zone checks, ``next.E162`` with the ``DObject`` checks, and ``next.E163``, ``next.E164``, and ``next.W128`` with the page checks, see :doc:`system-checks`.

See also
--------

.. seealso::

   :doc:`/content/topics/permissions` for the vocabulary, the backend, the tag, and the forms integration.
   :doc:`/content/howto/enforce-object-level-permissions` for an owner-only edit form.
   :doc:`forms` for ``Meta.permission_required`` and ``has_object_permission``.
   :doc:`/content/security/actions` for the rule an action answers to.
