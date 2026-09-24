.. _ref-system-checks:

System checks
=============

Module summary
--------------

next.dj contributes Django system checks for every subsystem.
Run them through ``uv run python manage.py check`` and the framework reports configuration mistakes with a code and a hint.
Four of them are deployment checks and answer only to ``uv run python manage.py check --deploy``, see `Check registration`_ for which four and why.

Check registration
------------------

``next.checks.register_all`` runs during ``AppConfig.ready``.
It imports each subsystem ``checks`` module so the ``@register`` side effects take effect.
The imported modules are ``next.apps.checks``, ``next.components.checks``, ``next.conf.checks``, ``next.forms.checks``, ``next.pages.checks``, ``next.partial.checks``, ``next.static.checks``, and ``next.urls.checks``.

Each of these modules registers checks.
``next.pages.checks``, ``next.forms.checks``, and ``next.partial.checks`` are packages rather than single modules, and importing the package imports every submodule that carries a check.
The address a project imports stays the same either way, and each submodule opens with the catalogue of codes it owns, which is what the tables under `Check code reference`_ are derived from.

Four checks are deployment checks and carry ``deploy=True``, so ``manage.py check`` alone never runs them and ``manage.py check --deploy`` does.
They are ``check_page_module_imports`` (``next.E017``), ``check_component_module_imports`` (``next.E084``), ``check_composed_templates_compile`` (``next.E072``), and ``check_asset_version_moves_between_deploys`` (``next.W083``).
The first three import or compile every user module of a tree, which costs a full walk that a routine ``manage.py`` command should not pay.
The fourth describes a configuration every development checkout has, so reporting it outside a deployment audit would warn every project about nothing.

Every next.dj check carries the ``next`` tag.
That tag is the importable string constant ``next.checks.NEXT``, so a project check joins the framework ones by decorating itself with ``@register(NEXT)`` rather than by repeating the literal.
Run ``uv run python manage.py check --tag next`` to execute only the framework checks and skip the built-in Django and third-party ones.
Checks that also concern templates or URL patterns keep their :doc:`Django tags <django:ref/checks>` (``templates``, ``urls``) alongside ``next``, so filtering by those tags still reaches them.
A tagged run reports what a full run reports, and ``--tag next --deploy`` adds the four deployment checks to it.
Every check that reads registrations discovers the files declaring them itself, rather than relying on a URL check having expanded the router first.

``next.checks.reset_check_caches`` drops every per-run check cache so the next run rebuilds from the current sources.
The cached state covers the router and components managers, the composed-pages memo, the collected URL patterns, the page module memo, and the context registry.
Most of these caches also clear on ``settings_reloaded``, which a ``NEXT_FRAMEWORK`` change through ``override_settings`` triggers.
Tests and scripts that invoke checks directly and mutate the page or component tree in place call ``reset_check_caches`` explicitly, since the caches otherwise freeze the scanned state for the lifetime of the process.

Silencing a check
~~~~~~~~~~~~~~~~~

Django's ``SILENCED_SYSTEM_CHECKS`` setting takes a list of check ids and drops those messages from every run, framework ids included.
An id is the exact string the message carries, ``next.W059`` rather than a tag or a module path, so one entry silences one condition and leaves every other framework check in place.
The :doc:`Django settings reference <django:ref/settings>` documents the setting itself, and :doc:`django:ref/checks` covers the rest of the check framework.

Silencing answers a deliberate shape the check cannot recognise as intended, and it is the wrong answer to a defect the check names correctly.
The :repo:`audit-forms <tree/main/examples/audit-forms>` example earns ``next.W059``, which reports that two static wizard steps declare the same field name and that ``get_all_cleaned_data()`` keeps only the last value.
That wizard repeats one acknowledgement field across its three steps on purpose and reads the answer per step rather than out of the merged mapping, so the collapse the warning describes costs the project nothing and the id sits in ``SILENCED_SYSTEM_CHECKS`` beside a comment naming the reason.

A silenced check stays visible in the run.
``manage.py check`` counts the messages it dropped and closes with that number, so the setting hides the text of a message and never the fact that the project runs against the advice of a check.

.. warning::

   Silencing ``next.E077`` hides the one condition that means the whole ``NEXT_FRAMEWORK`` setting is ignored, so every value the project set in it is lost and the process runs on the framework defaults.

Shared helpers
~~~~~~~~~~~~~~

``next.checks.common`` holds helpers reused across subsystem check modules.
It is imported indirectly by those modules rather than by ``register_all``, and the router manager and the page-tree walk it passes on live in ``next.discovery``, outside this package, because production code reads them too.

.. automodule:: next.checks.common
   :members:

Subsystem checks
----------------

Pages
~~~~~

The package splits by subject, one submodule per group of codes.
``contexts`` covers the ``@context`` callables of a routed ``page.py``, ``layouts`` the page-body placeholder of a ``layout.djx``, ``loaders`` the ``TEMPLATE_LOADERS`` entries, ``modules`` what a ``page.py`` declares, ``processors`` the context processors a render runs, ``structure`` the shape of a tree on disk, and ``zones`` a ``@context`` reading a key another callable bound to a zone.

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

The package splits into ``actions`` for the registered form classes and ``@action`` handlers, ``config`` for the ``NEXT_FRAMEWORK`` keys the subsystem reads, ``widgets`` for the ``ComponentWidget`` a field carries, and ``wizards`` for the ``FormWizard`` subclasses.
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
``codes`` holds every identifier the package emits, so a submodule names a code without importing a sibling, and ``nodes`` and ``pages`` hold the node walk and the one-compile-per-page memo the zone and form checks share.

.. automodule:: next.partial.checks
   :members:
   :exclude-members: E_BACKENDS_NOT_A_LIST, E_BACKEND_WITHOUT_PATH, E_COMPOSED_TEMPLATE_SYNTAX, E_CONTEXT_ZONE_UNKNOWN, E_DUPLICATE_ZONE, E_LAZY_WITHOUT_PLACEHOLDER, E_NON_ASCII_ZONE, E_OP_BAD_NAME, E_OP_SHADOWS_BUILTIN, E_ZONE_IN_COMPONENT, E_ZONE_IN_FOR, E_ZONE_IN_IF, W_FORM_BACKEND_NOT_AWARE, W_FORM_IN_FOR_NO_KEY, W_MANIFEST_VERSION_NO_STORAGE, W_TOO_MANY_BACKENDS, W_WITH_OVER_ZONE

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
     - The router manager fails to initialize.
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
     - ``next.checks.common``
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
   * - ``next.E066``
     - A custom patch op shadows a built-in verb, so the built-in wins on the wire and the custom handler never runs.
       A name that is no valid verb token is ``next.E090``.
     - ``next.partial.checks.ops``
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
     - A ``NEXT_FRAMEWORK`` bool key, ``STRICT_CONTEXT``, ``STRICT_LOADING``, ``LAZY_COMPONENT_MODULES``, ``FORM_AUTODISCOVER``, or ``STATIC_DISCOVERY_CACHE``, holds a non-bool value.
       The ``bool()`` coercion turns a falsy-looking string such as ``'False'`` into ``True``, so the written value can mean the opposite of the intent.
     - ``next.conf.checks``
   * - ``next.W074``
     - A registered asset kind names a renderer outside ``render_link_tag``, ``render_script_tag``, and ``render_module_tag``, so it carries no client insertion verb.
       Assets of that kind reach the browser only on a full page render, never through a patch envelope.
     - ``next.static.checks``
   * - ``next.W075``
     - A page or a component registers a keyed ``serialize=True`` context under a name the ``next.min.js`` init payload reserves, ``$csrf`` or ``$dev``.
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

Codes are assigned per check and are not contiguous.

See also
--------

.. seealso::

   :doc:`/content/intro/install` for the first ``manage.py check`` run.
   :doc:`/content/faq/troubleshooting` for symptoms that map to individual ``next.*`` codes.
