.. _ref-system-checks:

System Checks
=============

Module Summary
--------------

next.dj contributes Django system checks for every subsystem.
Run them through ``uv run python manage.py check`` and the framework reports configuration mistakes with a code and a hint.

Check Registration
------------------

``next.checks.register_all`` runs during ``AppConfig.ready``.
It imports each subsystem ``checks`` module so the ``@register`` side effects take effect.
The imported modules are ``next.conf.checks``, ``next.pages.checks``, ``next.urls.checks``, ``next.components.checks``, ``next.forms.checks``, ``next.server.checks``, and ``next.static.checks``.

Most of these modules register checks. ``next.server.checks`` registers no Django system checks.
The dependency injection layer contributes no Django system checks.

Shared Helpers
~~~~~~~~~~~~~~

``next.checks.common`` holds helpers reused across subsystem check modules. It is imported indirectly by those modules rather than by ``register_all``.

.. automodule:: next.checks.common
   :members:

Subsystem Checks
----------------

Pages
~~~~~

.. automodule:: next.pages.checks
   :members:

URLs
~~~~

.. automodule:: next.urls.checks
   :members:

Components
~~~~~~~~~~

.. automodule:: next.components.checks
   :members:

Forms
~~~~~

.. automodule:: next.forms.checks
   :members:

Static
~~~~~~

.. automodule:: next.static.checks
   :members:

Configuration
~~~~~~~~~~~~~

.. automodule:: next.conf.checks
   :members:

Server
~~~~~~

``next.server.checks`` registers no Django system checks, as noted under Check Registration.

Dependency Injection
~~~~~~~~~~~~~~~~~~~~

The dependency injection layer does not contribute Django system checks.
There is no ``next.ENNN`` code for a missing provider or a bad marker graph.

.. note::

   Expect misconfiguration at **runtime**: unresolved parameters become ``None``, and cycles raise ``DependencyCycleError``.
   Troubleshooting lives in :doc:`/content/topics/dependency-injection` and :doc:`/content/faq/troubleshooting`.

Check Code Reference
--------------------

The codes follow the :doc:`Django convention <django:ref/checks>` ``next.X<NN>`` where ``X`` is ``E`` for errors and ``W`` for warnings.

Errors
~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 12 58 30

   * - Code
     - Condition
     - Emitted by
   * - ``next.E001``
     - ``NEXT_FRAMEWORK`` is not a dict, or ``PAGE_BACKENDS`` is not a list.
     - ``next.urls.checks``
   * - ``next.E002``
     - A ``PAGE_BACKENDS`` or ``COMPONENT_BACKENDS`` entry is not a dict.
     - ``next.urls.checks``, ``next.components.checks``
   * - ``next.E003``
     - A page backend entry does not specify ``BACKEND``.
     - ``next.urls.checks``
   * - ``next.E004``
     - A page backend entry names an unknown backend.
     - ``next.urls.checks``
   * - ``next.E005``
     - The file router ``APP_DIRS`` value is not a boolean.
     - ``next.urls.checks``
   * - ``next.E006``
     - The file router ``DIRS`` or ``OPTIONS`` has the wrong shape or an unknown key.
     - ``next.urls.checks``
   * - ``next.E007``
     - The router manager fails to initialize.
     - ``next.checks.common``
   * - ``next.E008``
     - A ``[param]`` directory uses invalid parameter syntax.
     - ``next.pages.checks``
   * - ``next.E009``
     - A ``[[args]]`` directory uses invalid or incomplete args syntax.
     - ``next.pages.checks``
   * - ``next.E010``
     - A parameter directory is missing its ``page.py`` file.
     - ``next.pages.checks``
   * - ``next.E011``
     - An error was raised while checking page functions.
     - ``next.pages.checks``
   * - ``next.E012``
     - A ``page.py`` has no body source: no ``render`` function, no ``template`` attribute, no loader match, and no sibling ``layout.djx``.
     - ``next.pages.checks``
   * - ``next.E013``
     - A page ``render`` attribute is not callable.
     - ``next.pages.checks``
   * - ``next.E014``
     - An error was raised while checking URL conflicts.
     - ``next.urls.checks``
   * - ``next.E015``
     - The same URL pattern is defined in more than one location.
     - ``next.urls.checks``
   * - ``next.E016``
     - An error was raised while collecting patterns from a router.
     - ``next.urls.checks``
   * - ``next.E019``
     - ``request`` is missing from the template context (required for ``{% form %}`` and CSRF).
     - ``next.pages.checks``
   * - ``next.E020``
     - A component name is registered more than once within the same scope.
     - ``next.components.checks``
   * - ``next.E021``
     - A ``component.py`` imports ``context`` from ``next.pages`` instead of ``next.components``.
     - ``next.components.checks``
   * - ``next.E022``
     - ``PAGE_BACKENDS`` is empty.
     - ``next.urls.checks``
   * - ``next.E023``
     - ``COMPONENT_BACKENDS`` is not a list.
     - ``next.components.checks``
   * - ``next.E024``
     - A file router entry is missing ``PAGES_DIR``.
     - ``next.urls.checks``
   * - ``next.E025``
     - A file router entry is missing ``APP_DIRS``.
     - ``next.urls.checks``
   * - ``next.E026``
     - A file router entry is missing ``OPTIONS``.
     - ``next.urls.checks``
   * - ``next.E027``
     - A ``COMPONENTS_DIR`` or ``PAGES_DIR`` value is not a string.
     - ``next.components.checks``, ``next.urls.checks``
   * - ``next.E028``
     - A route repeats the same bracket parameter name.
     - ``next.urls.checks``
   * - ``next.E029``
     - A keyless ``@context`` callable is not annotated as returning a dict.
     - ``next.pages.checks``
   * - ``next.E030``
     - An error was raised while checking router pages.
     - ``next.pages.checks``
   * - ``next.E031``
     - A component backend entry is missing a required key.
     - ``next.components.checks``
   * - ``next.E032``
     - A component backend ``BACKEND`` or ``DIRS`` value has the wrong type.
     - ``next.components.checks``
   * - ``next.E033``
     - ``COMPONENT_BACKENDS`` is empty.
     - ``next.components.checks``
   * - ``next.E034``
     - A component name uses the shared root namespace on more than one page tree.
     - ``next.components.checks``
   * - ``next.E035``
     - A configuration dict has unknown keys.
     - ``next.checks.common``
   * - ``next.E036``
     - A static backend dotted path fails to import.
     - ``next.static.checks``
   * - ``next.E037``
     - A static backend entry is not a dict, or the class is not a ``StaticBackend`` subclass.
     - ``next.static.checks``
   * - ``next.E038``
     - ``STATIC_BACKENDS`` contains a duplicate ``BACKEND`` entry.
     - ``next.static.checks``
   * - ``next.E040``
     - A configured context processor does not accept a ``request`` parameter.
     - ``next.pages.checks``
   * - ``next.E041``
     - A form action name is registered by more than one handler.
     - ``next.forms.checks``
   * - ``next.E042``
     - A ``TEMPLATE_LOADERS`` entry is not a dotted-path string.
     - ``next.pages.checks``
   * - ``next.E043``
     - A ``TEMPLATE_LOADERS`` entry cannot be imported or is not a ``TemplateLoader`` subclass.
     - ``next.pages.checks``
   * - ``next.E044``
     - A form action backend entry has the wrong shape or cannot be imported.
     - ``next.forms.checks``
   * - ``next.E045``
     - A form action backend class does not subclass ``FormActionBackend``.
     - ``next.forms.checks``
   * - ``next.E047``
     - A form class ``Meta.scope`` or an ``@action`` ``scope`` keyword is set to a value other than ``"page"`` or ``"shared"``.
     - ``next.forms.checks``
   * - ``next.E048``
     - ``Meta.instance_from_url`` references a field name that does not exist on the model.
     - ``next.forms.checks``
   * - ``next.E049``
     - ``Meta.instance_from_url`` is set on a class that is not a ``ModelForm`` subclass.
     - ``next.forms.checks``
   * - ``next.E050``
     - A ``FormWizard`` declares no ``Meta.steps`` or an empty list.
     - ``next.forms.checks``
   * - ``next.E051``
     - ``FORM_WIZARD_BACKEND`` is malformed, non-importable, or names a class that does not subclass ``FormWizardBackend``.
     - ``next.forms.checks``
   * - ``next.E052``
     - ``FORM_ANCHOR_FILES`` is not None or a list, tuple, or set of strings.
     - ``next.forms.checks``
   * - ``next.E053``
     - ``@action`` was applied to a class instead of a function.
     - ``next.forms.checks``

A code emitted by ``next.checks.common`` is produced by a shared helper that the listed subsystem check modules call.

Warnings
~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 12 58 30

   * - Code
     - Condition
     - Emitted by
   * - ``next.W001``
     - A ``layout.djx`` is missing the required ``{% block template %}``.
     - ``next.pages.checks``
   * - ``next.W030``
     - ``STATIC_BACKENDS`` is empty, so the framework falls back to ``StaticFilesBackend``.
     - ``next.static.checks``
   * - ``next.W031``
     - An ``OPTIONS`` tag template is missing the ``{url}`` placeholder.
     - ``next.static.checks``
   * - ``next.W042``
     - ``JS_CONTEXT_SERIALIZER`` is set but does not resolve to a usable serializer.
     - ``next.static.checks``
   * - ``next.W043``
     - A ``page.py`` declares more than one body source and the lower-priority ones are ignored.
     - ``next.pages.checks``
   * - ``next.W046``
     - A form class is declared in a file outside ``BASE_DIR`` and will not be registered automatically.
     - ``next.forms.checks``
   * - ``next.W054``
     - A ``ComponentWidget`` names a component that does not resolve.
     - ``next.forms.checks``
   * - ``next.W055``
     - A ``ComponentWidget`` is attached to a ``FileField`` or ``MultiValueField``, which it does not support.
     - ``next.forms.checks``
   * - ``next.W056``
     - Wizards are registered and the configured wizard backend keys stored steps by session, but ``django.contrib.sessions`` is not installed.
     - ``next.forms.checks``

.. note::

   Codes are assigned per check and are not contiguous. Inspect the source of each
   subsystem module above for the exact message text and trigger conditions.

See Also
--------

.. seealso::

   :doc:`/content/intro/install` for the first ``manage.py check`` run.
   :doc:`/content/faq/troubleshooting` for symptoms that map to individual ``next.*`` codes.
