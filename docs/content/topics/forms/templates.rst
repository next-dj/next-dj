.. _topics-forms-templates:

Form Templates
==============

The ``{% form "name" %}`` block tag renders a ``<form>`` element, injects the CSRF token, and publishes the form instance inside the block body.

.. contents::
   :local:
   :depth: 2

The Form Tag
------------

.. code-block:: jinja
   :caption: template.djx

   {% form "article_edit_form" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Save</button>
   {% endform %}

The first argument is the action name as a quoted string or a context variable that resolves to a string.
An opening tag without the action name raises ``TemplateSyntaxError`` at parse time.
Optional ``key="value"`` arguments after the name render as HTML attributes on the ``<form>`` element (see `HTML Attributes`_ below).

The tag does the following.

1. Looks up the action name in the registry, preferring a page-scoped match for the current page, then falling back to shared scope.
2. Resolves the stable dispatch URL for that action.
3. Emits ``<form action="..." method="post">`` plus any attributes passed to the tag.
4. Emits a hidden ``csrfmiddlewaretoken`` input.
5. Emits a hidden ``_next_form_origin`` input set to ``request.path``, used by ``redirect_to_origin`` on success and resolved through the URLconf on the error re-render.
6. Publishes ``form`` inside the block body (see `The form Variable`_ below).

On the validation-error re-render the request targets the dispatch endpoint, so the tag re-emits the posted origin of the original page instead of ``request.path``.

A name that is not in the registry raises ``FormActionNotFound`` at render time.

HTML Attributes
---------------

Every ``key="value"`` argument after the action name lands on the ``<form>`` element.
A file-upload form sets the encoding type directly on the tag.

.. code-block:: jinja
   :caption: extra attributes

   {% form "attachment_form" enctype="multipart/form-data" class="stack" %}
     {{ form.file }}
     <button type="submit">Upload</button>
   {% endform %}

Attribute values are escaped, and an unquoted value resolves as a context variable.
The ``action`` and ``method`` attributes belong to the tag, and passing either raises ``TemplateSyntaxError`` at parse time.

Scope Resolution
----------------

When the template renders inside a page, the tag first looks for a page-scoped registration whose file matches the current ``page.py``.
If no page-scoped match exists the tag falls back to the shared registry.
This means a page-local ``NoteForm`` takes precedence over a shared ``NoteForm`` with the same derived name.

The ``form`` Variable
---------------------

Inside the block body the variable ``form`` holds the form instance.

Initial render (GET).
   The framework calls ``get_initial`` through the dependency resolver, constructs an unbound form from the returned data or instance, and publishes it as ``form``.

Re-rendered page after a failing POST.
   The variable is the bound form with validation errors attached.
   The template renders the user input plus any error messages without branching.

Form-less action.
   When the action is a plain function registered with ``@action`` (no form class), ``form`` resolves to ``None``.
   The block body should not attempt to render field widgets in this case.

Captured URL Parameters
-----------------------

The tag does not need any extra argument to forward URL parameters.
The captured kwargs travel inside the origin path itself: the dispatcher resolves the posted ``_next_form_origin`` against the URLconf and recovers every kwarg through the real URL converters, skipping names reserved by the dependency resolver.

.. code-block:: jinja
   :caption: page for /notes/<int:note_id>/

   {% form "note_form" %}
     {{ form.title }}
     <button type="submit">Save</button>
   {% endform %}

A form rendered under ``/notes/42/`` posts ``_next_form_origin`` set to that path, and resolving it yields ``note_id=42`` as an ``int``.
The handler receives the value through ``DUrl["note_id", int]``, typed identically on the canonical GET and on the re-render.

Multiple Forms on One Page
--------------------------

Each ``{% form %}`` call references a different action name.
The dispatcher routes submissions by URL alone, so the forms do not interfere.

.. code-block:: jinja
   :caption: page with two forms

   {% form "note_form" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Save</button>
   {% endform %}

   {% form "delete_note" %}
     <button type="submit" class="danger">Delete</button>
   {% endform %}

``delete_note`` is a form-less action.
The second block has no ``{{ form }}`` usage because ``form`` is ``None``.

Rendering Field Errors
----------------------

Errors live on the bound form.
Render them inline or as a summary at the top.

.. code-block:: jinja
   :caption: inline errors

   {% form "note_form" %}
     <div>
       {{ form.title }}
       {% if form.title.errors %}
         <p class="error">{{ form.title.errors|first }}</p>
       {% endif %}
     </div>
     <button type="submit">Save</button>
   {% endform %}

.. code-block:: jinja
   :caption: error summary

   {% form "note_form" %}
     {% if form.errors %}
       <ul>
         {% for field, messages in form.errors.items %}
           {% for message in messages %}<li>{{ field }} — {{ message }}</li>{% endfor %}
         {% endfor %}
       </ul>
     {% endif %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Save</button>
   {% endform %}

Manual CSRF
-----------

The tag emits ``csrfmiddlewaretoken`` automatically.
Only add Django's ``{% csrf_token %}`` manually when you build the ``<form>`` element by hand and skip the tag entirely.
A hand-crafted form must also include the ``_next_form_origin`` hidden field or the dispatcher cannot re-render on failure.
Set it to the URL path of the page, the same value the tag emits, with ``{{ request.path }}`` as the natural source.

.. code-block:: jinja
   :caption: hand-crafted form

   <form action="/_next/form/{{ action_uid }}/" method="post">
     {% csrf_token %}
     <input type="hidden" name="_next_form_origin" value="{{ request.path }}">
     <button type="submit">Send</button>
   </form>

.. _topics-forms-templates-handwritten-views:

Forms in Hand-Written Views
---------------------------

A ``{% form %}`` tag also works inside a template rendered by an ordinary Django view, outside the file router.
The success path needs nothing extra, the handler runs and its response goes out.
The error re-render is different: the dispatcher resolves the posted origin to a view and reads the page source location from its ``next_page_path`` attribute, which the file router sets on every routed view and a hand-written view lacks.
Without it an invalid submission returns HTTP 400 instead of re-rendering.

Opt in by setting the attribute on the view function yourself, as a ``Path`` or a string naming the ``page.py`` location whose body the dispatcher should compose on the error re-render.
The file may be a real page module or a synthesised location next to a ``template.djx``, exactly as for virtual routes.

.. code-block:: python
   :caption: notes/views.py

   from pathlib import Path

   from django.shortcuts import render

   def feedback(request):
       return render(request, "feedback.html")

   feedback.next_page_path = Path(__file__).parent / "feedback" / "page.py"

Common Patterns
---------------

Form in a Component
~~~~~~~~~~~~~~~~~~~

A component template hosts ``{% form %}`` exactly like a page template.
The framework injects ``current_page_module_path`` from the surrounding page, so the action lookup scopes to the correct page.

Form in a Layout
~~~~~~~~~~~~~~~~

Layouts receive ``current_page_module_path`` from the page they wrap.
A login form placed in the root layout posts the wrapped page's path as its origin, so a validation failure re-renders the original page.

Render-Time Failures
--------------------

The tag raises ``ImproperlyConfigured`` when ``request`` is absent from the template context.
Add ``django.template.context_processors.request`` to ``TEMPLATES[*].OPTIONS.context_processors`` to make the request available.

The ``next.E019`` system check, described in :doc:`/content/security/csrf-and-forms`, catches the missing context processor before a request reaches the tag.

See Also
--------

.. seealso::

   :doc:`actions` for auto-registration and the handler side of the contract.
   :doc:`validation-rerender` for what runs after a failing submission.
   :doc:`/content/ref/template-tags` for every template tag the framework registers.
