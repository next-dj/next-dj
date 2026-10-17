.. _ref-permissions:

Permissions reference
=====================

Module summary
--------------

``next.permissions`` binds a predicate to each object permission ``app_label.codename`` and answers ``user.has_perm(perm, obj)`` through ``ObjectPermissionBackend``.
It also holds the access a ``proxy.py``, a page, or a component declares and the one check every endpoint runs on it.
The package exports ``AccessRule``, ``Denial``, ``ObjectPermissionBackend``, ``PermissionAlreadyRegisteredError``, ``Predicate``, ``Requirement``, ``UnknownAccessObjectError``, ``add_perm``, ``check``, ``declared``, ``has_perm``, ``perms_for``, and ``predicate``, plus the ``signals`` module.
``next.permissions.checks`` registers the system checks and stays out of the package namespace, so import it by its full path.
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

Access declarations
-------------------

``login_required()``, ``permission_required(perm, *, obj=None)``, and ``public()`` record what a file declares, see :ref:`topics-permissions-tree` and :doc:`pages` for where each is called.
A ``Requirement`` is one declared condition, ``kind`` ``"login"`` or ``"permission"``, the ``source`` file, the ``perms`` it names, and ``obj``, the model whose row the URL names for a page or a ``proxy.py`` and a context name for a component, and it raises ``ValueError`` for a login carrying permissions or a permission naming none.
An ``AccessRule`` holds the ``requirements`` of one endpoint in the order its chain declared them, ``public`` when a ``public()`` opened it, the ``sources`` they came from or the file that opened a public rule, and ``lifted``, the files whose requirements a ``public()`` lifted.
A ``Denial`` carries the ``reason``, ``"login"`` or ``"permission"``, and the ``requirement`` that refused.

``check(user, rule, lookup=None)`` returns ``None`` when ``user`` passes ``rule`` and a ``Denial`` otherwise, in the order of ``AccessMixin`` and ``PermissionRequiredMixin``, the login first, then every permission without an object, then each permission on the value ``lookup(obj)`` returns.
Without a ``lookup`` a requirement with an object refuses, so a caller that cannot load the object never passes it by accident.
``answer(denial, next_url)`` turns a ``Denial`` into the response every page view, the action dispatcher, and a foreign zone morph give, a redirect to ``LOGIN_URL`` for a login and :exc:`~django.core.exceptions.PermissionDenied` for a permission, and ``signed_in(user)`` tells whether a user is authenticated, ``False`` for a request without one.
It returns a value rather than a response, so the page view redirects or raises, a component renders nothing, and a tool reads the reason.
``chained(rules)`` in ``next.permissions.markers`` joins the rules of a chain root first, a public rule lifting the requirements before it and a later requirement binding again, while ``joined(rules)`` joins rules side by side and lifts nothing, which an action uses for its owner and its origin.
``declared(file_path)`` returns the ``AccessRule`` a file declares about itself, an empty rule for a file that declares nothing, and ``next.permissions.access.declared_files()`` the files that declare anything, in the order they first did, which the checks read.
A file executed again drops what it declared before, so a reload never keeps a stale requirement.

``Requirement``, ``AccessRule``, and ``Denial`` live in ``next.permissions.markers`` beside the predicates, documented above.

.. automodule:: next.permissions.access

``next.permissions.endpoints`` reads the rule every page, action, and declaring component answers to without a request, which ``manage.py next permissions`` and the ``Access`` column of ``manage.py next routes`` print.
``Endpoint``, ``endpoints``, ``page_endpoint``, and ``resolve_page`` are its public names.
``endpoints(page_path=None)`` returns one ``Endpoint`` per page, action, and declaring component, or those of one page, ``page_endpoint(route)`` the endpoint of one route, which ``manage.py next routes`` reads, and ``resolve_page(url)`` the ``page.py`` path a URL resolves to.
An ``Endpoint`` carries its ``kind``, ``name``, ``file``, ``rule``, whether ``LoginRequiredMiddleware`` closes it, which holds only for a rule without requirements that is not public, and the ``broken`` file of its chain, and its ``access`` and ``sources`` properties spell the rule as the tables do.
Asked for one page, ``endpoints`` also marks the page ``broken`` when its composed template does not compile, since the components it renders cannot be read.
The rule of an action is its owner rule and its ``Meta`` guard, since the origin page joins only per request.

.. automodule:: next.permissions.endpoints

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
An ``obj=DObject[Model]`` of a page or a ``proxy.py`` whose route lacks the kwarg raises ``UnboundObjectError`` from ``next.deps`` instead, see ``next.E164``.

.. automodule:: next.permissions.errors

Template tag
------------

``{% has_perm perm [obj] %}`` … ``{% else %}`` … ``{% endhas_perm %}`` is a builtin from ``next.templatetags.permissions``, see :doc:`template-tags`.
A zone render asks every block above the zone again, and a refusal answers 403 for the whole request, while a zone in the body of a component that decides what it shows is refused as an unknown zone, see :doc:`/content/topics/permissions`.

Management command
------------------

``manage.py next permissions [url]`` prints the rule of every endpoint or of one page, see :doc:`management`.

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

``next.permissions.checks`` owns ``next.E161``, ``next.E162``, ``next.W130``, and ``next.W131``.
``next.W129`` and ``next.W134`` live with the forms checks, ``next.E163`` with the zone checks, ``next.E164`` with the ``DObject`` checks, and ``next.E165``, ``next.E166``, ``next.W132``, ``next.W133``, and ``next.W135`` with the page checks, see :doc:`system-checks`.

See also
--------

.. seealso::

   :doc:`/content/topics/permissions` for the vocabulary, the backend, the tag, and the forms integration.
   :doc:`/content/howto/enforce-object-level-permissions` for an owner-only edit form.
   :doc:`forms` for ``Meta.permission_required`` and ``has_object_permission``.
   :doc:`/content/security/actions` for the rule an action answers to.
