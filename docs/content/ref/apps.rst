.. _ref-apps:

Apps reference
==============

Module summary
--------------

``next.apps`` contains the Django ``AppConfig`` and the helpers that the framework runs at application startup.

``NextFrameworkConfig.ready()`` first runs ``next.checks.register_all()`` to register the framework system checks.
It then calls five installer hooks in a fixed order.

#. ``autoreload.install()``
#. ``templates.install()``
#. ``staticfiles.install()``
#. ``components.install()``
#. ``autodiscover_forms()``

``autodiscover_forms()`` imports the ``forms`` submodule of every installed app so shared forms register before the first request arrives.
It respects the ``FORM_AUTODISCOVER`` setting and is a no-op when that setting is ``False``.

Public API
----------

.. automodule:: next.apps
   :members:

The installer submodules below are called exclusively from ``NextFrameworkConfig.ready`` and are not part of the project-level public API.
They are documented here for framework contributors and for projects that instrument startup behaviour.

Template tag registration
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.templates
   :members:

Staticfiles integration
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.staticfiles
   :members:

``staticfiles.install()`` calls ``next.static.register_defaults`` to register the built-in ``css``, ``js``, and ``module`` kinds and the ``styles`` and ``scripts`` slots.

Autoreload installer
~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.autoreload
   :members:

``install()`` swaps Django's ``StatReloader`` for ``NextStatReloader`` and connects the ``autoreload_started`` signal so the framework's watch specs are registered at dev-server startup.
``uninstall()`` restores the original ``StatReloader`` subclass.
Test suites that patched the reloader through ``ready()`` call it to put the original class back.
Repeated ``ready()`` calls need no such cleanup because ``install()`` itself is idempotent.

Components installer
~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.components
   :members:

``install()`` loads the component backends and populates their registries.
Unless ``LAZY_COMPONENT_MODULES`` is true it also imports every discovered ``component.py``.
See :doc:`/content/internals/component-pipeline` for the discovery and load sequence.

See also
--------

.. seealso::

   :doc:`/content/topics/project-layout` for the application setup.
   :doc:`/content/topics/extending` for the extension surface.
   :doc:`/content/internals/overview` for the full ``ready()`` sequence, including system-check registration and the installer hooks.
