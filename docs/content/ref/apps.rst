.. _ref-apps:

Apps Reference
==============

Module Summary
--------------

``next.apps`` contains the Django ``AppConfig`` and the helpers that the framework runs at application startup.

``NextFrameworkConfig.ready()`` first runs ``next.checks.register_all()`` to register the framework system checks.
It then calls four installer hooks in a fixed order: ``autoreload.install()``, ``templates.install()``, ``staticfiles.install()``, and ``components.install()``.

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

``staticfiles.install()`` calls ``next.static.register_defaults`` to register the built-in ``css``, ``js``, and ``module`` kinds and the ``styles`` and ``scripts`` slots.

Autoreload Installer
~~~~~~~~~~~~~~~~~~~~

The two installer submodules below are called exclusively from ``NextFrameworkConfig.ready`` and are not part of the project-level public API.
They are documented here for framework contributors and for projects that instrument startup behaviour.

.. automodule:: next.apps.autoreload
   :members:

``install()`` swaps Django's ``StatReloader`` for ``NextStatReloader`` and connects the ``autoreload_started`` signal so the framework's watch specs are registered at dev-server startup.
``uninstall()`` restores the original ``StatReloader`` subclass.
Test suites that call ``ready()`` multiple times use it to avoid double-patching.

Components Installer
~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.components
   :members:

``install()`` loads the component backends and populates their registries.
Unless ``LAZY_COMPONENT_MODULES`` is true it also imports every discovered ``component.py``.
See :doc:`/content/internals/component-pipeline` for the discovery and load sequence.

See Also
--------

.. seealso::

   :doc:`/content/topics/project-layout` for the application setup.
   :doc:`/content/topics/extending` for the extension surface.
   :doc:`/content/internals/overview` for the full ``ready()`` sequence, including system-check registration and the four installer hooks.
