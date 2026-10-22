.. _topics-file-router:

File router
===========

The file router scans your project for ``page.py`` and ``template.djx`` files and generates Django URL patterns from the directory tree.

.. contents::
   :local:
   :depth: 2

Overview
--------

A directory under a configured page root becomes a URL segment.
A directory with a ``page.py`` becomes a navigable URL.
A directory with only a ``template.djx`` becomes a virtual route that renders static markup.
A bracketed segment such as ``[slug]`` is captured as a URL parameter and exposed to the page through the :doc:`dependency resolver <dependency-injection>`.

The router does not need an entry in ``urls.py`` per page.
Adding a directory adds a URL.
Renaming a directory renames the URL and its computed URL name.
Removing a directory removes the URL.

The name of the page root itself is the ``PAGES_DIR`` value of the backend entry, so it is whatever a project sets it to.
The examples on this page use ``pages``, the shipped default, and nothing below depends on which name a project picks.

When to use the file router
---------------------------

Use it when the URL tree and the directory tree should be the same tree.
A reader learns the shape of the site from the page root alone, a new URL costs one directory, and the layout chain of a page is its ancestor directories.

The cost is that a rename is a behaviour change.
Moving a directory changes the URL, the URL name, and the layout chain at the same time, and no static check finds a ``{% url %}`` call left behind.
Reach for a hand-written Django view when a URL must stay stable across a refactor of the tree that serves it, and see :ref:`topics-pages-plain-django-views` for how the two coexist in one project.

Route shapes
------------

The router recognises four routed directory shapes, and two more kinds of directory name route nothing, see `Route groups`_ and `Intercepting routes`_.

Plain segment.
   A directory name without brackets becomes a static URL segment.
   ``pages/blog/page.py`` answers ``/blog/``.

Captured segment.
   A directory wrapped in single brackets becomes a captured URL parameter.
   ``pages/posts/[slug]/page.py`` answers ``/posts/<str:slug>/``.

Typed captured segment.
   A captured directory with a converter prefix sets the Django path converter.
   ``pages/posts/[int:post_id]/page.py`` answers ``/posts/<int:post_id>/``.

Wildcard segment.
   A directory wrapped in double brackets becomes a ``path`` converter that swallows multiple URL segments.
   ``pages/api/[[suffix]]/page.py`` answers ``/api/<path:suffix>/``.
   A route holds at most one wildcard segment.
   A second ``[[name]]`` anywhere in the same trail leaves that page unrouted and reports :ref:`next.E028 <ref-system-checks>` at check time, whatever the two names are.

The following layout shows the four shapes together.

.. code-block:: text
   :caption: pages tree

   pages/
     page.py                       /
     blog/
       page.py                     /blog/
     posts/
       [slug]/
         page.py                   /posts/<str:slug>/
       [int:post_id]/
         page.py                   /posts/<int:post_id>/
     api/
       [[suffix]]/
         page.py                   /api/<path:suffix>/

Captured parameters
-------------------

The bracket syntax accepts every Django path converter.

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Bracket
     - Generated converter
     - When to use
   * - ``[name]``
     - ``<str:name>``
     - Any non empty value with no slash.
   * - ``[int:name]``
     - ``<int:name>``
     - Non-negative integers, including zero.
   * - ``[slug:name]``
     - ``<slug:name>``
     - URL slugs of ASCII letters, digits, hyphens, and underscores.
   * - ``[uuid:name]``
     - ``<uuid:name>``
     - Canonical UUID strings.
   * - ``[[name]]``
     - ``<path:name>``
     - Wildcard that matches one or more segments including slashes.
       A route holds at most one, see *Route shapes* above.

A bracket label is passed to Django verbatim, so any converter registered with :func:`django.urls.register_converter` works in ``[label:name]``, as :doc:`/content/internals/url-router` describes.

The ``[[name]]`` wildcard requires at least one character.
A request to the parent path with no trailing segment, such as ``/api/`` for an ``api/[[suffix]]/`` route, does not match, because the Django ``path`` converter never captures an empty string.

Captured values reach Python through markers.
``DUrl[T]`` parses the captured value into the requested type and provides it to context functions and action handlers.

Hyphens in directory names are normalised to underscores in the generated URL parameter and URL name.
A ``pages/[my-id]/page.py`` route becomes the Django parameter ``<str:my_id>``, the resolver provides it as ``my_id``, and the URL name registers as ``next:page_my_id``.
Name your directories without hyphens when you want the parameter name and the directory name to match exactly.
A directory name that is no Python identifier after that normalisation, such as ``[2fa]`` or ``[user.id]``, is refused with ``InvalidURLParameterError``.
``manage.py check`` reports it twice, as :ref:`next.E008 <ref-system-checks>` from the structural walk that reads the directory name and as :ref:`next.E082 <ref-system-checks>` from the pattern walk that tries to turn the route into a Django path.

The router does not abort on a refused route.
It logs a warning through the ``next.urls.backends`` logger that names the route and the offending ``page.py``, leaves that one page unrouted so it answers 404, and routes every other page.
The same holds for a repeated parameter name, refused with ``DuplicateURLParameterError``, so ``manage.py check`` runs and reports each refused page instead of failing on the URLconf.

.. code-block:: python
   :caption: pages/posts/[int:post_id]/page.py

   from notes.models import Note

   from next import context
   from next.urls import DUrl

   @context("note")
   def fetch_note(post_id: DUrl[int]) -> Note:
       return Note.objects.get(pk=post_id)

``DUrl[int]`` reads the captured segment whose name matches the parameter, so ``post_id`` resolves the ``[int:post_id]`` segment and the marker coerces it to ``int``.
A plain ``post_id: int`` arrives already parsed as well, because the URL kwargs provider coerces the captured segment to the parameter annotation, so ``DUrl`` is only needed to read a segment under a different name.
See :doc:`dependency-injection` for the full set of ``DUrl`` forms and the coercion table.

Virtual routes
--------------

A directory that contains a ``template.djx`` without a ``page.py`` still becomes a URL.
The router renders the template directly without invoking a Python page module.

.. code-block:: text
   :caption: pages tree

   pages/
     about/
       template.djx                /about/
     legal/
       privacy/
         template.djx              /legal/privacy/

Virtual routes are useful for marketing pages, static content, and quick mockups.
A virtual route can still receive layout wrapping from any ancestor ``layout.djx``.

A directory routes nothing until a page sits in it or below it, a ``page.py`` with a body or a ``template.djx``.
``manage.py check`` warns with ``next.W128`` about the outermost directory with no page and no action at any depth, one message for ``pages/a/`` however deep its empty subdirectories go.
The message names what the branch holds in vain, a ``layout.djx`` that wraps no page or a ``page.py`` without a body whose ``inherit=True`` access guards no page, or opens none for ``public(inherit=True)``, or whose ``inherit_context=True`` context reaches no page.
A private ``_name/`` directory and the ``COMPONENTS_DIR`` folder hold no pages, so they leave the branch empty.

Every view the router generates, virtual routes included, carries a ``next_page_path`` attribute naming the page source.
The form dispatcher reads it when it resolves a posted origin back to the page that should re-render after a validation failure, see :doc:`/content/topics/forms/validation-rerender`.

URL names
---------

Every page receives a URL name in the ``next`` namespace.
The name is computed from the URL path with the leading slash removed and each segment separated by an underscore.
Captured segments contribute their parameter name without the brackets.

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - File
     - URL name
   * - ``pages/page.py``
     - ``next:page_``
   * - ``pages/blog/page.py``
     - ``next:page_blog``
   * - ``pages/posts/[slug]/page.py``
     - ``next:page_posts_slug``
   * - ``pages/posts/[int:post_id]/page.py``
     - ``next:page_posts_int_post_id``
   * - ``pages/api/[[suffix]]/page.py``
     - ``next:page_api_suffix``

The trailing underscore on the root page name is intentional.
``reverse('next:page_')`` resolves the root page.

A tree a URLconf places through ``pages()`` names its pages in a namespace of its own and without the prefix, see `Placing application trees`_.

A typed captured segment keeps its converter label in the URL name.
``[int:post_id]`` becomes ``posts_int_post_id``, not ``posts_post_id``, because the name is computed from the raw segment text.

The ``page_`` prefix comes from the ``URL_NAME_TEMPLATE`` setting.
Its default is ``page_{name}``, where ``{name}`` is the underscore-joined path computed above.
Set it to change the prefix for every file-routed page at once.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "URL_NAME_TEMPLATE": "route_{name}",
   }

With this value ``pages/blog/page.py`` registers as ``next:route_blog``.
The placeholder ``{name}`` must appear in the template so each page still gets a distinct name.

Reverse them through the standard ``{% url %}`` tag or with ``page_reverse``.
See :doc:`url-reversing` for the Python side.

Page roots
----------

The router resolves routes from application directories and from project directories listed in ``DIRS``.
A third shape covers the project that has neither, and it is described last.

App directories.
   When ``APP_DIRS`` is ``True`` the router scans each installed application for a directory named ``PAGES_DIR``.
   ``PAGES_DIR`` is required on every ``FileRouterBackend`` entry, and the ``next.E024`` system check fails when the key is missing.
   A backend that is not a file router carries only ``BACKEND``, and any other key on that entry reports ``next.E035``.
   In the tutorial it is set to ``pages``, so the router scans ``notes/pages/``.
   Applications are resolved through Django's application registry, so an ``INSTALLED_APPS`` entry written as an ``AppConfig`` path such as ``notes.apps.NotesConfig`` scans the same ``notes/pages/`` directory as the plain ``notes`` entry, and both are reported as ``App 'notes'``.
   The scanned directory is the one the registry reports for the application, so an ``AppConfig`` that declares its own ``path`` moves the scan with it, and an application shipped as a namespace package is scanned like any other.
   Django's own applications and ``next`` itself are never scanned, so the packages they ship cannot contribute routes.

   A directory reachable both as an application tree and through ``DIRS`` is routed twice, once under each root, which the ``next.E015`` system check reports as a URL pattern conflict.
   The page checks report each file once, however many roots reach it.

Project directories.
   The ``DIRS`` list adds absolute or project-relative paths to the scan.
   The router walks each directory in order and registers every ``page.py`` and ``template.djx`` it finds.

Fallback root.
   With ``APP_DIRS`` set to ``False`` and no usable path entry in ``DIRS``, the router falls back to scanning ``BASE_DIR / PAGES_DIR``.
   This shape serves a project that keeps every page in a single tree without a page-bearing application.

.. code-block:: python
   :caption: config/settings.py, pages without applications

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": False,
               "DIRS": [],
               "PAGES_DIR": "pages",
           }
       ]
   }

With this configuration the router serves every page under ``<BASE_DIR>/pages/``.

You can use both sources at once.
URL patterns are built in this order, first from application directories then from each entry in ``DIRS``, and a tree that ``pages()`` places under a prefix leads both, as *Route order across trees* below describes.
If two routes convert to exactly the same Django path string the system check ``next.E015`` reports the conflict, whether they come from one tree or several.
The comparison is string equality after bracket conversion, so semantic overlap between typed converters stays silent, and ``posts/[int:id]`` next to ``posts/[id]`` is not reported.

.. code-block:: python
   :caption: config/settings.py

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "DIRS": [str(BASE_DIR / "chrome")],
               "PAGES_DIR": "pages",
               "OPTIONS": {
                   "context_processors": [
                       "myapp.context_processors.global_context",
                   ],
               },
           }
       ]
   }

The ``OPTIONS`` block accepts a list of Django context processor paths.
Each processor contributes values to every template that the router renders.

DIRS entry types
~~~~~~~~~~~~~~~~

Each entry in ``DIRS`` is classified by ``next.utils.classify_dirs_entries`` before the router uses it.
``DIRS`` itself is a sequence of such entries, and a bare string in its place raises ``next.errors.InvalidDirsError`` rather than splitting into one entry per character.

Path entry.
   An absolute path, or a relative path that resolves to an existing directory under ``settings.BASE_DIR``.
   The router walks this directory as an additional page root alongside the application directories.

Segment entry.
   A plain string such as ``"api"`` or ``"drafts"`` that does not resolve to an existing directory.
   The router adds it to the set of directory names it skips during the file walk, preventing those directories from becoming URL segments.
   A name that starts with an underscore needs no entry, because such a directory is private already, see *Private folders* below.

.. code-block:: python
   :caption: skipping a directory by name

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": ["drafts"],
               "APP_DIRS": True,
               "PAGES_DIR": "pages",
           }
       ]
   }

In the example above, any directory named ``drafts`` under any application's page root is silently skipped.
No URL is registered for it and the file walk does not descend into it.

.. _topics-file-router-trees:

Placing application trees
-------------------------

An application page tree routes at one of two places.

Root.
   By default the ``PAGES_DIR`` tree of every installed application routes from the point where the root URLconf includes ``next.urls``, so the first directory of the tree is the first URL segment.

URLconf.
   The project places one tree anywhere in a ``urls.py`` through ``pages()``, and that tree leaves ``next.urls``.

``manage.py routes`` prints the result, one row per routed page with its URL, name, file, and access, see :doc:`/content/ref/management`.

The pages() include
~~~~~~~~~~~~~~~~~~~

``pages()`` from ``next.urls`` returns what :func:`~django.urls.include` returns, so :func:`~django.urls.path` places one application tree under any route of the project.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   from next.urls import pages

   urlpatterns = [
       path("staff/", pages("audit")),
       path("staff/history/", pages("audit", "history", namespace="audit_history")),
       path("", include("next.urls")),
   ]

The first argument is the label or the dotted name of an installed application, and the optional second names one of its subdirectories as one plain directory name.
Without it ``pages()`` places the ``PAGES_DIR`` tree of the application, with ``PAGES_DIR`` read from the first file router of ``PAGE_BACKENDS``.
A subdirectory other than ``PAGES_DIR``, such as ``history/`` above, routes only through a ``pages()`` include.
Both arguments resolve while the URLconf imports, the way ``include()`` fails on a missing module.
An unknown application raises ``LookupError``, and a subdirectory that is missing, holds a separator, or is ``""``, ``"."``, or ``".."`` raises ``ImproperlyConfigured``.

.. code-block:: text
   :caption: audit application

   audit/
     apps.py
     pages/
       page.py                     /staff/
       log/
         page.py                   /staff/log/
     history/
       page.py                     /staff/history/

A converter in the route above ``pages()`` reaches every page of the tree as a URL kwarg, as it does for any :func:`~django.urls.include`, through a plain parameter or ``DUrl``.
A page directory that captures a name the route already captures, such as ``history/[int:wallet]/`` under ``path("w/<int:wallet>/", pages(...))``, makes one value overwrite the other, and the checks report :ref:`next.E152 <ref-system-checks>`, naming the page and the include.

The pages of a ``pages()`` tree register in a namespace named after the application label, without the outer prefix.
``page_reverse("", namespace="audit")`` answers ``/staff/`` and ``{% url "audit:page_log" %}`` answers ``/staff/log/``.
A second tree of the same application needs a namespace of its own, which the ``namespace`` keyword sets, as ``audit_history`` does above.
A layout, a context, an access declaration, or a breadcrumb of one tree never reaches the pages of another, see :ref:`topics-access-tree-boundary` and :doc:`/content/topics/seo/breadcrumbs`.

One tree may be included twice, each include under a namespace of its own, such as ``pages("audit", namespace="one")`` under ``one/`` and ``pages("audit", namespace="two")`` under ``two/``.
Its pages then answer under both prefixes, while the sitemap lists the tree as one section, built from the include the URLconf lists first, so only the URLs under ``one/`` appear in it.
The checks ``next.W126``, ``next.E154``, and ``next.E162`` judge such a tree by that first include alone, its route and the kwargs it captures.

The router finds every ``pages()`` include by walking the URLconf ``ROOT_URLCONF`` names, nested ``include()`` calls included, so an include in a module that URLconf does not reach takes no tree out of ``next.urls``.
Under :func:`~django.conf.urls.i18n.i18n_patterns` the walk reads the language prefix in ``LANGUAGE_CODE``, so the routes it records, and the URLs ``manage.py routes`` prints, carry the prefix of that language.
The routes of ``include("next.urls")`` follow a new ``ROOT_URLCONF`` only once the routers reload, which :func:`~django.test.override_settings` triggers and pytest-django's ``@pytest.mark.urls`` does not, see :ref:`topics-testing-urlconf`.

Route order across trees
~~~~~~~~~~~~~~~~~~~~~~~~

Inside ``next.urls`` the application trees route first, in ``INSTALLED_APPS`` order, then the ``DIRS`` roots.
A ``pages()`` tree routes where its URLconf lists it, under Django's first-match rule, so a ``[slug]`` or ``[[rest]]`` page at the root of ``next.urls`` never shadows a tree a ``pages()`` include placed above it.

.. _topics-file-router-private-folders:

Private folders
---------------

A directory whose name starts with an underscore is private.
The router never enters it, so nothing below it becomes a URL, and the page checks skip it too.
``pages/_drafts/page.py`` routes nowhere, and a private folder at any depth keeps its whole subtree out of the URL map.

The default components folder ``_components`` is one such directory, and the router registers it as a component root while it skips it as a route.
The router shares its file walk with the components backend, and the folder name comes from ``COMPONENTS_DIR`` on the first ``COMPONENT_BACKENDS`` entry.
A ``COMPONENTS_DIR`` without a leading underscore is skipped as a route all the same, by its exact name.

A ``COMPONENT_BACKENDS`` list that is empty, or whose first entry carries no ``COMPONENTS_DIR``, leaves the router with no name to skip, and every file-router entry then refuses to build.
The manager logs that refusal and skips the entry, so the symptom is a project that serves no file-routed page at all.

.. _topics-file-router-route-groups:

Route groups
------------

A directory named with an identifier in parentheses is a route group.
``(auth)`` and ``(marketing-site)`` are groups.
The name starts with a letter or an underscore and continues with letters, digits, underscores, or hyphens.
Any other parenthesised name reports :ref:`next.E169 <ref-system-checks>`, and the router routes it literally, parentheses included, or skips it when it opens with ``(.`` like a malformed intercept.

A group adds nothing to the URL and nothing to the URL name.
``pages/(auth)/login/page.py`` answers ``/login/`` and registers as ``next:page_login``, so moving a page into or out of a group changes neither.
Reversing the group spelling, such as ``page_reverse("(auth)/login")``, raises.

.. code-block:: text
   :caption: pages tree

   pages/
     (auth)/
       layout.djx
       login/
         page.py                     /login/
       logout/
         page.py                     /logout/
     (app)/
       page.py                       /
       [int:id]/
         page.py                     /<int:id>/
       new/
         page.py                     /new/

The router flattens a group into the level that holds it.
The pages of the directory come first, then the pages of its groups in name order with nested groups depth first, then the children.
The groups share one level with their siblings, so ``(app)/page.py`` serves the URL of ``pages/`` itself.
A group page with a body that sits beside a page of its parent serves the same URL, and two groups that route one trail do the same.
Both report :ref:`next.E015 <ref-system-checks>`, whose hint explains that a group leaves the URL.

What a group scopes follows the file system and not the URL.

Layouts.
   A ``layout.djx`` in a group wraps every page below the group.
   A group with a ``{% layout root %}`` layout gives its pages a document of their own, see :ref:`topics-layouts-root`.

Access and context.
   An ``inherit=True`` access rule and an ``inherit_context=True`` context of a group ``page.py`` reach every page below the group, see :doc:`/content/topics/access` and :doc:`/content/topics/context`.
   A group ``page.py`` without a body routes nothing, and its declarations still fold into the pages below.

Components.
   A ``_components`` folder in a group is visible below the group only.
   The root ``_components`` folder stays global, see :doc:`/content/topics/components`.

A private folder keeps its meaning around a group.
``_private/(x)`` is never routed and ``(x)/_private`` is skipped.
A group may sit under a captured segment and a captured segment may sit under a group.
A group at the top of a ``pages()`` tree adds nothing to the prefix of the include.

Breadcrumb and sitemap trails are URL trails, so they hold no group.
A group ``page.py`` gives no crumb of its own, and its title and other metadata still fold into the pages below.
A breadcrumb declared in the ``page.py`` of a group reports ``next.W136``, see :doc:`/content/topics/seo/breadcrumbs`.
``@sitemap.items`` names the URL trail without the group.

A static logical name keeps the group.
``pages/(auth)/login/page.css`` is named with ``(auth)`` in its path, so moving a page into a group renames its assets while the URL stays.
Two groups can each hold a ``layout.css`` without a collision, see :doc:`/content/topics/static-assets/name-resolution`.

``manage.py check`` reports ``next.W137`` for a group that holds one routed directory and nothing else, since the group changes nothing.
An empty group reports ``next.W128``, as any empty directory does.
:doc:`/content/ref/system-checks` lists every code.

.. _topics-file-router-intercepts:

Intercepting routes
-------------------

An intercepting route is a directory that names the page it opens over.
It lets a link open the page of another URL, such as a photo, as a modal over the current page while the URL stays the honest URL of that page.

The directory name is one marker followed by the segment it targets.
The marker is ``(.)``, ``(..)`` repeated, or ``(...)``, and the kinds never mix.
A bare marker or a mixed one reports ``next.E169``.

The scope of an intercept is the URL trail, without groups, of the directory that holds the intercept directory.
The marker counts levels from that scope.

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Marker
     - Directory
     - Target trail
   * - ``(.)``
     - ``feed/(.)photo/``
     - ``feed/photo``
   * - ``(..)``
     - ``feed/(..)photo/``
     - ``photo``
   * - ``(..)(..)``
     - ``a/b/(..)(..)photo/``
     - ``photo``
   * - ``(...)``
     - ``feed/(...)photo/``
     - ``photo``

A ``(..)`` that climbs above the tree root reports ``next.E171`` and the router drops the intercept.
``(...)`` always starts at the root of the page tree that holds the intercept.

The subtree below an intercept directory walks with the trail seeded to the target.
``feed/(.)photo/[id]/page.py`` targets ``feed/photo/[id]``.

.. code-block:: text
   :caption: pages tree

   pages/
     feed/
       page.py                       /feed/
       photo/
         [id]/
           page.py                   /feed/photo/<str:id>/
       (.)photo/
         [id]/
           page.py                   opens over /feed/photo/<str:id>/

The target must match a routed trail of the same page tree exactly.
``[id]`` does not match ``[int:id]``, and a trail from another tree or a ``pages()`` mount does not match either.
An intercept with no target reports ``next.E170``, and the hint says why.
A group inside an intercept directory flattens as anywhere else, and a group around an intercept leaves the scope.
An intercept directory inside another intercept directory is never read and reports ``next.E172``.

An intercept is never routed.
It holds no URL pattern, no URL name, no ``next.E015`` conflict, and no sitemap entry.
The router imports its ``page.py``, so the context, the metadata, the actions, and the access rules it declares register like those of any page.

An intercept answers a zone request of its target.
A link such as ``<a href="/feed/photo/7/" data-next-layer="photo-modal">`` asks for the target URL with the zone name, and the server renders the zone of the first intercept that declares it, unless the target declares that zone itself.
The page of the intercept renders the zone alone, with no layout around it, so a ``layout.djx`` inside an intercept directory reports ``next.W139``.
A full request of the same URL renders the real target page.
:ref:`topics-partial-rendering-layers` covers the layer that hosts the zone and :doc:`/content/howto/open-a-page-as-a-modal-over-another` walks through a recipe.

An intercept whose template fails to compose is skipped when a zone is requested, and ``next.E072`` reports it.
Intercept pages are part of discovery, so ``next.E017``, ``next.E072``, and the ``DObject`` checks cover them against the kwargs of the target, and their actions register with the rest.
The development server restarts when an intercept page is added, removed, or renamed.

The access chains of the target and of the intercept both run on a zone request, and a denial of either is the response.
:doc:`/content/ref/system-checks` lists the checks of intercepts, ``next.E170`` to ``next.E176`` and ``next.W138`` to ``next.W140``.

Multiple backends
-----------------

The settings list accepts more than one backend.
Each backend can read from a different directory, register a different ``PAGES_DIR``, or use a custom subclass.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "PAGES_DIR": "pages",
           },
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "PAGES_DIR": "admin_pages",
           }
       ]
   }

Two backends produce two independent sets of URLs.
Resolution preserves the concatenated list order.
The first match wins.
Both backends emit the same signals and follow the same naming rules.

Route order
-----------

Within one tree a static directory always routes before a parameter sibling, and a parameter sibling before a catch-all.
``pages/blog/about/`` therefore answers ``/blog/about/`` even though ``pages/blog/[slug]/`` matches that path too.
Among siblings of the same kind the more specific one routes first, ``post-[id]`` before ``[slug]`` and ``[int:id]`` before ``[str:key]``, and the name decides the rest, so the order is the same on every file system.
A route group flattens into the level that holds it before the ranking, so the children of a group rank with their siblings and ``(app)/[id]/`` does not shadow ``new/``.
A child of the directory itself stays ahead of a group child of equal rank, and an intercept directory never ranks because it routes nothing.
Directories of one segment name, in the plain tree and in groups, rank their children together, so ``(admin)/shop/new/`` still wins over ``shop/[slug]/``.
Inside ``next.urls`` the framework endpoints under ``/_next/`` route ahead of every page, so a root ``[[rest]]`` page of ``next.urls`` cannot swallow them, while a ``pages()`` tree listed above the include can, which ``next.E149`` reports.
See :doc:`/content/internals/url-router` for the complete rule.

Resolution performance
----------------------

URL resolution scales with the depth of the request path, not with the number of registered pages.
A static route resolves through one dictionary lookup, and a parameterised route resolves through a walk over a trie of path segments that yields a handful of candidates.
The final match runs through standard Django pattern resolution, so converters and first-match-wins ordering behave as in plain Django.
Setting ``URL_RESOLVER`` in ``NEXT_FRAMEWORK`` to ``"django.urls.resolvers.URLResolver"`` restores the plain linear scan.
See :doc:`/content/internals/url-router` for the algorithm and :doc:`/content/ref/settings` for the setting.

Common patterns
---------------

Single page application root
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A single ``pages/page.py`` registers the empty path ``/``.
The router treats it as the default URL for the project.

Static content section
~~~~~~~~~~~~~~~~~~~~~~

Use virtual routes for marketing pages and legal copy.
The directory holds only a ``template.djx``, no Python required.

Per project page tree
~~~~~~~~~~~~~~~~~~~~~

Place a layout and one ``page.py`` under ``chrome/`` and add ``chrome`` to ``DIRS``.
The result is a project-level shell that wraps every application page.
See :doc:`multi-project` for the full pattern.

Hot reload
----------

A backend that reads from a database or other dynamic source needs to rebuild its pattern list when the data changes.
``router_manager.reload()`` rebuilds every backend and clears the Django URL resolver cache, and the call is idempotent.
The rebuild emits ``router_backend_loaded`` once per instantiated ``PAGE_BACKENDS`` entry, with the resolved router class as sender and the entry copy and the built instance as payload.
Each invocation then emits a ``router_reloaded`` signal with the manager class as sender, so long lived processes can listen for it to refresh cached URL references.
A receiver of that signal that rebuilds again calls ``router_manager.reload(notify=False)``, which skips ``router_backend_loaded``, the URL-cache clear, and ``router_reloaded``, so the receiver does not re-enter itself.

A reload also happens without an explicit call.
The framework holds a :data:`~django.test.signals.setting_changed` receiver that reloads every router when ``INSTALLED_APPS`` or ``ROOT_URLCONF`` moves, because a file router caches its patterns per application name and reads its ``pages()`` includes from the root URLconf.
A test that swaps the application list or the URLconf with :func:`~django.test.override_settings` therefore gets the routes of the swapped value rather than the ones built before it.
See :doc:`/content/internals/url-router` for the full reload order and :doc:`/content/ref/signals` for the signal payloads.

Rebuilding drops the cached patterns, so the next request walks every page tree configured in ``PAGE_BACKENDS`` again.
A burst of model writes that each triggers a reload can dominate that request.
Receivers should debounce or batch invocations when one logical change triggers many model signals at once.
See :doc:`/content/howto/reload-routes-from-code` for the model-signal receiver that triggers the reload.

System checks
-------------

The router contributes Django system checks that validate the configuration at startup, alongside two page checks that ``next.pages`` registers over the same tree the router scans.

- ``check_next_pages_configuration`` validates the ``NEXT_FRAMEWORK`` structure and each backend entry.
- ``check_pages_structure`` validates directory naming, captured parameter syntax, and the presence of ``page.py`` or ``template.djx``.
- ``check_page_functions`` reports :ref:`next.E012 <ref-system-checks>` when a ``page.py`` that declares no access has no render function, no template attribute, and no loader-backed source, a ``layout.djx`` beside or above it counting for none.
- ``check_url_patterns`` reports two routes that convert to exactly the same Django path string, whether they come from one tree or several (:ref:`next.E015 <ref-system-checks>`).
  It compares full routes, the prefixes of ``pages()`` includes too, so two trees placed onto the same URL are reported as well.
  The comparison is string equality after bracket conversion, so ``posts/[int:id]`` next to ``posts/[id]`` is not reported.
  While collecting patterns it also reports a route that repeats a captured parameter name (:ref:`next.E028 <ref-system-checks>`), listing every conflicting name, so parameter duplicates surface from every scanned tree.
  A page that repeats a name the ``pages()`` prefix of its tree captures reports :ref:`next.E152 <ref-system-checks>` instead.
- ``check_reverse_name_collisions`` fails when two distinct routes collapse to the same reverse URL name after separator normalisation, such as ``foo-bar`` next to ``foo_bar`` (:ref:`next.E039 <ref-system-checks>`).
  Names collide only within one namespace, so the pages of a ``pages()`` tree are compared with each other and not with ``next``.

- The group and intercept checks report an unreadable parenthesised name (:ref:`next.E169 <ref-system-checks>`), a pointless group (``next.W137``), and an intercept with no target, past the root, or nested (``next.E170``, ``next.E171``, ``next.E172``), see `Route groups`_ and `Intercepting routes`_.

Run them through ``uv run python manage.py check``.
A clean exit confirms that every page resolves and every name is unique.

Extension points
----------------

Two surfaces let you replace or augment the router.

- ``next.urls.RouterBackend`` is the abstract contract for any source of URL patterns.
- ``next.urls.FileRouterBackend`` is the default file-based implementation.

A custom backend is named by its dotted path under ``PAGE_BACKENDS``, exactly as every other backend family is named, and it takes that entry as its single constructor argument.

Subclass ``FileRouterBackend`` to add additional patterns or augment URL names without writing a backend from scratch.
See :doc:`extending` for a worked example.

Database driven routes
----------------------

A hybrid backend combines file routes with routes built from database rows.
Subclass ``FileRouterBackend`` and override ``generate_urls`` to call ``super().generate_urls()`` for the file routes, then append one named pattern per row.
The base method returns a fresh list on every call, so appending to it never mutates the cached file routes.
Register the backend in ``PAGE_BACKENDS`` and call ``router_manager.reload()`` from a model signal so the row-derived patterns rebuild when the data changes.

See :doc:`/content/howto/write-a-router-backend` for the full worked recipe.

See also
--------

.. seealso::

   :doc:`url-reversing` for building URLs in Python and templates.
   :doc:`/content/howto/add-a-page` for a one page recipe.
   :doc:`/content/howto/serve-an-application-under-a-prefix` for a tree under a prefix.
   :doc:`project-layout` for the files of an application and the trees of a growing project.
   :doc:`access` for the access a ``page.py`` declares over a directory of a tree.
   :doc:`/content/howto/reload-routes-from-code` for hot reload mechanics.
   :doc:`/content/internals/url-router` for the parser, dispatcher, and signal flow.
   :doc:`/content/ref/urls` for the public API.
