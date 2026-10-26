.. _ref-apps:

Apps reference
==============

Module summary
--------------

``next.apps`` contains the Django ``AppConfig`` and the helpers that the framework runs at application startup.

``NextFrameworkConfig.ready()`` first runs ``next.checks.register_all()`` to register the framework system checks.
It then runs nineteen startup steps in a fixed order.

#. ``setting_changed`` receivers connected
#. ``settings_reloaded`` receivers connected
#. ``router_reloaded`` receivers connected
#. ``apply_resolver_setting()``
#. ``action_owners_slot.set(ActionOwnersImpl())``
#. ``form_routes_slot.set(form_action_manager)``
#. ``page_scan_slot.set(PageScanImpl())``
#. ``partial_shaper_slot.set(PartialShaperImpl())``
#. ``realtime_negotiation_slot.set(RealtimeNegotiationImpl())``
#. ``router_access_slot.set(RouterAccessImpl())``
#. ``page_scripts_slot.set(PageScriptsImpl())``
#. ``seo_routes_slot.set(SeoRoutesImpl())``
#. ``seo_routes_version.move()``
#. ``static_assets_slot.set(StaticAssetsImpl())``
#. ``autoreload.install()``
#. ``templates.install()``
#. ``staticfiles.install()``
#. ``autodiscover.install()``
#. ``components.install()``

Every signal receiver of the framework is connected in the first three steps and nowhere else, none at import time.
Each table lists its receivers in the order they run, and each is connected with ``dispatch_uid`` set to its module and qualified name, so a second ``ready`` call connects nothing twice.

- ``setting_changed`` runs ``reload_on_setting_changed`` of ``next.conf.settings`` first, which rebuilds the merged ``NEXT_FRAMEWORK`` and sends ``settings_reloaded``, then the receivers that read Django settings directly, among them ``forget_app_page_roots``, ``reload_app_routers``, ``staticfiles.reinstall_finders``, and ``templates.reinstall_builtins``.
- ``settings_reloaded`` runs ``reset_failure_logs`` first, so every failure logs again under the new configuration, then every receiver that forgets a memo derived from the settings, among them ``reapply_resolver_setting``, ``forget_site_config``, ``forget_metadata_renderer``, ``forget_serializer``, ``forget_static_manager``, ``forget_seo_sources``, ``forget_scripts``, and ``forget_consent_settings``.
- ``router_reloaded`` runs ``forget_watch_state``, ``forget_page_roots``, ``forget_manager_page_roots``, ``forget_dep_caches``, ``forget_object_rows``, ``forget_seo_sources``, ``forget_translated_urls``, and ``forget_scripts``.
  A reload that replaces the routers from code changes what they report without touching settings, so every memo derived from the old routers goes with them, see :doc:`/content/howto/reload-routes-from-code`.

``apply_resolver_setting()`` points the dependency-injection singleton at the class named by ``DEPENDENCY_RESOLVER``, see :doc:`settings`.
It runs ahead of every install because the two discovery steps import user modules, and a ``component.py``, a ``deps.py``, a ``permissions.py``, a ``forms.py``, or a ``signals.py`` that resolves at import time has to see the configured resolver rather than the base one.

``autodiscover.install()`` imports the ``deps``, ``permissions``, ``forms``, and ``signals`` submodules of every installed app, finishing one module name across all apps before it starts the next.
It runs ahead of ``components.install()``, so a ``component.py`` imported in the last step finds every provider and named dependency registered.
The ``deps``, ``permissions``, and ``signals`` sweeps always run, the way Django's admin imports every ``admin.py``, and ``FORM_AUTODISCOVER`` turns off the ``forms`` one, see :doc:`settings`.

Steps five to fourteen bind the nine :doc:`next.ports <ports>` slots, ahead of every step that imports user code so that a module touching a framework path at import time never reads an unbound slot.
They also run ahead of the discovery steps so a discovery failure leaves no process behind with a slot still empty.
Each implementation resolves its manager when a method is called rather than when the slot is bound, so the static manager is still built on first use.
Step thirteen moves ``seo_routes_version`` of ``next.urls.manager``, the counter the lazy urlpatterns key their cache on, so they rebuild their pattern list with the routes of the port step twelve bound.

Public API
----------

.. automodule:: next.apps
   :members:

The installer submodules below run from ``NextFrameworkConfig.ready``, and the template and staticfiles installers run again from ``reinstall_builtins`` and ``reinstall_finders`` on ``setting_changed``, so an override that replaces ``TEMPLATES`` or ``STATICFILES_FINDERS`` gets the framework wiring back.
None of them is part of the project-level public API.
They are documented here for framework contributors and for projects that instrument startup behaviour.

Template tag registration
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.templates
   :members:

``templates.install()`` also prepends a line-spanning branch to Django's template lexing pattern, so a component tag can carry its arguments over more than one line.
The branch matches nineteen literal framework tag names and nothing else, and Django's own block-tag branch stays behind it, so a stock tag, a third-party tag, a variable, and a comment all lex as Django lexes them.
See :doc:`template-tags` for the tags this enables and the exact set.
A Django release that spells the block-tag branch differently raises ``RuntimeError`` out of ``ready``, because a pattern left unwidened would turn every multi-line tag into template text at render time.

``reinstall_builtins`` runs on ``setting_changed``, so an override that hands the engines a new ``TEMPLATES`` value gets the framework builtins installed again and the engines built without them dropped.

Staticfiles integration
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.staticfiles
   :members:

``staticfiles.install()`` calls ``next.static.register_defaults`` to register the built-in ``css``, ``js``, and ``module`` kinds and the ``styles`` and ``scripts`` slots.
The installer appends ``NextStaticFilesFinder`` and puts ``NextAppDirectoriesFinder`` in place of the stock ``AppDirectoriesFinder``, which keeps the framework package itself out of ``STATIC_ROOT``.
A list that names the stock path beside the framework one collapses to a single entry, so a project that already lists ``NextAppDirectoriesFinder`` gains no duplicate scan.
``reinstall_finders`` runs on ``setting_changed``, so an override that replaces ``STATICFILES_FINDERS`` gets both back as soon as the override lands.

Autoreload installer
~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.autoreload
   :members:

``install()`` swaps Django's ``StatReloader`` for ``NextStatReloader`` and connects the ``autoreload_started`` signal, and ``uninstall()`` puts the original class back.
See :doc:`/content/internals/autoreload` for the idempotence contract and the test cleanup.

Autodiscover installer
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.autodiscover
   :members:

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
