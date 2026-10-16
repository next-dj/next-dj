.. _ref-permissions:

Permissions reference
=====================

Module summary
--------------

``next.permissions`` binds a predicate to each object permission ``app_label.codename`` and answers ``user.has_perm(perm, obj)`` through ``ObjectPermissionBackend``.
The package exports ``ObjectPermissionBackend``, ``PermissionAlreadyRegisteredError``, ``Predicate``, ``add_perm``, ``has_perm``, ``perms_for``, and ``predicate``, plus the ``checks`` and ``signals`` modules.
Nothing in it imports a model, so the package loads before the app registry is ready.
:doc:`/content/topics/permissions` covers the concepts and the forms integration.

Predicates
----------

``predicate`` wraps a callable into a leaf ``Predicate`` and returns a ``Predicate`` it receives unchanged.
The callable takes ``()``, ``(user)``, or ``(user, obj)``, at most two positional parameters with defaults counted, ``**kwargs`` allowed, and no ``*args`` or keyword-only parameter without a default.
``predicate`` counts the positional parameters once and stores the count as ``arity``, and a call passes the leaf exactly that many of ``user`` and ``obj``, so a one-parameter callable always receives the user.
It refuses no signature, so a module whose callable breaks the rule still imports and ``next.E161`` reports it.

.. code-block:: python
   :caption: the three signatures

   from next.permissions import predicate

   @predicate
   def maintenance_off():
       return True

   @predicate
   def is_staff(user):
       return user.is_staff

   @predicate
   def is_owner(user, obj=None):
       return obj is not None and obj.owner_id == user.pk

A ``Predicate`` is a frozen value, a leaf holding ``test`` and ``arity`` or a composite holding ``operator`` and ``operands``, and it raises ``ValueError`` when it is built in a shape it cannot evaluate, such as a leaf with operands or an ``arity`` outside 0 to 2.
``&``, ``|``, and ``~`` build composites over operands of any arity, which evaluate their operands left to right and stop once the answer is known, and ``&`` or ``|`` with an operand that is no ``Predicate`` raises ``TypeError``.
Calling a predicate as ``rule(user, obj)`` returns a ``bool``, ``obj`` defaulting to ``None``.
``tests()`` yields the name and the callable of each leaf, left to right.

.. automodule:: next.permissions.markers
   :members: Predicate, predicate

Registration and questions
--------------------------

``add_perm(name, predicate)`` registers the predicate, or a plain callable it wraps, and returns the ``Predicate`` it holds.
A name outside ``app_label.codename`` raises ``ValueError``, an equal predicate under a name already held changes nothing, and a different one raises ``PermissionAlreadyRegisteredError``.
Each new registration sends ``permission_registered``.

``has_perm(user, perm, obj=None)`` and ``perms_for(user, obj=None)`` answer from the registered predicates alone.
An inactive user holds nothing, an unregistered name answers ``False``, and neither function asks another backend or grants a superuser anything a predicate refuses.
``perms_for`` returns a ``frozenset`` of names.

.. automodule:: next.permissions.manager
   :members: add_perm, has_perm, perms_for

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

.. automodule:: next.permissions.errors
   :members: PermissionAlreadyRegisteredError

Template tag
------------

``{% has_perm perm [obj] %}`` … ``{% else %}`` … ``{% endhas_perm %}`` is a builtin from ``next.templatetags.permissions``, see :doc:`template-tags`.

Setting
-------

``PERMISSIONS_AUTODISCOVER``, default ``True``, imports the ``permissions`` submodule of every installed application at startup, see :doc:`settings`.

Signal
------

``permission_registered`` lives in ``next.permissions.signals`` and is re-exported from ``next.signals``.
The sender is ``PermissionRegistry``, and the keyword arguments are ``name`` and ``predicate``, the ``Predicate`` registered under it.
It fires once per new registration, never for a repeated equal one, see :doc:`signals`.

System checks
-------------

``next.permissions.checks`` owns ``next.E161``, ``next.E162``, and ``next.W130``, and ``next.W129`` and ``next.E163`` live with the forms and the zone checks, see :doc:`system-checks`.

See also
--------

.. seealso::

   :doc:`/content/topics/permissions` for the vocabulary, the backend, the tag, and the forms integration.
   :doc:`/content/howto/enforce-object-level-permissions` for an owner-only edit form.
   :doc:`forms` for ``Meta.permission_required`` and ``has_object_permission``.
