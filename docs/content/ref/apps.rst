.. _ref-apps:

Apps Reference
==============

Module Summary
--------------

``next.apps`` contains the Django ``AppConfig`` and the helpers that the framework runs at application startup.

``NextFrameworkConfig.ready()`` runs ``next.checks.register_all()`` first, then ``autoreload.install()``, ``templates.install()``, ``staticfiles.install()``, and ``components.install()`` in that order.

Public API
----------

.. automodule:: next.apps
   :members:

Template Tag Registration
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.templates
   :members:

Staticfiles Integration
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.staticfiles
   :members:

Bootstrap Helpers
~~~~~~~~~~~~~~~~~

The two remaining submodules are called exclusively from ``NextFrameworkConfig.ready`` and are not part of the project-level public API.
They are documented here for framework contributors and for projects that instrument startup behaviour.

``next.apps.autoreload``.
   Swaps Django's ``StatReloader`` for ``NextStatReloader`` and connects the ``autoreload_started`` signal so the framework's watch specs are registered at dev-server startup.
   Exposes ``install()`` and ``uninstall()``.
   ``uninstall()`` restores the original ``StatReloader`` subclass. Test suites that call ``ready()`` multiple times use it to avoid double-patching.

``next.apps.components``.
   Calls ``components_manager._ensure_backends()`` then invokes ``_ensure_loaded()`` on each backend that defines it.
   For ``FileComponentsBackend`` this discovers components and populates the registry.
   Unless ``NEXT_FRAMEWORK["LAZY_COMPONENT_MODULES"]`` is true, it also imports every ``component.py`` found under configured component roots during startup so decorators run before the first request.
   Exposes a single ``install()`` callable.

See Also
--------

.. seealso::

   :doc:`/content/topics/project-layout` for the application setup.
   :doc:`/content/topics/extending` for the extension surface.
   :doc:`/content/internals/overview` for the full ``ready()`` sequence, including system-check registration and the four installer hooks.
