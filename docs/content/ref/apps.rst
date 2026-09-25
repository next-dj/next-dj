.. _ref-apps:

Apps reference
==============

Module summary
--------------

``next.apps`` contains the Django ``AppConfig`` and the helpers that the framework runs at application startup.

``NextFrameworkConfig.ready()`` first runs ``next.checks.register_all()`` to register the framework system checks.
It then runs thirteen startup steps in a fixed order.

#. ``router_reloaded.connect()`` for the five cache-forgetting receivers
#. ``apply_resolver_setting()``
#. ``component_tags_slot.set(ComponentTagsImpl())``
#. ``page_scan_slot.set(PageScanImpl())``
#. ``partial_shaper_slot.set(PartialShaperImpl())``
#. ``router_access_slot.set(RouterAccessImpl())``
#. ``seo_routes_slot.set(SeoRoutesImpl())``
#. ``static_assets_slot.set(StaticAssetsImpl())``
#. ``autoreload.install()``
#. ``templates.install()``
#. ``staticfiles.install()``
#. ``components.install()``
#. ``autodiscover_forms()``

Step one connects ``forget_watch_state``, ``forget_page_roots``, ``forget_manager_page_roots``, ``forget_dep_caches``, and ``seo_manager.reset`` to ``router_reloaded``.
A reload that replaces the routers from code changes what they report without touching settings, so the watch state, the page roots, the static manager page roots, the dependency caches, and the discovered sitemap and robots sources all go with the generation that produced them.
See :doc:`/content/howto/reload-routes-from-code` for the reload itself.

``apply_resolver_setting()`` points the dependency-injection singleton at the class named by ``DEPENDENCY_RESOLVER``, see :doc:`settings`.
It runs ahead of every install because the two discovery steps import user modules, and a ``component.py`` or a ``forms.py`` that resolves at import time has to see the configured resolver rather than the base one.

``autodiscover_forms()`` imports the ``forms`` submodule of every installed app so shared forms register before the first request arrives.
It respects the ``FORM_AUTODISCOVER`` setting and is a no-op when that setting is ``False``.

Steps three to eight bind the six :doc:`next.ports <ports>` slots, ahead of every step that imports user code so that a module touching a framework path at import time never reads an unbound slot.
They also run ahead of the discovery steps so a discovery failure leaves no process behind with a slot still empty.
Each implementation resolves its manager when a method is called rather than when the slot is bound, so the static manager is still built on first use.

Public API
----------

.. automodule:: next.apps
   :members:

The installer submodules below run from ``NextFrameworkConfig.ready``, and the template and staticfiles installers run again from their own ``setting_changed`` receivers, so an override that replaces ``TEMPLATES`` or ``STATICFILES_FINDERS`` gets the framework wiring back.
None of them is part of the project-level public API.
They are documented here for framework contributors and for projects that instrument startup behaviour.

Template tag registration
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.templates
   :members:

``templates.install()`` also prepends a line-spanning branch to Django's template lexing pattern, so a component tag can carry its arguments over more than one line.
The branch matches fourteen literal framework tag names and nothing else, and Django's own block-tag branch stays behind it, so a stock tag, a third-party tag, a variable, and a comment all lex as Django lexes them.
See :doc:`template-tags` for the tags this enables and the exact set.
A Django release that spells the block-tag branch differently raises ``RuntimeError`` out of ``ready``, because a pattern left unwidened would turn every multi-line tag into template text at render time.

The module also connects a ``setting_changed`` receiver, so an override that hands the engines a new ``TEMPLATES`` value gets the framework builtins installed again and the engines built without them dropped.

Staticfiles integration
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.staticfiles
   :members:

``staticfiles.install()`` calls ``next.static.register_defaults`` to register the built-in ``css``, ``js``, and ``module`` kinds and the ``styles`` and ``scripts`` slots.
The installer appends ``NextStaticFilesFinder`` and puts ``NextAppDirectoriesFinder`` in place of the stock ``AppDirectoriesFinder``, which keeps the framework package itself out of ``STATIC_ROOT``.
A list that names the stock path beside the framework one collapses to a single entry, so a project that already lists ``NextAppDirectoriesFinder`` gains no duplicate scan.
The module also connects a ``setting_changed`` receiver, so an override that replaces ``STATICFILES_FINDERS`` gets both back as soon as the override lands.

Autoreload installer
~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.autoreload
   :members:

``install()`` swaps Django's ``StatReloader`` for ``NextStatReloader`` and connects the ``autoreload_started`` signal, and ``uninstall()`` puts the original class back.
See :doc:`/content/internals/autoreload` for the idempotence contract and the test cleanup.

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
   :doc:`/content/internals/overview` for the subsystem map the startup steps wire together.
