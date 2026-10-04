.. _ref-system-checks:

System checks
=============

Module summary
--------------

next.dj contributes Django system checks for every subsystem.
Run them through ``uv run python manage.py check`` and the framework reports configuration mistakes with a code and a hint.
Nine of them are deployment checks and answer only to ``uv run python manage.py check --deploy``, see `Check registration`_ for which nine and why.

Check registration
------------------

``next.checks.register_all`` runs during ``AppConfig.ready``.
It imports each subsystem ``checks`` module so the ``@register`` side effects take effect.
The imported modules are ``next.apps.checks``, ``next.components.checks``, ``next.conf.checks``, ``next.consent.checks``, ``next.forms.checks``, ``next.pages.checks``, ``next.partial.checks``, ``next.scripts.checks``, ``next.seo.checks``, ``next.site.checks``, ``next.static.checks``, and ``next.urls.checks``.

Each of these modules registers checks.
``next.pages.checks``, ``next.forms.checks``, ``next.partial.checks``, and ``next.seo.checks`` are packages rather than single modules, and importing the package imports every submodule that carries a check.
``next.pages.checks.metadata`` is a package inside the pages one and splits the same way.
The address a project imports stays the same either way, and the tables under `Check code reference`_ name the submodule behind each code.

Nine checks are deployment checks and carry ``deploy=True``, so ``manage.py check`` alone never runs them and ``manage.py check --deploy`` does.
Four of them are ``check_page_module_imports`` (``next.E017``), ``check_component_module_imports`` (``next.E084``), ``check_composed_templates_compile`` (``next.E072``), and ``check_asset_version_moves_between_deploys`` (``next.W083``).
The first three import or compile every user module of a tree, which costs a full walk that a routine ``manage.py`` command should not pay.
The fourth describes a configuration every development checkout has, so reporting it outside a deployment audit would warn every project about nothing.
``check_runtime_bundles_deployed`` (``next.W090``) asks the static files a deployment serves for the client runtime, which a source checkout builds only on demand.
The other four read the settings a deployment runs on, ``check_site_url_for_deploy`` (``next.W110``, ``next.W122``) and ``check_seo_sources_on_closed_site`` (``next.W111``) with the ``seo`` tag, ``check_script_deploy`` (``next.W118``), and ``check_consent_cookie_secure`` (``next.W119``).

Every next.dj check carries the ``next`` tag.
That tag is the importable string constant ``next.checks.NEXT``, so a project check joins the framework ones by decorating itself with ``@register(NEXT)`` rather than by repeating the literal.
Run ``uv run python manage.py check --tag next`` to execute only the framework checks and skip the built-in Django and third-party ones.
Checks that also concern templates or URL patterns keep their :doc:`Django tags <django:ref/checks>` (``templates``, ``urls``) alongside ``next``, so filtering by those tags still reaches them.
A tagged run reports what a full run reports, and ``--tag next --deploy`` adds the nine deployment checks to it.
The ``seo`` tag, the importable constant ``next.checks.SEO``, marks every ``next.seo`` check, every page metadata check, and the site checks.
``manage.py check --tag seo`` runs the metadata, crawler-document, and site checks alone, and ``manage.py check --deploy --tag seo`` adds the two deployment checks that carry the tag.
Every check that reads registrations discovers the files declaring them itself, rather than relying on a URL check having expanded the router first.

``next.checks.reset_check_caches`` drops every per-run check cache so the next run rebuilds from the current sources.
The cached state covers the router manager with the ``page.py`` pass memoised on it, the components manager, the collected URL patterns, the page module memo, the context and metadata registries with the settings tier, and the seo sources with their items registry.
It also covers the run memos of ``next.checks.common``, which hold the routed pages, their metadata folds, the composed page templates, and the discovered SEO roots so that the checks of one run share a single pass over the tree.
``next.pages.manager.reset_page_registrations`` and ``next.seo.manager.reset_seo_sources`` are the two area resets it calls.
Most of these caches also clear on ``settings_reloaded``, which a ``NEXT_FRAMEWORK`` change through ``override_settings`` triggers.
Tests and scripts that invoke checks directly and mutate the page or component tree in place call ``reset_check_caches`` explicitly, since the caches otherwise freeze the scanned state for the lifetime of the process.

Silencing a check
~~~~~~~~~~~~~~~~~

Django's ``SILENCED_SYSTEM_CHECKS`` setting takes a list of check ids and drops those messages from every run, framework ids included.
An id is the exact string the message carries, ``next.W059`` rather than a tag or a module path, so one entry silences one condition and leaves every other framework check in place.
The :doc:`Django settings reference <django:ref/settings>` documents the setting itself, and :doc:`django:ref/checks` covers the rest of the check framework.

Silencing answers a deliberate shape the check cannot recognise as intended, and it is the wrong answer to a defect the check names correctly.
The :repo:`audit-forms <tree/main/examples/audit-forms>` example triggers ``next.W059``, which reports that two static wizard steps declare the same field name and that ``get_all_cleaned_data()`` keeps only the last value.
That wizard repeats one acknowledgement field across its three steps on purpose and reads the answer per step rather than out of the merged mapping, so the collapse the warning describes costs the project nothing and the id sits in ``SILENCED_SYSTEM_CHECKS`` beside a comment naming the reason.

A silenced check stays visible in the run.
``manage.py check`` counts the messages it dropped and closes with that number, so the setting hides the text of a message and never the fact that the project runs against the advice of a check.

.. warning::

   Silencing ``next.E077`` hides the one condition that means the whole ``NEXT_FRAMEWORK`` setting is ignored, so every value the project set in it is lost and the process runs on the framework defaults.

Shared helpers
~~~~~~~~~~~~~~

``next.checks.common`` holds helpers reused across subsystem check modules.
It is imported indirectly by those modules rather than by ``register_all``, and the router manager and the page-tree walk it passes on live in ``next.discovery``, outside this package, because production code reads them too.
The unknown-key probe behind ``next.E035``, ``errors_for_unknown_keys``, lives in ``next.conf.checks``, the lowest layer every area's checks reach, and ``next.checks.common`` passes it on.
``raw_scope(name)`` answers a ``NEXT_FRAMEWORK`` scope as written, ``None`` where ``next.E076`` reports its shape, and ``takes_request(func)`` whether a callable setting can take the request alone, read off its signature without a call.
``RunMemo`` holds one value a check run builds once, kept while its key is the same object, and ``forget_run_memos`` drops every such value, which ``reset_check_caches`` calls.

.. automodule:: next.checks.common
   :members:

Subsystem checks
----------------

Pages
~~~~~

The package splits by subject, one submodule per group of codes.
``contexts`` covers the ``@context`` callables of a routed ``page.py``, ``layouts`` the page-body placeholder of a ``layout.djx``, ``loaders`` the ``TEMPLATE_LOADERS`` entries, ``metadata`` the ``METADATA`` settings scope and the metadata of every routed ``page.py``, split further under `Metadata and responses`_ below, ``modules`` what a ``page.py`` declares, ``processors`` the context processors a render runs, ``structure`` the shape of a tree on disk, and ``zones`` a ``@context`` reading a key another callable bound to a zone.
``composed`` carries no check and holds the one walk of the composed page templates that ``next.W085`` and the partial checks share, a ``RunMemo`` that ``forget_run_memos`` drops.

.. automodule:: next.pages.checks
   :members:

Metadata and responses
~~~~~~~~~~~~~~~~~~~~~~~

The ``metadata`` package of ``next.pages.checks`` reports a declaration the framework cannot render as intended, on every ``manage.py check``.
``scope`` owns the ``METADATA`` settings scope, ``titles`` parses every title template under every language, ``shape`` what each routed ``page.py`` declares and folds to, ``head`` the links, icons, names, and viewport, ``ld`` the JSON-LD graph, and ``templates`` whether a composed page renders ``{% metadata %}``.
``pages`` and ``links`` carry no check and hold the page pass and the URL predicates the checking submodules share.
Every check reads the static fold, the settings tier, and the ``metadata`` dicts of the chain, so a ``@page.metadata`` callable is validated for its shape and never called.

``next.pages.checks.responses`` reads the ``cache`` and ``headers`` of every routed ``page.py``, the ``CSRF_DELIVERY`` setting, and the composed template of every page a shared cache may hold.
The shared-page warnings walk the composed templates through the ``composed`` memo the partial checks share, and the two about the forms of such a page live in ``next.forms.checks.csrf``.
See :doc:`/content/howto/cache-pages-on-a-cdn` for shared pages from the project side.

Sitemap and robots
~~~~~~~~~~~~~~~~~~

``next.seo.checks`` reads the ``sitemap.py``, ``robots.py``, and ``robots.txt`` at the top of every page root, loaded through the same discovery the routes run, and the ``SEO`` settings scope.
The package splits into ``sources`` for the files themselves and a site closed to search that still publishes them, ``sitemaps`` for what a ``sitemap.py`` lists, ``robots`` for the robots sources, ``routes`` for the addresses the routes answer, and ``backends`` for ``SEO``.
``roots`` carries no check and holds ``loaded_seo_roots``, which answers ``seo_manager.roots()``, the discovery the routes already hold, so a check run executes no ``sitemap.py`` or ``robots.py`` again and leaves the items registry as the runtime filled it.
A router manager that fails to start is ``next.E007``, reported once, and every SEO check then answers nothing.
Every check carries the ``seo`` tag beside ``next``, and needs neither a request nor a database.
No check calls a callable ``SITE["INDEXABLE"]`` or ``SITE["URL"]``, which read as open and are judged by their signature alone, while a ``SITEMAP_BACKENDS`` backend is asked for ``sections(None)``.

The checks call the helpers the routes call, ``SitemapOptions``, ``listed_trails``, and ``is_excluded`` of ``next.seo.sitemaps`` and ``declared_rules`` and ``robots_candidates`` of ``next.seo.robots``, so a check reports what the runtime serves.
See :doc:`seo` for the check callables.

.. automodule:: next.seo.checks
   :members:

Site
~~~~

``next.site.checks`` validates the ``SITE`` scope and warns at deploy about a missing origin, and louder about one ``ALLOWED_HOSTS = ["*"]`` hands to any client.

.. automodule:: next.site.checks
   :members:
   :no-index:

Scripts and consent
~~~~~~~~~~~~~~~~~~~

``next.scripts.checks`` reads every ``scripts.py``, the consent categories, and the composed pages that render ``{% #consented %}``, and ``next.consent.checks`` the rest of the ``CONSENT`` scope.
``next.static.checks`` owns ``next.W117`` for the tag templates the nonce reaches and ``next.W120`` for the shared pages a nonce makes private, since the nonce is a static option.
It also owns ``next.E130`` for the injection policy, ``next.E139`` for a tag template ``.format`` cannot fill, and ``next.W090`` for a runtime bundle the storage cannot serve.

.. automodule:: next.scripts.checks
   :members:
   :no-index:

.. automodule:: next.consent.checks
   :members:
   :no-index:

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

The package splits into ``actions`` for the registered form classes and ``@action`` handlers, ``config`` for the ``NEXT_FRAMEWORK`` keys the subsystem reads, ``csrf`` for how a form receives its token on a page a shared cache may hold, ``widgets`` for the ``ComponentWidget`` a field carries, and ``wizards`` for the ``FormWizard`` subclasses.
``sources`` carries no check and holds the page-tree pass every reader shares, because a page-scoped registration exists only once its ``page.py`` has run.

.. automodule:: next.forms.checks
   :members:

Static
~~~~~~

.. automodule:: next.static.checks
   :members:

Partial rendering
~~~~~~~~~~~~~~~~~

The package splits into ``backends`` for the ``PARTIAL_BACKENDS`` entries, ``forms`` for where a form action meets partial rendering, ``ops`` for the custom patch verbs, ``templates`` for the composed-template compile, and ``zones`` for the ``{% zone %}`` tags of a composed page.
``codes`` holds every identifier the package emits, so a submodule names a code without importing a sibling, ``nodes`` holds the node walk the zone and form checks share, and both read the one-compile-per-page walk of ``next.pages.checks.composed``.

.. automodule:: next.partial.checks
   :members:
   :exclude-members: E_BACKENDS_NOT_A_LIST, E_BACKEND_WITHOUT_PATH, E_COMPOSED_TEMPLATE_SYNTAX, E_CONTEXT_ZONE_UNKNOWN, E_DUPLICATE_ZONE, E_LAZY_WITHOUT_PLACEHOLDER, E_NON_ASCII_ZONE, E_OP_BAD_NAME, E_ZONE_IN_COMPONENT, E_ZONE_IN_FOR, E_ZONE_IN_IF, W_FORM_BACKEND_NOT_AWARE, W_FORM_IN_FOR_NO_KEY, W_MANIFEST_VERSION_NO_STORAGE, W_TOO_MANY_BACKENDS, W_WITH_OVER_ZONE

Apps
~~~~

.. automodule:: next.apps.checks
   :members:

Configuration
~~~~~~~~~~~~~

.. automodule:: next.conf.checks
   :members:

Dependency injection
~~~~~~~~~~~~~~~~~~~~

The layer contributes no check, and there is no ``next.ENNN`` code for a missing provider, a bad marker graph, or an unregistered ``Depends`` name.

.. note::

   Expect this class of misconfiguration at **runtime**.
   Unresolved parameters become ``None``, cycles raise ``DependencyCycleError``, and a ``Depends`` name nothing registered raises ``UnknownDependencyError`` with a ``Did you mean`` hint.
   The exception reaches every injection site, which a check walking the page tree cannot.
   Troubleshooting lives in :doc:`/content/topics/dependency-injection` and :doc:`/content/faq/troubleshooting`.

Check code reference
--------------------

The codes follow the :doc:`Django convention <django:ref/checks>` ``next.X<NNN>`` where ``X`` is ``E`` for errors and ``W`` for warnings.
One code stands for one condition, so silencing it through ``SILENCED_SYSTEM_CHECKS`` never takes an unrelated failure down with it.
The ``Emitted by`` column names the module that builds the message, which for the three check packages is the submodule rather than the package.

A code that a release dropped is retired and never reused, so a silenced code keeps naming the condition it named.
The retired errors are ``next.E001``, ``next.E066``, ``next.E091``, and ``next.E109``.
The retired warnings are ``next.W003`` through ``next.W029``, ``next.W032`` through ``next.W041``, ``next.W044``, ``next.W045``, ``next.W047`` through ``next.W053``, ``next.W064`` through ``next.W066``, and ``next.W073``.

Errors
~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 12 58 30

   * - Code
     - Condition
     - Emitted by
   * - ``next.E002``
     - A ``PAGE_BACKENDS`` entry is not a dict.
       The ``COMPONENT_BACKENDS`` counterpart is ``next.E079``.
     - ``next.urls.checks``
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
     - The file router ``DIRS`` value is not a list.
       The ``OPTIONS`` shapes carry ``next.E094`` through ``next.E097``, so one malformed key costs one error.
     - ``next.urls.checks``
   * - ``next.E007``
     - The routers ``PAGE_BACKENDS`` lists fail to initialize.
       It is reported once per check run, by ``check_router_manager`` in ``next.urls.checks`` under every tag the router checks carry, and each check that walks the routers skips without a message of its own.
     - ``next.discovery``
   * - ``next.E008``
     - A ``[param]`` directory uses invalid parameter syntax, names a converter Django has no registration for, or names a parameter that is no Python identifier.
     - ``next.pages.checks.structure``
   * - ``next.E009``
     - A ``[[args]]`` directory names no route, because the brackets hold nothing or hold a name that is no Python identifier once ``-`` is read as ``_``.
     - ``next.pages.checks.structure``
   * - ``next.E010``
     - A parameter directory is missing its ``page.py`` file.
     - ``next.pages.checks.structure``
   * - ``next.E011``
     - An error was raised while checking page functions.
       What failed is the page-tree walk of one router, so the body-source checks ``next.E012``, ``next.E013``, and ``next.W043`` report nothing for the pages beneath it and a ``page.py`` with neither a body source nor a conflict passes unexamined.
     - ``next.pages.checks.modules``
   * - ``next.E012``
     - A ``page.py`` has no body source, meaning no ``render`` function, no ``template`` attribute, no loader match, and no sibling ``layout.djx``.
     - ``next.pages.checks.modules``
   * - ``next.E013``
     - A page ``render`` attribute is not callable.
     - ``next.pages.checks.modules``
   * - ``next.E014``
     - An error was raised while checking URL conflicts.
       The comparison that raised is the one reporting ``next.E015``, so no duplicate route pair is named in that run however many the tree holds.
     - ``next.urls.checks``
   * - ``next.E015``
     - The same URL pattern is defined in more than one location.
     - ``next.urls.checks``
   * - ``next.E016``
     - An error was raised while collecting patterns from a router.
       That router contributes no route to the shared collection, so ``next.E015`` and ``next.E039`` both compare a set it is missing from and a duplicate path or reverse name it would have caused goes unreported.
     - ``next.urls.checks``
   * - ``next.E017``
     - A ``page.py`` raises while importing.
       The message names the recorded exception type and text, so an ``ImportError`` raised by the module body reads as such instead of masking as a missing body source.
       The body-source checks ``next.E012``, ``next.E013``, and ``next.W043`` stay silent for that file, so the import failure surfaces once.
       Importing every routed module costs a full tree walk, so the check carries ``deploy=True`` and the code fires under ``manage.py check --deploy`` alone.
     - ``next.pages.checks.modules``
   * - ``next.E018``
     - A ``page.py`` registers more than one keyless ``@context`` callable, and only the last one runs.
     - ``next.pages.checks.contexts``
   * - ``next.E019``
     - A ``TEMPLATES`` entry leaves ``django.template.context_processors.request`` out of its ``OPTIONS.context_processors``, so ``request`` is missing from the template context, which ``{% form %}`` and CSRF both need.
     - ``next.pages.checks.processors``
   * - ``next.E020``
     - A component name is registered more than once under one route scope, so nothing tells the two apart.
     - ``next.components.checks``
   * - ``next.E021``
     - A ``component.py`` reaches for the page ``context`` decorator instead of the component one, under any spelling: ``next.pages``, the ``next`` package root, or ``next.page.context``.
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
     - A ``PAGES_DIR`` value is not a string.
       The ``COMPONENTS_DIR`` counterpart is ``next.E080``.
     - ``next.urls.checks``
   * - ``next.E028``
     - A route repeats one or more bracket parameter names, all listed in the error.
     - ``next.urls.checks``
   * - ``next.E029``
     - A keyless ``@context`` callable is not annotated as returning a dict.
       The check reads the context registry, so it catches ``@context``, ``@page.context``, an aliased import, and ``async def`` alike.
     - ``next.pages.checks.contexts``
   * - ``next.E030``
     - A router cannot report its page trees, because ``page_roots`` raised or answered something other than ``PageRoot`` entries.
       Such a router is named here once, with the exception text, and reports no tree to any other check.
       The run continues, so a third-party backend that cannot reach its source costs its own trees instead of ending ``manage.py check`` with a traceback.
       This is also the one place in the run that logs that traceback, so the message is not buried under a copy per check that asked.
       A tree that is reported and then fails the walk is ``next.E088`` instead.
     - ``next.pages.checks.structure``
   * - ``next.E031``
     - A component backend entry is missing ``BACKEND``, ``DIRS``, or ``COMPONENTS_DIR``.
     - ``next.components.checks``
   * - ``next.E032``
     - A component backend ``BACKEND`` path cannot be imported.
       A path that imports into something outside the ``ComponentsBackend`` family is ``next.E055``.
     - ``next.components.checks``
   * - ``next.E033``
     - ``COMPONENT_BACKENDS`` is empty.
     - ``next.components.checks``
   * - ``next.E034``
     - A component name sits at the root scope of two roots the same template resolves against, with neither taking precedence.
     - ``next.components.checks``
   * - ``next.E035``
     - A configuration dict has unknown keys.
     - ``next.conf.checks``
   * - ``next.E036``
     - A static backend dotted path fails to import.
     - ``next.static.checks``
   * - ``next.E037``
     - A ``STATIC_BACKENDS`` entry is not a dict.
       A class outside the ``StaticBackend`` family is ``next.E093``.
     - ``next.static.checks``
   * - ``next.E038``
     - ``STATIC_BACKENDS`` contains a duplicate ``BACKEND`` entry.
     - ``next.static.checks``
   * - ``next.E039``
     - Two distinct routes collapse to the same reverse URL name after separator normalisation.
     - ``next.urls.checks``
   * - ``next.E040``
     - A context processor named by a ``PAGE_BACKENDS`` entry does not accept a ``request`` parameter.
     - ``next.pages.checks.processors``
   * - ``next.E041``
     - A form action name is registered by more than one handler.
     - ``next.forms.checks.actions``
   * - ``next.E042``
     - A ``TEMPLATE_LOADERS`` entry is not a dotted-path string.
     - ``next.pages.checks.loaders``
   * - ``next.E043``
     - A ``TEMPLATE_LOADERS`` entry cannot be imported.
       An entry that imports into something outside the ``TemplateLoader`` family is ``next.E089``.
     - ``next.pages.checks.loaders``
   * - ``next.E044``
     - ``FORM_ACTION_BACKENDS`` is not a list, so no entry can be read.
       The per-entry shapes carry ``next.E058``, ``next.E059``, ``next.E068``, and ``next.E045``.
     - ``next.forms.checks.config``
   * - ``next.E045``
     - A form action backend class does not subclass ``FormActionBackend``.
     - ``next.forms.checks.config``
   * - ``next.E046``
     - One shared action name is declared in two different modules, so bare-name lookups resolve to whichever module imported first.
       Rename one class or set ``Meta.scope``.
     - ``next.forms.checks.actions``
   * - ``next.E047``
     - A form class ``Meta.scope`` is set to a value other than ``"page"`` or ``"shared"``.
       The ``@action`` ``scope`` keyword carries ``next.E085``.
     - ``next.forms.checks.actions``
   * - ``next.E048``
     - ``Meta.instance_from_url`` references a field name that does not exist on the model.
     - ``next.forms.checks.actions``
   * - ``next.E049``
     - ``Meta.instance_from_url`` is set on a class that is not a ``ModelForm`` subclass.
     - ``next.forms.checks.actions``
   * - ``next.E050``
     - A ``FormWizard`` declares no ``Meta.steps`` or an empty list.
     - ``next.forms.checks.wizards``
   * - ``next.E051``
     - ``FORM_WIZARD_BACKEND`` is not a dict, so the key names no backend at all.
       The shape of the entry itself carries ``next.E069`` through ``next.E071``.
     - ``next.forms.checks.config``
   * - ``next.E052``
     - ``FORM_ANCHOR_FILES`` is neither ``None`` nor a list.
       A tuple or a set is refused here rather than dropped silently by the settings merge, and a list holding a non-string is ``next.E086``.
     - ``next.forms.checks.config``
   * - ``next.E053``
     - ``@action`` was applied to a class instead of a function.
     - ``next.forms.checks.actions``
   * - ``next.E054``
     - A page-scoped ``FormWizard`` is declared on a page whose route lacks the ``[url_param]`` segment, so the wizard can never advance past its first step.
     - ``next.forms.checks.wizards``
   * - ``next.E055``
     - A component backend ``BACKEND`` path imports into something that is no ``ComponentsBackend`` subclass.
     - ``next.components.checks``
   * - ``next.E056``
     - A component backend ``BACKEND`` value is not a string.
     - ``next.components.checks``
   * - ``next.E057``
     - A component backend ``DIRS`` value is not a list.
     - ``next.components.checks``
   * - ``next.E058``
     - A ``FORM_ACTION_BACKENDS`` entry is not a dict.
     - ``next.forms.checks.config``
   * - ``next.E059``
     - A ``FORM_ACTION_BACKENDS`` entry names no ``BACKEND``, or names one that is not a string.
     - ``next.forms.checks.config``
   * - ``next.E060``
     - A zone name is declared more than once in a page's composed template, the layout chain plus the page body.
     - ``next.partial.checks.zones``
   * - ``next.E061``
     - A zone name is not an ASCII slug, so it cannot travel in the latin-1 ``X-Next-Zone`` header.
     - ``next.partial.checks.zones``
   * - ``next.E062``
     - A ``{% zone %}`` sits inside a ``{% for %}`` loop, which a standalone zone render cannot reproduce.
     - ``next.partial.checks.zones``
   * - ``next.E063``
     - A ``{% zone %}`` sits inside an ``{% if %}`` block, whose condition a standalone zone render cannot evaluate.
     - ``next.partial.checks.zones``
   * - ``next.E064``
     - A ``lazy=`` zone declares no ``{% placeholder %}`` branch to show until its body arrives.
     - ``next.partial.checks.zones``
   * - ``next.E065``
     - A component template declares a ``{% zone %}`` tag, which belongs to a page or layout.
     - ``next.partial.checks.zones``
   * - ``next.E067``
     - ``NEXT_FRAMEWORK['PARTIAL_BACKENDS']`` is not a list, so the value is ignored and the default protocol backend loads in place of the configured one.
       The generic shape probe behind ``next.E076`` leaves this key out, so the drop is reported once.
     - ``next.partial.checks.backends``
   * - ``next.E068``
     - A ``FORM_ACTION_BACKENDS`` entry names a ``BACKEND`` path that cannot be imported.
     - ``next.forms.checks.config``
   * - ``next.E069``
     - ``FORM_WIZARD_BACKEND`` names no ``BACKEND``, or names one that is not a string.
     - ``next.forms.checks.config``
   * - ``next.E070``
     - The ``FORM_WIZARD_BACKEND`` ``BACKEND`` path cannot be imported.
     - ``next.forms.checks.config``
   * - ``next.E071``
     - The ``FORM_WIZARD_BACKEND`` ``BACKEND`` path imports into something that is no ``FormWizardBackend`` subclass.
     - ``next.forms.checks.config``
   * - ``next.E072``
     - A composed page template does not compile, so the syntax error would otherwise surface only as a 500 on the first request to the page.
       Compiling every composed template of every tree costs a full walk, so the check carries ``deploy=True`` and the code fires under ``manage.py check --deploy`` alone.
     - ``next.partial.checks.templates``
   * - ``next.E073``
     - A ``PARTIAL_BACKENDS`` entry has no ``BACKEND`` key, so the entry would fall back to the default protocol backend and the intended wire format would never load.
     - ``next.partial.checks.backends``
   * - ``next.E074``
     - A ``@context`` registration binds to a file no page render collects.
       A registration keys on the file declaring the callable, so decorating an imported helper binds it to the helper's module, and decorating a callable imported from a sibling ``page.py`` binds it to that other page.
       Declare the callable in the ``page.py`` that needs it and let it call the shared helper.
     - ``next.pages.checks.contexts``
   * - ``next.E075``
     - A ``@component.context`` registration binds to a file no component render collects.
       The rule and the fix match ``next.E074``.
       The check covers the configured component roots and the ``_components`` folders the page trees carry, which it discovers through the same walk the router uses.
     - ``next.components.checks``
   * - ``next.E076``
     - A ``NEXT_FRAMEWORK`` value has a type the settings merge silently drops in favour of the framework default.
       The check covers ``PAGE_BACKENDS``, ``COMPONENT_BACKENDS``, ``STATIC_BACKENDS``, and ``TEMPLATE_LOADERS`` as lists.
       It also covers ``COMPONENT_TEMPLATE_LOADER``, ``DEPENDENCY_RESOLVER``, ``URL_NAME_TEMPLATE``, and ``URL_RESOLVER`` as strings, ``STATIC_VERSION`` as a string or ``None``, and ``NEXT_JS_OPTIONS`` as a dict.
       ``PARTIAL_BACKENDS``, ``FORM_ACTION_BACKENDS``, ``FORM_ANCHOR_FILES``, ``FORM_WIZARD_BACKEND``, and ``JS_CONTEXT_SERIALIZER`` carry their own per-key checks, ``next.E067``, ``next.E044``, ``next.E052``, ``next.E051``, and ``next.W042``, so this probe leaves them out.
     - ``next.conf.checks``
   * - ``next.E077``
     - ``NEXT_FRAMEWORK`` is not a dict, so the settings layer ignores it entirely and the project runs on the framework defaults.
       It carries its own code rather than sharing ``next.E076``, so silencing the noise from one mistyped key never silences this one.
       The per-key probes are skipped, because there is nothing to index into.
       No other area repeats the condition under a code of its own, so one mistyped setting costs one error.
     - ``next.conf.checks``
   * - ``next.E078``
     - A ``@context(zone=)`` names a zone the composed page template does not declare, so no zone request ever matches the callable and its value is missing from every zone render.
     - ``next.partial.checks.zones``
   * - ``next.E079``
     - A ``COMPONENT_BACKENDS`` entry is not a dict.
       It carries its own code rather than sharing ``next.E002`` with ``PAGE_BACKENDS``, so silencing one settings key never silences the other.
     - ``next.components.checks``
   * - ``next.E080``
     - A ``COMPONENTS_DIR`` value is not a string.
       It carries its own code rather than sharing ``next.E027`` with ``PAGES_DIR``, for the same reason.
     - ``next.components.checks``
   * - ``next.E081``
     - ``NEXT_FRAMEWORK['PAGE_BACKENDS']`` is not a list, so no page backend entry can be read.
     - ``next.urls.checks``
   * - ``next.E082``
     - A route names a bracket parameter Django refuses as a route name, so the route reaches neither the URLconf nor the conflict map.
       The directory-level counterpart is ``next.E008``, which reads the same normalisation rule.
     - ``next.urls.checks``
   * - ``next.E083``
     - ``STATICFILES_FINDERS`` lists an ``AppDirectoriesFinder`` subclass that does not extend ``next.static.NextAppDirectoriesFinder``, and the configuration is refused rather than reported as a risk.
       The framework's ``next/static`` directory is the ``next.static`` Python package, so such a finder hands ``collectstatic`` the framework's own modules and their bytecode cache to publish into ``STATIC_ROOT``.
       Django's stock path is substituted automatically, so the check fires only for a finder the project wrote itself, and subclassing ``next.static.NextAppDirectoriesFinder`` clears it.
     - ``next.static.checks``
   * - ``next.E084``
     - A ``component.py`` raises while importing, so the render falls back to the template alone and every ``@component.context`` of that module stays out of the body.
       The message names the recorded exception type and text, which is what tells a missing import path apart from a typo in the module.
       Importing every component module costs a full tree walk, so the check carries ``deploy=True`` and the code fires under ``manage.py check --deploy`` alone.
     - ``next.components.checks``
   * - ``next.E085``
     - An ``@action`` declares a ``scope`` keyword other than ``"page"`` or ``"shared"``.
       It carries its own code rather than sharing ``next.E047`` with ``Meta.scope``, so silencing one spelling never silences the other.
     - ``next.forms.checks.actions``
   * - ``next.E086``
     - ``FORM_ANCHOR_FILES`` is a list holding something other than a string.
     - ``next.forms.checks.config``
   * - ``next.E087``
     - A directory name opens with ``[`` but closes neither as ``[param]`` nor as ``[[args]]``, so the router reads it as an incomplete wildcard segment.
     - ``next.pages.checks.structure``
   * - ``next.E088``
     - A router reported its page trees and the walk of one of them then raised.
       A router that cannot report its trees at all is ``next.E030``.
     - ``next.pages.checks.structure``
   * - ``next.E089``
     - A ``TEMPLATE_LOADERS`` entry imports into something that is no ``TemplateLoader`` subclass.
     - ``next.pages.checks.loaders``
   * - ``next.E090``
     - A custom patch op name is no valid verb token.
       A verb travels in the JSON envelope, so the name is limited to letters, digits, dots, hyphens, and underscores.
     - ``next.partial.checks.ops``
   * - ``next.E092``
     - A ``STATIC_BACKENDS`` entry holds a ``BACKEND`` that is not a dotted string.
     - ``next.static.checks``
   * - ``next.E093``
     - A ``STATIC_BACKENDS`` entry names a class that is no ``StaticBackend`` subclass.
     - ``next.static.checks``
   * - ``next.E094``
     - The file router ``OPTIONS`` value is not a dictionary.
     - ``next.urls.checks``
   * - ``next.E095``
     - ``OPTIONS['context_processors']`` is not a list.
     - ``next.urls.checks``
   * - ``next.E096``
     - ``OPTIONS['context_processors']`` holds an entry that is not a string.
     - ``next.urls.checks``
   * - ``next.E097``
     - ``OPTIONS`` names a key other than ``context_processors``, the one option a file router entry supports.
       Extra page roots belong in the top-level ``DIRS``.
     - ``next.urls.checks``
   * - ``next.E098``
     - ``NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]`` is no mapping, or names a key or a value the metadata schema refuses.
       The keys are the lower-case ones a ``page.py`` declares, and the settings title takes only the ``template`` and ``default`` form.
       ``DEFAULTS`` takes no ``breadcrumb``, and an option of ``METADATA`` outside ``DEFAULTS``, ``CANONICAL_QUERY``, and ``RENDERER`` is ``next.E035``.
     - ``next.pages.checks.metadata.scope``
   * - ``next.E099``
     - A title template, in the settings or in a ``page.py``, names a placeholder outside ``{title}`` and ``{site_name}``, reaches an attribute or an index, carries a conversion or a format spec, or is malformed.
       Every template is evaluated under every ``LANGUAGES`` code, and the message names the languages the failure held under when it is not all of them.
     - ``next.pages.checks.metadata.titles``
   * - ``next.E100``
     - A title template is declared without a ``default``, so a page under it with no title of its own renders none.
     - ``next.pages.checks.metadata.scope``, ``next.pages.checks.metadata.shape``
   * - ``next.E101``
     - A page folds one JSON-LD ``@id`` under two types, so the graph holds one node under two kinds.
       A bare fragment such as ``#org`` and the rooted ``/#org`` name one node, since both render on the site root.
     - ``next.pages.checks.metadata.ld``
   * - ``next.E102``
     - A ``page.py`` declares both a ``metadata`` dict and a ``@page.metadata`` callable, or names its ``@page.metadata`` callable ``metadata``, the name the dict takes.
     - ``next.pages.checks.metadata.shape``
   * - ``next.E103``
     - A module-level ``metadata`` is not a mapping.
     - ``next.pages.checks.metadata.shape``
   * - ``next.E104``
     - A ``metadata`` dict names a key or a value the schema refuses, at any depth, the key path named in the message.
       This covers a URL with a scheme outside http and https, ``x-default`` given both in ``languages`` and as ``x_default``, a NaN or a non-string key in raw JSON-LD, ``canonical: False``, an ``og.determiner``, ``viewport_fit``, ``interactive_widget``, or ``color_scheme`` outside its values, a ``twitter.card`` outside ``summary``, ``summary_large_image``, ``app``, and ``player``, and a ``player`` card without ``twitter.player``.
       A lazy URL passes unforced, and a render that forces it to such a scheme leaves the tag out and logs the ``PageMetadataShapeError`` at most once every ten minutes.
       Under ``DEBUG`` or ``STRICT_LOADING`` the render raises it instead.
     - ``next.pages.checks.metadata.shape``
   * - ``next.E105``
     - A title, a title default, or an absolute title is the empty string, which renders an empty ``<title>``.
     - ``next.pages.checks.metadata.scope``, ``next.pages.checks.metadata.shape``
   * - ``next.E106``
     - A ``@page.metadata`` callable is declared in a file no page render collects, an imported helper module or a sibling page.
     - ``next.pages.checks.metadata.shape``
   * - ``next.E107``
     - ``NEXT_FRAMEWORK["METADATA"]["RENDERER"]`` does not import or names no concrete ``MetadataRenderer`` subclass.
       The check imports the dotted path and never builds the class.
       A render with such a value falls back to ``HtmlMetadataRenderer`` and logs it at most once every ten minutes, and raises under ``DEBUG`` or ``STRICT_LOADING``.
     - ``next.pages.checks.metadata.scope``
   * - ``next.E108``
     - A ``@page.metadata`` callable is not annotated as returning a mapping.
       The check is static, because running the callable at check time could reach an unmigrated database.
     - ``next.pages.checks.metadata.shape``
   * - ``next.E110``
     - A ``sitemap.py`` or a ``robots.py`` raises on import, the cause named, a ``RobotsRuleError`` among them.
       The route stays and does not fall back to another source, a broken ``sitemap.py`` answering 404 and a broken ``robots.py`` answering 503, since a crawler reads a 404 on ``/robots.txt`` as no restriction at all.
     - ``next.seo.checks.sources``
   * - ``next.E111``
     - An ``@sitemap.items`` trail is routed by no page of its tree, so the sitemap raises when built.
     - ``next.seo.checks.sitemaps``
   * - ``next.E112``
     - A ``sitemap.py`` exists and ``sitemap.xml`` or ``sitemap_index.xml`` does not load, because ``django.contrib.sitemaps`` is not installed or the template backend has no ``APP_DIRS``.
     - ``next.seo.checks.sitemaps``
   * - ``next.E113``
     - A module attribute of ``sitemap.py`` or ``robots.py`` is outside its shape, a ``changefreq`` outside the protocol, a ``priority`` outside 0 to 1, a ``limit`` outside 1 to 50000, a ``cache`` that is no int, ``False``, or ``CacheDict``, an ``exclude`` or ``languages`` that is not a list of strings, an ``i18n``, ``alternates``, or ``x_default`` that is not a bool, a ``protocol`` outside ``http`` and ``https``, or a ``section`` that is no slug.
       In ``robots.py`` it covers a ``rules`` that is neither a list of ``RobotsRule`` nor a callable, and a ``sitemaps`` entry that is no absolute http or https URL on one line.
     - ``next.seo.checks.sources``
   * - ``next.E114``
     - More than one source serves ``/robots.txt``, a ``robots.py`` beside a ``robots.txt`` or a source in two page roots, and only the first answers.
       The message names the source that answers and the ones ignored.
       At runtime the same choice is logged at most once every ten minutes under ``DEBUG`` and never in production, where this check is the report.
     - ``next.seo.checks.robots``
   * - ``next.E115``
     - A page directory is named after an address the SEO sources serve, ``sitemap.xml``, ``sitemap-<section>.xml``, or ``robots.txt``, so the page answers its own path, such as ``/sitemap.xml/``, beside the framework route.
       Crawlers and visitors then reach two different documents, and the message names both paths.
       A urlpattern of the project answering the address first is ``next.W094``.
     - ``next.seo.checks.routes``
   * - ``next.E116``
     - Two sources serve one sitemap section name, two page trees, an ``@sitemap.items(section=...)``, or a backend, and only the first is listed.
     - ``next.seo.checks.sitemaps``
   * - ``next.E117``
     - A static ``robots.txt`` does not decode as UTF-8.
     - ``next.seo.checks.robots``
   * - ``next.E118``
     - ``@sitemap.items`` runs in a file no sitemap build reads, anything other than the ``sitemap.py`` at the top of a routed page tree, such as a nested ``sitemap.py``, a ``page.py``, or a helper module.
       A registration binds to the file running the decorator, so the fix is to run it in the root ``sitemap.py``, which may import the callable from anywhere.
     - ``next.seo.checks.sources``
   * - ``next.E128``
     - A ``sitemap.py`` runs ``@sitemap.items`` twice for one trail, so only the later callable lists its URLs.
       The message names both callables, and the fix is to merge them into one.
     - ``next.seo.checks.sources``
   * - ``next.E119``
     - A ``sitemap.py`` or a ``robots.py`` opens with ``from __future__ import annotations``, which turns the annotations the dependency resolver reads into strings.
     - ``next.seo.checks.sources``
   * - ``next.E120``
     - ``NEXT_FRAMEWORK["SEO"]["SITEMAP_BACKENDS"]`` is no list, or an entry is no mapping with a ``BACKEND`` naming a ``SitemapBackend`` subclass, or carries an ``OPTIONS`` that is no mapping.
     - ``next.seo.checks.backends``
   * - ``next.E121``
     - A ``@page.metadata`` callable annotates a parameter ``Metadata``, which nothing fills since the chain merges the ancestors.
     - ``next.pages.checks.metadata.shape``
   * - ``next.E122``
     - A ``links`` entry names a rel another key renders, ``canonical``, ``alternate``, ``icon``, ``shortcut``, ``apple-touch-icon``, ``mask-icon``, ``manifest``, or ``stylesheet``.
     - ``next.pages.checks.metadata.head``
   * - ``next.E123``
     - A ``preconnect`` or ``dns-prefetch`` link names an ``href`` that is no origin, or a ``preload`` link carries no ``as``.
       A lazy ``href`` is not forced to be read.
     - ``next.pages.checks.metadata.head``
   * - ``next.E124``
     - An icon declares ``sizes`` other than ``any`` or ``WxH``, a type outside ``image/*``, or is a ``mask-icon`` without a color.
     - ``next.pages.checks.metadata.head``
   * - ``next.E125``
     - ``other`` names a meta a typed key renders, or ``properties`` names an ``og:*``, ``article:*``, ``profile:*``, or ``book:*`` property.
     - ``next.pages.checks.metadata.head``
   * - ``next.E126``
     - A viewport scale is outside 0.1 to 10.
     - ``next.pages.checks.metadata.head``
   * - ``next.E127``
     - A declared JSON-LD node does not serialise to JSON, a ``nan``, an infinity, a set, or a time with a time zone among the causes.
       The check serialises the node through ``dump_jsonld``, the call the renderer makes, so a node that passes renders.
       A naive datetime passes, since the renderer gives it the current time zone.
       One ``@id`` under two types is ``next.E101``.
     - ``next.pages.checks.metadata.ld``
   * - ``next.E129``
     - A ``SITE`` value is unusable, a ``URL`` that is no bare http or https origin, one carrying a path, a query, or a fragment included, or a dotted path that does not import, a ``NAME`` that is no text, or an ``INDEXABLE`` outside ``"auto"``, a bool, and a callable.
       A callable ``URL`` or ``INDEXABLE`` that cannot be called with the request as its one positional argument is reported too, read from its signature alone, since the check never calls it.
     - ``next.site.checks``
   * - ``next.E130``
     - ``NEXT_JS_OPTIONS["policy"]`` names no ``ScriptInjectionPolicy``, read through ``NextScriptBuilder.from_options`` as a render reads it.
       Pages inject the runtime as under ``"auto"``, and log the fallback at most once every ten minutes.
     - ``next.static.checks``
   * - ``next.E131``
     - A ``page.py`` declares a ``cache`` or ``headers`` the response cannot carry as written, an unknown key, a negative age, a flag that is no bool, ``public`` with ``no_store``, a forbidden or invalid header name, or a value with a control character or a character outside ASCII.
     - ``next.pages.checks.responses``
   * - ``next.E132``
     - ``CSRF_DELIVERY`` names no mode, so pages deliver the token as under ``"auto"``.
     - ``next.pages.checks.responses``
   * - ``next.E133``
     - A ``scripts.py`` fails to import, and its tree runs none of its scripts, or its ``scripts`` holds anything but ``Script`` values, and the tree runs only the ``Script`` values it holds.
       The message names the import error or the stray type, and the pages keep rendering meanwhile.
     - ``next.scripts.checks``
   * - ``next.E134``
     - A ``scripts.py`` declares one script name twice.
     - ``next.scripts.checks``
   * - ``next.E135``
     - ``CONSENT["CATEGORIES"]`` is no list of names or lacks ``necessary``.
       While it fires, ``next.E140`` stays silent, since the list is fixed first.
     - ``next.consent.checks``
   * - ``next.E136``
     - A script carries neither ``src`` nor ``init``.
     - ``next.scripts.checks``
   * - ``next.E137``
     - ``CONSENT["BACKEND"]`` does not import or is no ``ConsentBackend`` subclass.
       Pages still render, every visitor reading as undecided with every category but ``necessary`` denied, and the failure is logged at most once every ten minutes, or raised under ``DEBUG`` or ``STRICT_LOADING``.
       Name a subclass by its dotted path, or remove ``BACKEND`` to read the cookie the runtime writes.
     - ``next.consent.checks``
   * - ``next.E138``
     - A script loads through the runtime, by a gated category or an ``IDLE``, ``INTERACTION``, or ``MANUAL`` strategy, while ``NEXT_JS_OPTIONS["policy"]`` keeps the runtime off every page.
     - ``next.scripts.checks``
   * - ``next.E139``
     - A custom ``preload_template``, ``script_tag_template``, or ``init_template`` in ``NEXT_JS_OPTIONS``, or a ``css_tag``, ``js_tag``, or ``module_tag`` in a ``STATIC_BACKENDS`` entry, does not format with its fields, ``{url}`` or ``{payload}`` and ``{nonce_attr}``, through a stray brace or another field.
       The check formats each with blank values, and a render uses the default tag in its place and logs it at most once every ten minutes.
     - ``next.static.checks``
   * - ``next.E140``
     - A script names a category ``CONSENT["CATEGORIES"]`` does not list, so no visitor can grant it.
       Silent while ``next.E135`` reports the list.
     - ``next.scripts.checks``
   * - ``next.E141``
     - A script ``src`` is neither an http or https URL nor a staticfiles name a finder answers, such as another scheme, a scheme-relative URL, an absolute path, or a name that climbs out of the static root.
     - ``next.scripts.checks``
   * - ``next.E142``
     - A script ``init`` holds ``</script`` or ``<script`` followed by whitespace, ``/``, or ``>``, or holds ``<!--``, which the HTML parser reads as markup, so the element closes early or hides the rest of the page.
     - ``next.scripts.checks``
   * - ``next.E143``
     - A script carries an attribute outside ``integrity``, ``crossorigin``, ``referrerpolicy``, and the ``data-*`` names.
     - ``next.scripts.checks``
   * - ``next.E144``
     - A script names a strategy outside ``Strategy``.
     - ``next.scripts.checks``
   * - ``next.E145``
     - ``CONSENT["SERVER_RENDER"]`` is outside ``"auto"``, ``True``, and ``False``.
     - ``next.consent.checks``
   * - ``next.E146``
     - A ``CONSENT["CATEGORIES"]`` name holds a character outside letters, digits, ``_``, ``-``, and ``.``, which the consent cookie ``2:<a>|<b>:<seconds>`` cannot carry.
     - ``next.consent.checks``
   * - ``next.E147``
     - A registered asset kind renders through a method of the rendering backend, the first ``STATIC_BACKENDS`` entry that loads, which is missing or takes no ``request`` keyword, so every page holding such an asset fails to render, or takes no ``nonce`` keyword, so such a page fails to render whenever its request carries a CSP nonce.
     - ``next.static.checks``
   * - ``next.E148``
     - ``CSRF_DELIVERY`` is ``"lazy"``, or ``"auto"`` with a page whose ``cache`` a CDN may hold, while ``ROOT_URLCONF`` does not route the ``_next/csrf/`` endpoint, so ``csrf_url()`` does not reverse.
       Pages embed the token instead and log it at most once every ten minutes, and the hint asks for ``include("next.urls")``.
     - ``next.pages.checks.responses``
   * - ``next.E149``
     - The CSRF token endpoint or the form action endpoint reverses, but ``resolve()`` of its address returns a view other than the framework view, such as a project pattern listed above ``include("next.urls")``, a project view that reuses the URL name ``csrf`` or ``form_action``, or a page, which the message names by its file.
       The framework routes lead the patterns of ``next.urls``, so a page tree cannot shadow them from inside the include.
     - ``next.urls.checks``
   * - ``next.E150``
     - The consent cookie's ``samesite`` option is not ``"Lax"``, ``"Strict"``, or ``"None"`` in any letter case, so the browser ignores it and the cookie takes its default policy.
       Only a ``CookieConsentBackend`` or a subclass of it is checked.
     - ``next.consent.checks``
   * - ``next.E151``
     - The consent cookie's ``max_age`` option is not a positive int.
       A value that is not an int is replaced by the default age, and zero or less makes the browser drop the cookie at once, so the choice is not kept.
     - ``next.consent.checks``

A code emitted by ``next.checks.common`` or by ``next.discovery`` is produced by a shared helper that the listed subsystem check modules call.

Warnings
~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 12 58 30

   * - Code
     - Condition
     - Emitted by
   * - ``next.W001``
     - A ``layout.djx`` carries no ``{% template %}`` placeholder, so composition drops the layout and the pages under it render without its markup.
       The paired ``{% #template %}`` form, whose body is a fallback, counts as the placeholder too.
     - ``next.pages.checks.layouts``
   * - ``next.W002``
     - A directory named by ``PAGES_DIR`` sits beside the working directory, holds pages, and no configured router routes it, so nothing under it is served.
       Name the directory in ``PAGE_BACKENDS`` ``DIRS``, or turn that entry's ``APP_DIRS`` off, which routes ``BASE_DIR`` over ``PAGES_DIR`` when ``DIRS`` names no root.
       The tree is not walked by the page checks, so its contents raise no ``next.E010``, ``next.E012``, or ``next.E017``.
       This one warning stands for all of them.
     - ``next.pages.checks.structure``
   * - ``next.W030``
     - ``STATIC_BACKENDS`` is empty, so the framework falls back to ``StaticFilesBackend``.
     - ``next.static.checks``
   * - ``next.W031``
     - An ``OPTIONS`` tag template is missing the ``{url}`` placeholder.
     - ``next.static.checks``
   * - ``next.W042``
     - ``JS_CONTEXT_SERIALIZER`` holds a value that is not a dotted-path string.
       The failures of a path that is one carry ``next.W079`` through ``next.W082``.
     - ``next.static.checks``
   * - ``next.W043``
     - A ``page.py`` declares more than one body source and the lower-priority ones are ignored.
     - ``next.pages.checks.modules``
   * - ``next.W046``
     - A form class is declared in a file outside ``BASE_DIR`` and will not be registered automatically.
     - ``next.forms.checks.actions``
   * - ``next.W054``
     - A ``ComponentWidget`` names a component that does not resolve.
     - ``next.forms.checks.widgets``
   * - ``next.W055``
     - A ``ComponentWidget`` without multipart binding is attached to a ``FileField``, one with it such as ``ComponentFileWidget`` to a field that is not one, or any ``ComponentWidget`` to a ``MultiValueField``.
     - ``next.forms.checks.widgets``
   * - ``next.W056``
     - Wizards are registered and the configured wizard backend needs Django sessions to store steps, but ``django.contrib.sessions`` is not installed.
     - ``next.forms.checks.wizards``
   * - ``next.W057``
     - A static ``Meta.steps`` form class is also registered as a standalone form action.
     - ``next.forms.checks.wizards``
   * - ``next.W058``
     - A static ``Meta.steps`` form declares a ``FileField`` or ``ImageField``, whose uploads do not survive the wizard draft storage between requests.
     - ``next.forms.checks.wizards``
   * - ``next.W059``
     - Two static wizard steps declare the same field name, so ``get_all_cleaned_data()`` keeps only the last value.
     - ``next.forms.checks.wizards``
   * - ``next.W060``
     - A form action declares ``permission_required`` while ``django.contrib.auth`` is not in ``INSTALLED_APPS``.
     - ``next.forms.checks.actions``
   * - ``next.W061``
     - A form action declares ``Meta.success_message`` while the messages framework is not fully installed, so a valid submission raises ``MessageFailure``.
     - ``next.forms.checks.actions``
   * - ``next.W062``
     - No ``DjangoTemplates`` engine is configured, so the framework ``{% %}`` tags cannot install.
     - ``next.apps.checks``
   * - ``next.W063``
     - A tag library under ``next.templatetags`` is not listed as a builtin, so its tags never install.
     - ``next.apps.checks``
   * - ``next.W067``
     - A ``{% zone %}`` is a direct child of a ``{% with %}`` block, whose bindings a standalone zone render cannot see.
     - ``next.partial.checks.zones``
   * - ``next.W068``
     - A form action backend overrides ``shape_response`` while ``PARTIAL_BACKENDS`` is configured, so it may drop the patch envelope.
     - ``next.partial.checks.forms``
   * - ``next.W069``
     - A partial backend sets ``VERSION: "manifest"`` while the staticfiles storage does not hash files, so the version guard stays silent.
     - ``next.partial.checks.backends``
   * - ``next.W070``
     - A ``{% form %}`` renders directly inside a ``{% for %}`` of a composed page without a ``key=`` or a ``zone=``, so a partial morph cannot tell the repeated instances apart.
       The check does not descend into a component template, so a looped ``{% component %}`` that holds the form is not flagged.
       Thread a ``key=`` into the form to keep the repeated morph correct.
     - ``next.partial.checks.forms``
   * - ``next.W071``
     - ``PARTIAL_BACKENDS`` has more than one entry.
       Partial rendering uses a single protocol backend, so only the first entry runs and the rest are ignored.
     - ``next.partial.checks.backends``
   * - ``next.W072``
     - A ``NEXT_FRAMEWORK`` bool key, ``STRICT_CONTEXT``, ``STRICT_LOADING``, ``LAZY_COMPONENT_MODULES``, ``FORM_AUTODISCOVER``, ``STATIC_DISCOVERY_CACHE``, or ``CSP_NONCE``, holds a non-bool value.
       The ``bool()`` coercion turns a falsy-looking string such as ``'False'`` into ``True``, so the written value can mean the opposite of the intent.
     - ``next.conf.checks``
   * - ``next.W074``
     - A registered asset kind names a renderer outside ``render_link_tag``, ``render_script_tag``, and ``render_module_tag``, so it carries no client insertion verb.
       Assets of that kind reach the browser only on a full page render, never through a patch envelope.
     - ``next.static.checks``
   * - ``next.W075``
     - A page or a component registers a keyed ``serialize=True`` context under a name the ``next.min.js`` init payload reserves, ``$csrf``, ``$dev``, ``$chunks``, ``$scripts``, or ``$consent``.
       The framework owns those names on every render, so the registered value never reaches ``window.Next.context`` and no ``context`` patch updates it.
       The message names the declaring ``page.py`` or ``component.py`` and asks for a rename.
       A keyless ``serialize=True`` provider spreads the keys of the dict it returns at render time, so the check never sees them.
     - ``next.static.checks``
   * - ``next.W076``
     - A registered asset kind names one of the three bundled renderers together with an ``inline_tag`` that is not the element that renderer's verb builds.
       The URL form of such a kind travels in a patch envelope while its inline bodies carry no insertion verb and reach the browser only on a full page render.
       Pair ``render_link_tag`` with ``inline_tag="style"`` or ``render_script_tag`` with ``inline_tag="script"``.
     - ``next.static.checks``
   * - ``next.W077``
     - A ``@context`` parameter names the key of another ``@context`` bound to a zone the reader does not share, so a request outside that zone skips the provider and the reader runs with ``None``.
     - ``next.pages.checks.zones``
   * - ``next.W078``
     - A ``layout.djx`` carries more than one ``{% template %}`` placeholder.
       Composition fills the first one and every other renders its own fallback instead of the page.
       It carries its own code rather than sharing ``next.W001``, so silencing one layout mistake never silences the other.
     - ``next.pages.checks.layouts``
   * - ``next.W079``
     - The ``JS_CONTEXT_SERIALIZER`` dotted path cannot be imported.
     - ``next.static.checks``
   * - ``next.W080``
     - The ``JS_CONTEXT_SERIALIZER`` path imports into something that is no class.
     - ``next.static.checks``
   * - ``next.W081``
     - The ``JS_CONTEXT_SERIALIZER`` class cannot be instantiated with no arguments.
     - ``next.static.checks``
   * - ``next.W082``
     - The ``JS_CONTEXT_SERIALIZER`` instance does not implement the ``JsContextSerializer`` protocol, which needs a ``dumps(value) -> str`` method.
     - ``next.static.checks``
   * - ``next.W083``
     - The asset version of a partial response resolves to a constant, because no ``PARTIAL_BACKENDS`` entry names a ``VERSION``, ``STATIC_VERSION`` is unset, and the staticfiles storage hashes nothing into a manifest.
       Every deploy then stamps the same version and the guard cannot ask an open client to reload stale assets.
       The check carries ``deploy=True`` and the code fires under ``manage.py check --deploy`` alone, because a development checkout is exactly the configuration it describes.
     - ``next.partial.checks.backends``
   * - ``next.W084``
     - A title template never names ``{title}``, so every page under it renders the same title.
       The hint names ``{title}`` as the spelling, because the ``%s`` placeholder of Next.js is not substituted.
     - ``next.pages.checks.metadata.titles``
   * - ``next.W085``
     - A page declares metadata, but its composed template, the components it reaches, and the templates it includes render no ``{% metadata %}``, so the head tags never reach the page.
       An ``{% include %}`` is followed only when it names its template by a literal string, and one the check cannot follow, a variable or filtered name, a missing or broken template, or an include loop, keeps the page silent.
       A ``render()`` page and a composition ``next.E072`` reports are skipped.
     - ``next.pages.checks.metadata.templates``
   * - ``next.W086``
     - A ``sitemap.py`` with ``i18n`` and ``alternates`` sets a ``limit`` above the one a page of alternates fits in 50 MB, ``50000 // (languages + 1 + x_default)``.
       Every URL links each language then, so the section lists the smaller number per page, and the message names it as the highest ``limit`` to set.
     - ``next.seo.checks.sitemaps``
   * - ``next.W087``
     - A page asks for hreflang alternates with ``alternates.languages=True``, but ``ROOT_URLCONF`` uses no ``i18n_patterns()``, so every language points at the same URL.
     - ``next.pages.checks.metadata.shape``
   * - ``next.W088``
     - A ``noindex`` page points its canonical at another origin, which passes no signal.
       On a site closed to search every page reads as ``noindex``, so any cross-origin canonical triggers it.
     - ``next.pages.checks.metadata.shape``
   * - ``next.W089``
     - A ``SITEMAP_BACKENDS`` backend raises from ``sections(None)``, which the checks call without a request to compare its section names with the others, so ``next.E116`` cannot read its sections.
       The message names the dotted backend class and what it raised, and the fix is to make ``sections()`` answer without a request.
     - ``next.seo.checks.sitemaps``
   * - ``next.W090``
     - The client runtime ``next/next.min.js`` or a lazy chunk other than the dev one is unbuilt, so no static files finder answers it, or the static files storage, a hashing one such as ``ManifestStaticFilesStorage``, holds no entry for it.
       A page renders without the runtime, or without the chunk, and logs it at most once every ten minutes.
       The check carries ``deploy=True``.
     - ``next.static.checks``
   * - ``next.W091``
     - A ``{% #consented %}`` block on a composed page, a component it reaches, or a template it includes names by a literal a category ``CONSENT["CATEGORIES"]`` does not list, so no visitor can grant it and the block always renders its ``else`` branch.
       A category named by a variable is not read, and under ``DEBUG`` a render logs such a category at most once every ten minutes.
       Silent while ``CONSENT`` is unset, which ``next.W123`` reports, or while ``next.E135`` reports the list.
     - ``next.scripts.checks``
   * - ``next.W092``
     - A dynamic route of a tree with a ``sitemap.py`` has no ``@sitemap.items`` callable, matches no ``exclude`` glob, and is not ``noindex`` by its static metadata, so the sitemap lists no URL for it.
     - ``next.seo.checks.sitemaps``
   * - ``next.W093``
     - An ``@sitemap.items`` trail names a page whose static metadata is ``noindex``, so the sitemap lists a page whose tag keeps it out of the index.
       Silent while the site is closed to search, which serves no sitemap.
     - ``next.seo.checks.sitemaps``
   * - ``next.W094``
     - A served SEO route, ``/sitemap.xml`` or ``/robots.txt``, does not resolve to the framework view at the host root under ``ROOT_URLCONF``, because ``include("next.urls")`` sits under a prefix or inside ``i18n_patterns``, or a pattern of the project answers the address first.
     - ``next.seo.checks.routes``
   * - ``next.W095``
     - A ``Disallow`` of ``robots.py`` covers a URL the sitemap lists, or ``/sitemap.xml`` itself.
       A dynamic route counts by its URL cut at the first parameter, reversed with placeholder values, so a tree of dynamic routes alone is checked too, and a trail whose converter takes no placeholder, a custom converter for one, is skipped.
       Only the groups of static ``rules`` that name ``*`` are read, since a crawler named in a group of its own follows that group alone, and the check is silent while the site is closed to search.
     - ``next.seo.checks.robots``
   * - ``next.W096``
     - A ``Disallow`` of ``robots.py`` covers a ``noindex`` page, whose tag a crawler kept out never reads.
       Dynamic routes and the groups read count as ``next.W095`` describes.
     - ``next.seo.checks.robots``
   * - ``next.W097``
     - A ``sitemap.py``, ``robots.py``, or ``robots.txt`` sits below the top of its page tree, where nothing reads it.
     - ``next.seo.checks.sources``
   * - ``next.W098``
     - A static ``robots.txt`` names no ``Sitemap:`` line while the project serves a sitemap, and a static file is served as written.
       Silent while the site is closed to search.
     - ``next.seo.checks.robots``
   * - ``next.W099``
     - Two page roots take the same sitemap section label, so the trees serve as numbered sections in router order, and the message lists the sections actually served.
       Routing them from differently named directories or different apps keeps the section addresses stable.
     - ``next.seo.checks.sitemaps``
   * - ``next.W100``
     - The i18n options of a ``sitemap.py`` take no effect as written, ``alternates`` or ``x_default`` without ``i18n``, ``x_default`` without ``alternates``, or a ``languages`` code outside ``settings.LANGUAGES``.
       Each problem names its own fix, setting the option it needs or dropping the one not read.
     - ``next.seo.checks.sitemaps``
   * - ``next.W101``
     - A ``sitemap.py`` sets ``i18n = True`` while ``ROOT_URLCONF`` mounts no ``i18n_patterns()``, so every language lists the same URL.
     - ``next.seo.checks.sitemaps``
   * - ``next.W102``
     - An ``@sitemap.items`` trail matches an ``exclude`` glob of the same file, so its URLs are dropped.
     - ``next.seo.checks.sitemaps``
   * - ``next.W103``
     - One segment declares several JSON-LD nodes with the same ``@id``, and only the last renders.
     - ``next.pages.checks.metadata.scope``
   * - ``next.W104``
     - ``DEFAULTS`` wraps a value in ``Replace`` or ``RESET``, which has no inherited value to replace there.
     - ``next.pages.checks.metadata.scope``
   * - ``next.W105``
     - A ``links`` entry names a rel no browser knows.
     - ``next.pages.checks.metadata.head``
   * - ``next.W106``
     - Two icons share their rel, sizes, and media, and the browser picks either.
     - ``next.pages.checks.metadata.head``
   * - ``next.W107``
     - A viewport keeps the page from zooming, through ``user_scalable=False`` or a ``maximum_scale`` below 2.
     - ``next.pages.checks.metadata.head``
   * - ``next.W108``
     - A page folds an ``og:locale`` Open Graph cannot read, or ``locale_alternates=True`` meets a language no ``ll_CC`` locale derives from.
     - ``next.pages.checks.metadata.head``
   * - ``next.W109``
     - A page folds a viewport or a theme color while its composed template writes a literal meta of the same name, so the head carries two that disagree.
     - ``next.pages.checks.metadata.head``
   * - ``next.W110``
     - ``SITE["URL"]`` is unset and no ``django.contrib.sites`` row is pinned through ``SITE_ID``, so canonical, Open Graph, sitemap, and robots URLs follow the ``Host`` header.
       ``ALLOWED_HOSTS`` holding ``"*"`` raises it to ``next.W122``.
       The check carries ``deploy=True`` and the ``seo`` tag.
     - ``next.site.checks``
   * - ``next.W111``
     - ``SITE["INDEXABLE"]`` is ``False`` while the site still publishes a ``sitemap.py`` or a robots source for crawlers.
       A site private by design serves none of them and triggers no warning.
       The check carries ``deploy=True`` and the ``seo`` tag.
     - ``next.seo.checks.sources``
   * - ``next.W112``
     - A page a shared cache may hold renders a ``{% form %}`` or the runtime while ``CSRF_DELIVERY`` is ``"eager"``, so every response sets the CSRF cookie and is sent with ``Cache-Control: private``.
     - ``next.forms.checks.csrf``
   * - ``next.W113``
     - A page a shared cache may hold renders ``{% csrf_token %}``, which sets the CSRF cookie on every response.
       A page with a callable ``cache`` counts as one a shared cache may hold, here and in ``next.W114``, ``next.W120``, ``next.W121``, and ``next.W124``, and the message marks it ``(callable cache)``.
     - ``next.pages.checks.responses``
   * - ``next.W114``
     - A page a shared cache may hold answers several languages at one URL, ``LocaleMiddleware`` active with more than one language and the pages outside ``i18n_patterns()``.
     - ``next.pages.checks.responses``
   * - ``next.W115``
     - A page a shared cache may hold, or any page under ``CSRF_DELIVERY="lazy"``, renders a ``{% form %}`` without a CSRF field, so a browser without JavaScript gets 403 on submit.
       A form naming by a literal an action that declares ``requires_runtime`` does not count, while one naming its action through a variable or naming no registered action still does.
     - ``next.forms.checks.csrf``
   * - ``next.W116``
     - A gated script loads ``BLOCKING`` while consent may render on the client, where it loads after the page and blocks nothing.
     - ``next.scripts.checks``
   * - ``next.W117``
     - A custom ``NEXT_JS_OPTIONS`` or backend tag template holds no ``{nonce_attr}`` while a CSP nonce is active, so the tag it renders is refused.
     - ``next.static.checks``
   * - ``next.W118``
     - A script loads its ``src`` over plain HTTP, which a https page blocks as mixed content.
       The check carries ``deploy=True``.
     - ``next.scripts.checks``
   * - ``next.W119``
     - The consent cookie's ``secure`` option is ``False`` while ``SESSION_COOKIE_SECURE`` is on.
       The check carries ``deploy=True``.
     - ``next.consent.checks``
   * - ``next.W120``
     - A CSP nonce is active, ``CSP_NONCE`` on and a nonce-minting middleware installed, while pages declare a ``cache`` a CDN may hold.
       A nonce belongs to one visitor, so each of those pages is sent with ``Cache-Control: private``, and the message lists them.
     - ``next.static.checks``
   * - ``next.W121``
     - ``CSRF_USE_SESSIONS`` is on while pages declare a ``cache`` a CDN may hold.
       The CSRF middleware then reads the session on every request, so each of those pages is sent with ``Cache-Control: private``, and the message lists them.
     - ``next.pages.checks.responses``
   * - ``next.W122``
     - ``SITE["URL"]`` is unset while ``ALLOWED_HOSTS`` holds ``"*"``, so any client picks the host of the canonical, Open Graph, sitemap, and robots URLs, and a CDN may keep them for everyone.
       The check carries ``deploy=True`` and the ``seo`` tag.
     - ``next.site.checks``
   * - ``next.W123``
     - ``{% #consented %}`` renders on a composed page while ``NEXT_FRAMEWORK`` holds no ``CONSENT`` entry.
       A partial response carries no consent state, so a block that reaches a page only through a patch stays hidden there.
     - ``next.scripts.checks``
   * - ``next.W124``
     - ``settings.MIDDLEWARE`` lists ``ConditionalGetMiddleware`` below a middleware that may set a cookie, such as ``SessionMiddleware``, while pages declare a ``cache`` a CDN may hold.
       The 304 it answers copies the shared ``Cache-Control`` of the page, and a cookie the outer middleware sets afterwards reaches a public response the framework can no longer make private, so the message lists the pages.
       A middleware is matched by its class, so a subclass counts as its base, and one that does not import counts as one that may set a cookie.
       ``next.middleware.SharedCacheGuardMiddleware`` listed first, or with only ``UpdateCacheMiddleware`` above it, silences it, and the hint names it.
     - ``next.pages.checks.responses``
   * - ``next.W125``
     - The consent cookie's ``samesite`` option is ``"None"`` while ``secure`` is not ``True``, so a browser rejects the cookie on a page that is not served over https.
     - ``next.consent.checks``

Codes are assigned per check and are not contiguous.

See also
--------

.. seealso::

   :doc:`/content/intro/install` for the first ``manage.py check`` run.
   :doc:`/content/faq/troubleshooting` for symptoms that map to individual ``next.*`` codes.
