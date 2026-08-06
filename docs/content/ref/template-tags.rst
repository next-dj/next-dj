.. _ref-template-tags:

Template tags
=============

Module summary
--------------

The framework registers its template tags as Django builtins through ``next.apps.templates.install``.
Templates therefore use them without an explicit ``{% load %}`` statement.

Forms
-----

.. describe:: {% form "<name>" attr="value" ... %}...{% endform %}

   Renders a form bound to a registered action.
   The first argument is the action name, a quoted string or a context variable that resolves to a string.
   Injects the ``csrfmiddlewaretoken`` CSRF field, and the ``_next_form_origin`` field carrying the URL path of the rendering page when an origin is available.
   The block body has access to the bound or unbound form through ``{{ form }}``.

   Optional ``attr="value"`` arguments after the action name render as HTML attributes on the ``<form>`` element, for example ``{% form "upload_form" class="stack" %}``.
   Attribute values are escaped, and an unquoted value resolves as a context variable.

   The opening tag emits its attributes in a fixed order.

   .. list-table::
      :widths: 100

      * - ``action`` with the dispatch URL.
      * - ``method="post"``.
      * - ``data-next-action`` with the action UID when the registry meta is available.
      * - The ``data-next-*`` attributes compiled from the partial params.
      * - ``enctype="multipart/form-data"`` when the form is multipart.
      * - The attributes passed to the tag.

   The ``enctype`` attribute is automatic for any form whose widgets need multipart encoding, so a file-upload form needs no extra argument.
   An explicit ``enctype="..."`` argument on the tag suppresses the automatic value and renders in the user-attribute position, for example ``{% form "upload_form" enctype="text/plain" %}``.

   The HTTP method is always ``post``.
   The tag owns the ``action`` and ``method`` attributes plus every attribute starting with ``data-next-``, and passing any of them raises ``TemplateSyntaxError`` at parse time.
   ``data-next-*`` is the single framework namespace in rendered markup.

   The ``validate``, ``trigger``, ``debounce``, ``zone``, and ``key`` params compile to ``data-next-*`` attributes, the authored seam for partial behaviour.
   Each param maps to the attribute of the same name except ``zone``, which compiles to ``data-next-target``.
   ``key`` distinguishes one instance of a repeated form, rendered in a loop, so a partial morph lands on the submitted instance rather than the first.
   See :doc:`/content/topics/partial-rendering/scenarios`.

   Captured URL parameters travel inside the origin path, the dispatcher recovers them by resolving ``_next_form_origin`` against the URLconf.

   The tag requires ``request`` in the template context for the CSRF token.
   It also uses ``current_page_module_path`` when present to scope the action lookup to the origin page, which is how the file router renders it.
   Inside a component's own template body ``current_component_module_path`` takes precedence, so a component-anchored action resolves before the page anchor.
   Neither context value is strictly required.
   When both are absent the action lookup falls back to the name index.

.. describe:: {% action_url "<name>" %}

   Returns the dispatch endpoint URL for a registered action.
   The first argument is the action name, a quoted string or a context variable that resolves to a string.
   The lookup uses the same anchor scoping as ``{% form %}``.
   A match for the component anchor wins over the page anchor, and either wins over a shared one.

   As a ``simple_tag`` it supports assignment, ``{% action_url "delete_note" as delete_url %}``.

   Use it for hand-written forms and client-side requests that post outside the ``{% form %}`` tag.
   Such a request supplies the CSRF token and the hidden ``_next_form_origin`` field itself, see the manual-form notes in :doc:`/content/topics/forms/templates`.

   An unknown name raises ``FormActionNotFoundError`` at render time, with the closest registered names in the message.
   An argument that resolves to an empty string raises ``FormActionNotFoundError`` with a hint to quote the literal name, since an unquoted name is read as a template variable.

Components
----------

.. describe:: {% component "<name>" key="value" ... %}

   Void form.
   Renders a component by name with the given literal string props.

.. describe:: {% #component "<name>" %}...{% /component %}

   Block form.
   Renders a component and substitutes child content through slots.

.. describe:: {% slot "<name>" %}

   Void form.
   Fills a named slot from the caller inside a ``{% #component %}`` block.

.. describe:: {% #slot "<name>" %}...{% /slot %}

   Block form.
   Fills a named slot with body content from the caller inside a ``{% #component %}`` block.

.. describe:: {% set_slot "<name>" %}

   Void form.
   Marks a slot location inside a component template, with no default body.

.. describe:: {% #set_slot "<name>" %}...{% /set_slot %}

   Block form.
   Marks a slot location inside a component template, with a fallback body used when the caller omits the slot.

Resolution misses
~~~~~~~~~~~~~~~~~

A ``{% component %}`` name that does not resolve has three outcomes, selected by ``NEXT_FRAMEWORK["STRICT_LOADING"]`` and ``settings.DEBUG``.

With ``STRICT_LOADING`` the tag raises ``TemplateSyntaxError`` at render time, so a typo fails the page rather than being silently dropped.
The message reads ``component 'card' not found from <path>, did you mean 'cards'?``, with the hint present when a close match exists among the component names visible to the rendering template.
A template rendered without a ``current_template_path`` context value has no discovery scope to search.
The strict message says so instead, ``component 'card' cannot be resolved because the template context has no current_template_path``.

With ``DEBUG`` and without ``STRICT_LOADING`` the miss renders as a visible HTML comment in place of the component, ``<!-- next: component 'card' not found (not-found), did you mean 'cards'? -->``.
The missing discovery scope renders as ``<!-- next: component 'card' skipped, no current_template_path in context (no-discovery-path) -->``.
The page keeps rendering while the gap stays visible in the markup.

With both off, the production default, the tag renders an empty string and logs a warning.
The production path never runs the did-you-mean machinery.

This is deliberately softer than the page-load contract described in :doc:`pages`.
A broken ``page.py`` fails the whole request because the page is the unit of response.
One missed component inside an otherwise healthy page degrades to a comment in development instead of taking the page down.

Multiline tag bodies
~~~~~~~~~~~~~~~~~~~~

The framework reinstalls Django's template tag pattern with the ``re.DOTALL`` flag so a single ``{% ... %}`` token may span several lines.
That allows readable block components and slots when the inner markup is long.

.. warning::

   This changes template parsing for **every** template the process loads, not only DJX files.
   If you rely on Django's stock behaviour where a newline inside ``{% ... %}`` ends the tag, adjust those templates before adopting next.dj.

Static pipeline
---------------

.. describe:: {% collect_styles %}

   Marks the placeholder slot where collected CSS link tags are injected.
   Takes no arguments.

.. describe:: {% collect_scripts %}

   Marks the placeholder slot where collected JS and module tags are injected.
   Takes no arguments.

.. describe:: {% use_style "<url>" %}

   Registers an external CSS URL on the active collector.
   The asset is prepended so shared dependencies load before co-located styles.

.. describe:: {% use_script "<url>" %}

   Registers an external JS URL on the active collector.
   The asset is prepended the same way as ``use_style``.

.. describe:: {% #use_style %}...{% /use_style %}

   Inline CSS block.
   The body is rendered with the template context and deduplicated by content.

.. describe:: {% #use_script %}...{% /use_script %}

   Inline JS block.
   The body is rendered with the template context and deduplicated by content.

Partial rendering
-----------------

.. describe:: {% zone "<name>" tag="<element>" lazy="<trigger>" poll="<interval>" %}...{% placeholder %}...{% endzone %}

   Marks a named slice of a page template the server can re-render on its own.
   The first argument is the quoted zone name, an ASCII slug that must be unique across the composed template, the layout chain plus the page body.
   On a full page render the body is wrapped in ``<div data-next-zone="<name>">`` so the client can address it, and a partial request re-renders only the body.

   ``tag="<element>"`` names the wrapper element, defaulting to ``div``.
   Use it where a ``<div>`` is invalid, for example ``tag="ul"`` inside a list or ``tag="tbody"`` inside a table.
   The wrapper carries ``data-next-zone`` whatever the tag.

   ``lazy="load"`` or ``lazy="revealed"`` defers the body.
   A lazy zone renders only its ``{% placeholder %}`` branch up front and fetches the body on ``ready`` for ``load`` or when it scrolls into view for ``revealed``.
   Any other ``lazy`` value raises ``TemplateSyntaxError`` at parse time.

   ``poll="<interval>"`` re-GETs the body on the interval, read from a quoted ``5s`` or ``1500ms`` literal or a bare number of milliseconds, never from a template variable.
   It is mutually exclusive with ``lazy=``, and an interval below one second, above the browser timer ceiling, or malformed raises ``TemplateSyntaxError`` at parse time.

   An option without ``=`` and an unknown option key also raise ``TemplateSyntaxError`` at parse time, so a typo fails the compile rather than being silently dropped.

.. describe:: {% placeholder %}

   Opens the placeholder branch of a lazy ``{% zone %}``, shown until the deferred body arrives.
   It is valid only between a ``{% zone %}`` and its ``{% endzone %}``, and a lazy zone without it raises ``next.E064``.
   The branch belongs to lazy zones only, so a zone without ``lazy=`` rejects it with ``TemplateSyntaxError`` when the template compiles.

A zone belongs to a page or layout, not a component, and may not sit inside a ``{% for %}`` or an ``{% if %}``.
A zone directly inside a ``{% with %}`` draws the ``next.W067`` warning because the bindings are invisible to a standalone zone render.
The :doc:`zone placement checks </content/ref/system-checks>` enforce each rule at ``manage.py check`` time.

Layouts
-------

.. describe:: {% block template %}{% endblock %}

   Marks the slot inside a ``layout.djx`` where the page template is composed.
   The layout loader replaces the empty block with the wrapped page body when it builds the final template string.
   Both ``{% endblock %}`` and ``{% endblock template %}`` are accepted as the closing tag.

   A ``layout.djx`` without this block raises ``next.W001`` during ``manage.py check``, since the page body would have nowhere to render.
   Nested layouts each carry their own ``{% block template %}`` and compose from innermost to outermost.

Tag loading
-----------

.. autofunction:: next.apps.templates.install
   :no-index:

The framework calls ``install`` during ``AppConfig.ready``.
Project code does not need to load the tag libraries manually.

See also
--------

.. seealso::

   :doc:`/content/topics/forms/templates` for the ``{% form %}`` tag.
   :doc:`/content/topics/components` for ``{% component %}`` and slots.
   :doc:`/content/topics/static-assets/template-tags` for the static tags.
   :doc:`/content/topics/partial-rendering/zones` for the ``{% zone %}`` tag.
