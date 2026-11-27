.. _ref-apps:

Apps reference
==============

Module summary
--------------

``next.apps`` contains the Django ``AppConfig`` and the helpers that the framework runs at application startup.

Importing ``next.apps`` registers the framework system checks, because the package imports its own ``checks`` and ``config.py`` imports ``next.apps.areas``, whose area manifests import the ``checks`` of every other area.
``NextFrameworkConfig.check_modules`` holds that catalogue, ``CHECK_MODULES`` of ``next.apps.areas``, and ``ready()`` leaves the checks alone.
``NextFrameworkConfig.ready()`` walks ``MANIFESTS``, the ordered manifest of every area, connecting every signal receiver first, then binding the port slots, running the startup steps such as the resolver setting, and registering the page declarations, the lifecycle participants, and the page script providers of each area, and runs the installers last, see :ref:`internals-adding-an-area-manifest`.

#. ``setting_changed`` receivers connected
#. ``settings_reloaded`` receivers connected
#. ``user_logged_in`` receiver ``clear_on_login`` connected
#. ``user_logged_out`` receiver ``clear_on_logout`` connected
#. ``connection_created`` receiver ``cover_new_connection`` connected
#. ``apply_resolver_setting()``
#. ``deferrals_slot.set(inline_sink)``
#. ``action_owners_slot.set(ActionOwnersImpl())``
#. ``form_routes_slot.set(form_action_manager)``
#. ``page_scan_slot.set(PageScanImpl())``
#. ``route_access_slot.set(RouteAccessImpl())``
#. ``router_stamp_slot.set(stamp)``
#. ``zone_marking_slot.set(action_marking)``
#. ``seo_routes_slot.set(SeoRoutesImpl())``
#. ``seo_routes_version.move()``
#. ``static_assets_slot.set(StaticAssetsImpl())``
#. ``autoreload.install()``
#. ``templates.install()``
#. ``staticfiles.install()``
#. ``autodiscover.install()``
#. ``components.install()``

Every framework receiver of ``setting_changed``, ``settings_reloaded``, ``user_logged_in``, ``user_logged_out``, and ``connection_created`` is connected in the connect steps and none at import time, and the one other receiver, the ``autoreload_started`` watcher, is connected by ``autoreload.install()``.
Each signal below lists its receivers in the order they run, and each is connected with ``dispatch_uid`` set to its module and qualified name, so a second ``ready`` call connects nothing twice.

- ``setting_changed`` runs ``reload_on_setting_changed`` of ``next.conf.settings`` first, which moves the ``SETTINGS`` source of ``next.caches`` or, for ``NEXT_FRAMEWORK``, rebuilds the merged mapping, moves ``SETTINGS`` and ``FRAMEWORK`` and sends ``settings_reloaded``, then the receivers that act on Django settings, ``forget_render_pipeline``, ``reload_app_routers``, ``staticfiles.reinstall_finders``, and ``templates.reinstall_builtins``.
- ``settings_reloaded`` runs ``reapply_resolver_setting`` and ``reload_router_settings``, which rebuild the resolver and the routers.
- No receiver drops a memo.
  Each memo attaches to the source it follows, so a move drops every one before either signal reaches its receivers, see :ref:`internals-process-state`.
  ``route_manager.reload`` moves ``ROUTES`` before it sends ``routes_reloaded``, so every memo derived from the old routers goes with them, see :doc:`/content/howto/reload-routes-from-code`.
- ``user_logged_in`` and ``user_logged_out`` run ``clear_on_login`` and ``clear_on_logout`` of ``next.router.navigation``, which mark the request so its response tells the client router to drop what it cached for the previous identity, see :doc:`router`.
- ``connection_created`` runs ``cover_new_connection`` of ``next.transactions.savepoints``, which puts a connection the thread opens while a lazy savepoint watches every connection under that savepoint, as the connections already open are, see :doc:`defer`.

``apply_resolver_setting()`` points the dependency-injection singleton at the class named by ``DEPENDENCY_RESOLVER``, see :doc:`settings`.
It runs ahead of every install because the two discovery steps import user modules, and a ``component.py``, a ``deps.py``, a ``permissions.py``, a ``forms.py``, or a ``signals.py`` that resolves at import time has to see the configured resolver rather than the base one.

``autodiscover.install()`` imports the ``deps``, ``permissions``, ``forms``, ``realtime``, and ``signals`` submodules of every installed app, finishing one module name across all apps before it starts the next.
It runs ahead of ``components.install()``, so a ``component.py`` imported in the last step finds every provider and named dependency registered.
The ``deps``, ``permissions``, ``realtime``, and ``signals`` sweeps always run, the way Django's admin imports every ``admin.py``, and ``FORM_AUTODISCOVER`` turns off the ``forms`` one, see :doc:`settings`.
The ``realtime`` sweep imports the modules that ``REALTIME["HANDLERS"]`` selects.

The ``set`` steps bind every :doc:`next.ports <ports>` slot, ahead of every step that imports user code so that a module touching a framework path at import time never reads an unbound slot.
They also run ahead of the discovery steps so a discovery failure leaves no process behind with a slot still empty.
Each implementation resolves its manager when a method is called rather than when the slot is bound, so the static manager is still built on first use.
The ``seo_routes_version.move()`` step right after the ``seo_routes_slot`` binding moves the counter of ``next.urls.manager`` the lazy urlpatterns key their cache on, so they rebuild their pattern list with the routes of the port just bound.

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
The branch matches a fixed set of literal framework tag names and nothing else, and Django's own block-tag branch stays behind it, so a stock tag, a third-party tag, a variable, and a comment all lex as Django lexes them.
See :doc:`template-tags` for the tags this enables and the exact set.
A Django release that spells the block-tag branch differently raises ``RuntimeError`` out of ``ready``, because a pattern left unwidened would turn every multi-line tag into template text at render time.

``reinstall_builtins`` runs on ``setting_changed``, so an override that hands the engines a new ``TEMPLATES`` value gets the framework builtins installed again and the engines built without them dropped.

Staticfiles integration
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: next.apps.staticfiles
   :members:

``staticfiles.install()`` calls ``next.static.register_defaults`` to register the built-in ``css``, ``js``, and ``module`` kinds and the ``head``, ``styles``, and ``scripts`` slots.
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
