.. _topics-static-asset-kinds:

Asset Kinds
===========

An asset kind binds a file extension to a placeholder slot and to a backend renderer method.
The framework ships three kinds and lets projects register more.

.. contents::
   :local:
   :depth: 2

Built In Kinds
--------------

``register_defaults`` registers three kinds at startup through ``next.static.default_kinds``.

.. list-table::
   :header-rows: 1
   :widths: 20 25 25 30

   * - Kind
     - Extension
     - Slot
     - Renderer method
   * - ``css``
     - ``.css``
     - ``styles``
     - ``render_link_tag``
   * - ``js``
     - ``.js``
     - ``scripts``
     - ``render_script_tag``
   * - ``module``
     - ``.mjs``
     - ``scripts``
     - ``render_module_tag``

The static subsystem does not privilege CSS or JS in core code.
The three built in kinds register through the same public API that a project uses for a new kind.

The Registry
------------

The kind registry is ``next.static.default_kinds``, an instance of ``KindRegistry``.
A kind registration is keyed by the ``kind`` identifier and carries four pieces of metadata.

``kind``.
   The registry key, a non-empty Python identifier such as ``css`` or ``jsx``.

``extension``.
   The file suffix, starting with a dot, such as ``.jsx``.
   Discovery looks for files matching ``{stem}{extension}``.

``slot``.
   The name of the placeholder slot that buckets the asset at render time.
   The bundled slots are ``styles`` and ``scripts``.

``renderer``.
   The method name that the active static backend exposes for rendering this kind.
   The manager looks the method up with ``getattr`` on the backend per asset.

``inline_tag``.
   The optional HTML wrapper element for an inline body, such as ``"style"`` or ``"script"``.
   It defaults to verbatim, which wraps nothing and emits the inline body as written.

Registering a Kind
------------------

Register kinds in ``AppConfig.ready`` so the kind exists before the first request.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig
   from next.static import default_kinds

   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           default_kinds.register(
               "jsx",
               extension=".jsx",
               slot="scripts",
               renderer="render_module_tag",
           )

The ``jsx`` kind now lands in the ``scripts`` slot and renders through ``render_module_tag``.
The ``register`` call also accepts an optional ``inline_tag`` keyword, the HTML wrapper element such as ``"style"`` or ``"script"`` that wraps an inline body, defaulting to verbatim.
A repeated call with identical parameters is idempotent.
A repeated call with different parameters raises ``ValueError``.

Renderer Methods
----------------

The ``renderer`` value is a method name on the static backend.
The bundled ``StaticFilesBackend`` exposes three.

- ``render_link_tag`` for stylesheets.
- ``render_script_tag`` for scripts.
- ``render_module_tag`` for module scripts.

A custom kind reuses one of these methods, or a custom backend can add a new method.

.. code-block:: python
   :caption: notes/backends.py

   from next.static import StaticFilesBackend

   class BabelBackend(StaticFilesBackend):
       def render_babel_tag(self, url: str, *, request=None) -> str:
           return f'<script type="text/babel" src="{url}"></script>'

.. code-block:: python
   :caption: notes/apps.py

   default_kinds.register(
       "jsx",
       extension=".jsx",
       slot="scripts",
       renderer="render_babel_tag",
   )

The backend is registered through ``STATIC_BACKENDS``, see :doc:`backends`.
The renderer choice also decides whether the kind loads on a partial render, see the next section.

Renderers and Partial Rendering
-------------------------------

A full page render inserts an asset through its backend renderer method, which returns the HTML tag.
A partial render has no server-rendered tag to insert, so the patch envelope carries an asset manifest and the client runtime inserts the element itself.
The runtime needs to know which element to build, and the registered renderer is what tells it.

Each of the three bundled renderer methods stands for one client insertion verb.

.. list-table::
   :header-rows: 1
   :widths: 34 20 46

   * - Renderer method
     - Insertion verb
     - What the runtime inserts
   * - ``render_link_tag``
     - ``link``
     - A ``<link rel="stylesheet">`` awaited before the patch operations apply, or a ``<style>`` inserted synchronously ahead of them for an inline body.
   * - ``render_script_tag``
     - ``script``
     - A classic ``<script>``, run after the patch operations apply.
   * - ``render_module_tag``
     - ``module``
     - A ``<script type="module">``, run after the patch operations apply.

The verb travels in the ``load`` field of the manifest entry, see :doc:`/content/topics/partial-rendering/reference`.
A kind is loadable on a partial render when it registers one of those three renderers, whatever the kind is named.
The ``jsx`` kind registered above with ``render_module_tag`` therefore loads on a partial render exactly as ``module`` does.

The form the asset travels in decides the verb as well.
A URL-form asset takes the verb of its renderer.
An inline body keeps that verb only when the kind wraps it in the element the runtime builds, ``style`` for the ``link`` verb and ``script`` for the ``script`` verb.
The ``module`` verb builds a typed ``<script type="module">`` that no ``inline_tag`` names, so an inline body under ``render_module_tag`` carries no verb, and neither does the inline body of a kind registered without an ``inline_tag``.
Such a body reaches the browser on a full page render, which emits it verbatim, and is skipped on a partial render, so the two renders agree on the element that holds it.
A kind whose ``inline_tag`` names a different element than its renderer's verb builds keeps its URL form on a patch and leaves its inline bodies to the full render, which the ``next.W076`` check reports.

The rule the two sides follow reads in three parts.
The server derives the verb from the renderer registered for the kind and writes it into the entry.
A URL-form entry that reaches the client with no ``load`` field falls back to the verb implied by the name of a built-in kind, ``link`` for ``css``, ``script`` for ``js``, and ``module`` for ``module``, so an envelope authored before the field existed still loads.
An entry carrying an inline body takes no such fallback, and without an explicit ``load`` field it is dropped rather than wrapped in an element the full render would not build.

A kind registered with a custom renderer, such as the ``render_babel_tag`` example, carries no verb.
Its assets render on a full page render and are skipped on a partial render, because the runtime has no element to build for them.
The ``next.W074`` system check reports every such kind at ``manage.py check``.
Register the kind with one of the three bundled renderers when its assets must also arrive through a patch envelope.

Placeholder Slots
-----------------

A slot is the location where the static manager injects rendered tags.
The slot registry is ``next.static.default_placeholders``.
The framework registers two slots, ``styles`` and ``scripts``, each with an HTML comment token.

The Placeholder Registry
~~~~~~~~~~~~~~~~~~~~~~~~

``default_placeholders`` is an instance of ``PlaceholderRegistry``, exported from ``next.static`` alongside the ``PlaceholderSlot`` record.
A ``PlaceholderSlot`` binds a slot ``name`` to the HTML comment ``token`` that the matching ``{% collect_* %}`` tag emits.
The ``register`` method has the signature ``register(name, *, token)``.
A repeated call with the same token is idempotent, and a repeated call with a different token raises ``ValueError``.

Register a new slot when a kind should inject somewhere other than the standard two slots.

.. code-block:: python
   :caption: notes/apps.py

   from next.static import default_kinds, default_placeholders

   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           default_placeholders.register("preload", token="<!-- next:preload -->")
           default_kinds.register(
               "font",
               extension=".woff2",
               slot="preload",
               renderer="render_link_tag",
           )

The layout must contain the slot token, or a template tag that emits it, for the manager to find a place to inject.

Module Kind
-----------

The ``module`` kind renders ``<script type="module" src="...">`` through ``render_module_tag``.
Customise the rendered output through the ``module_tag`` key in the backend ``OPTIONS`` mapping, see :doc:`backends`.

The ``module`` kind carries no ``inline_tag``, so it renders an inline body verbatim, as do custom kinds registered without an ``inline_tag``.
An inline body of such a kind carries no insertion verb, so it arrives on a full page render only, while the URL form of the same kind still loads through a patch envelope.
The two renders agree on that split, because the client drops an inline entry that carries no verb instead of guessing one from the kind name.

System Checks
-------------

The static system checks ``next.W030``, ``next.W031``, and ``next.E036`` through ``next.E038`` validate the backend configuration.
The ``next.W042`` check validates the ``JS_CONTEXT_SERIALIZER`` setting.
The ``next.W074`` check walks the registered kinds and warns about each one whose renderer carries no client insertion verb.
The ``next.W076`` check walks the same kinds and warns about each one whose ``inline_tag`` is not the element its renderer's verb builds, so its URL form travels in a patch envelope while its inline bodies do not.
Both checks read the registry of the running process, so a kind no registration backs is outside their reach.
The ``next.W075`` check covers the init-payload keys the framework reserves rather than the kinds, see :doc:`js-context`.
No check validates the shape of a registration, because a bad call to ``default_kinds.register`` raises ``ValueError`` during ``AppConfig.ready``.
Because Django runs ``ready`` for every management command and during ASGI or WSGI worker boot, the exception aborts whatever process is starting up, not only ``manage.py check``.

Common Patterns
---------------

Custom asset kinds drive worked projects, including the ``kanban`` example with a ``.jsx`` kind and the ``live-polls`` example with a Vue single file component kind.
See :doc:`/content/misc/examples` for the runnable projects and their walkthroughs.

See Also
--------

.. seealso::

   :doc:`custom-stems` for recognising new filenames.
   :doc:`backends` for the renderer methods.
   :doc:`/content/howto/add-a-new-asset-kind` for a recipe.
   :doc:`/content/ref/static` for the public API.
