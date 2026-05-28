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

The tag accepts exactly one argument: the action name as a quoted string or a context variable that resolves to a string.
An opening tag with the wrong number of arguments raises ``TemplateSyntaxError`` at parse time.

The tag does the following.

1. Looks up the action name in the registry, preferring a page-scoped match for the current page, then falling back to shared scope.
2. Resolves the stable dispatch URL for that action.
3. Emits ``<form action="..." method="post">``.
4. Emits a hidden ``csrfmiddlewaretoken`` input.
5. Emits a hidden ``_next_form_origin`` input set to ``request.path``, used by ``redirect_to_origin``.
6. Emits a hidden ``_next_form_page`` input with the absolute path to the current ``page.py``, used on re-render.
7. Publishes ``form`` inside the block body (see `The form Variable`_ below).

A name that is not in the registry raises ``RuntimeError`` at render time.

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
At render time it reads ``request.resolver_match.kwargs`` and emits a hidden ``_url_param_<name>`` field for every captured kwarg, skipping ``uid`` and names reserved by the dependency resolver.

.. code-block:: jinja
   :caption: page for /notes/<int:note_id>/

   {% form "note_form" %}
     {{ form.title }}
     <button type="submit">Save</button>
   {% endform %}

A page whose URL captures ``note_id`` posts a hidden ``_url_param_note_id`` field automatically.
The handler receives the same value through ``DUrl["note_id", int]``.

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
A hand-crafted form must also include the ``_next_form_page`` hidden field or the dispatcher cannot re-render on failure.

.. code-block:: jinja
   :caption: hand-crafted form

   <form action="/_next/form/{{ action_uid }}/" method="post">
     {% csrf_token %}
     <input type="hidden" name="_next_form_page" value="{{ current_page_module_path }}">
     <button type="submit">Send</button>
   </form>

Common Patterns
---------------

Form in a Component
~~~~~~~~~~~~~~~~~~~

A component template hosts ``{% form %}`` exactly like a page template.
The framework injects ``current_page_module_path`` from the surrounding page, so re-renders land on the correct page.

Form in a Layout
~~~~~~~~~~~~~~~~

Layouts receive ``current_page_module_path`` from the page they wrap.
A login form placed in the root layout re-renders the original page on validation failure.

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
