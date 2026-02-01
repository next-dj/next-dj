Forms
=====

next.dj provides a powerful form handling system that integrates seamlessly with file-based routing, context management, and template rendering. Forms are registered using action decorators and automatically handle CSRF protection, validation, and error rendering.

Overview
--------

The forms system in next.dj allows you to:

- **Register form actions** using the ``@forms.action()`` decorator
- **Automatically handle CSRF protection** - tokens are inserted automatically
- **Integrate with file routing** - URL parameters are automatically passed to forms
- **Use Django forms** - Full support for ``Form`` and ``ModelForm``
- **Handle validation errors** - Forms are re-rendered with errors automatically
- **Support multiple response types** - Redirects, HTTP responses, or None

Key Concepts
------------

**Form Actions**: Named handlers registered with ``@forms.action()`` that process form submissions

**Form Classes**: Django form classes (``Form`` or ``ModelForm``) that define form fields and validation

**Handlers**: Functions decorated with ``@forms.action()`` that process valid form submissions

**Action URLs**: Automatically generated URLs for each form action (format: ``/_next/form/<uid>/``)

Quick Start
-----------

Here's a minimal example of a form in next.dj:

**1. Create a form class:**

.. code-block:: python

   from next import forms

   class ContactForm(forms.Form):
       name = forms.CharField(max_length=100)
       email = forms.EmailField()
       message = forms.CharField(widget=forms.Textarea)

**2. Register a form action:**

.. code-block:: python

   from django.http import HttpRequest, HttpResponseRedirect
   from django.contrib import messages

   @forms.action("contact", form_class=ContactForm)
   def contact_handler(request: HttpRequest, form: ContactForm) -> HttpResponseRedirect:
       # Form is already validated at this point
       # Process the form data
       send_email(form.cleaned_data['email'], form.cleaned_data['message'])
       messages.success(request, "Your message has been sent!")
       return HttpResponseRedirect("/")

**3. Use the form in a template:**

.. code-block:: html

   {% load forms %}
   
   {% form @action="contact" %}
       {{ form.as_p }}
       <button type="submit">Send Message</button>
   {% endform %}

That's it! The form automatically includes CSRF protection and handles validation errors.

Basic Usage
-----------

Creating Forms
~~~~~~~~~~~~~~

next.dj provides two base form classes:

**``next.forms.Form``**: Standard Django form with ``get_initial()`` support

**``next.forms.ModelForm``**: Django model form with ``get_initial()`` support

Both classes extend Django's form classes and add the ``get_initial()`` class method for providing initial data or model instances.

**Available Form Fields:**

All standard Django form fields are available:

.. code-block:: python

   from next import forms

   class MyForm(forms.Form):
       # Text fields
       name = forms.CharField(max_length=100)
       email = forms.EmailField()
       url = forms.URLField()
       
       # Number fields
       age = forms.IntegerField()
       price = forms.DecimalField(max_digits=10, decimal_places=2)
       rating = forms.FloatField()
       
       # Choice fields
       status = forms.ChoiceField(choices=[('active', 'Active'), ('inactive', 'Inactive')])
       multiple_choice = forms.MultipleChoiceField(choices=[...])
       
       # Date fields
       birth_date = forms.DateField()
       created_at = forms.DateTimeField()
       
       # Boolean
       is_active = forms.BooleanField(required=False)
       
       # File uploads
       avatar = forms.ImageField(required=False)
       document = forms.FileField(required=False)
       
       # Custom widgets
       description = forms.CharField(widget=forms.Textarea)
       password = forms.CharField(widget=forms.PasswordInput)

**The ``get_initial()`` Method:**

Override ``get_initial()`` to provide initial data for forms:

.. code-block:: python

   class MyForm(forms.Form):
       name = forms.CharField(max_length=100)
       email = forms.EmailField()

       @classmethod
       def get_initial(cls, request: HttpRequest, *args, **kwargs) -> dict:
           """Provide initial data based on request or URL parameters."""
           initial = {}
           if request.user.is_authenticated:
               initial['name'] = request.user.get_full_name()
               initial['email'] = request.user.email
           return initial

For ModelForm, ``get_initial()`` can return either a dictionary (for initial data) or a model instance (for editing existing objects):

.. code-block:: python

   class TodoForm(forms.ModelForm):
       class Meta:
           model = Todo
           fields = ['title', 'description']

       @classmethod
       def get_initial(cls, request: HttpRequest, id: int = None, **kwargs) -> Todo | dict:
           """Return model instance for editing, or empty dict for creating."""
           if id:
               try:
                   return Todo.objects.get(pk=id)
               except Todo.DoesNotExist:
                   return {}
           return {}

Registering Actions
~~~~~~~~~~~~~~~~~~~

Use the ``@forms.action()`` decorator to register form action handlers:

.. code-block:: python

   @forms.action("action_name", form_class=MyForm)
   def my_handler(request: HttpRequest, form: MyForm) -> HttpResponse:
       """Handler is called only when form is valid."""
       # Process form data
       return HttpResponseRedirect("/success/")

**Decorator Parameters:**

- **``name``** (required): Unique action name used in templates
- **``form_class``** (optional): Form class for validation. If omitted, handler receives only request

**Handler Function Signature:**

Handlers receive the request and validated form (if ``form_class`` is provided):

.. code-block:: python

   # With form_class
   @forms.action("submit", form_class=MyForm)
   def submit_handler(request: HttpRequest, form: MyForm, **kwargs) -> HttpResponse:
       # form is validated
       # kwargs contains URL parameters from file routing
       pass

   # Without form_class
   @forms.action("simple_action")
   def simple_handler(request: HttpRequest, **kwargs) -> HttpResponse:
       # No form validation
       # kwargs contains URL parameters from file routing
       pass

Using Forms in Templates
~~~~~~~~~~~~~~~~~~~~~~~~

Use the ``{% form %}`` template tag to render forms:

.. code-block:: html

   {% load forms %}
   
   {% form @action="contact" %}
       {{ form.as_p }}
       <button type="submit">Submit</button>
   {% endform %}

**Template Tag Attributes:**

- **``@action``** (required): Action name registered with ``@forms.action()``
- **Other attributes**: Any HTML attributes (class, id, etc.) are passed through to the ``<form>`` tag

**Example with HTML attributes:**

.. code-block:: html

   {% form @action="contact" class="contact-form" id="contact-form" %}
       {{ form.as_p }}
       <button type="submit">Submit</button>
   {% endform %}

**Automatic Features:**

- **CSRF Token**: Automatically included as hidden field
- **Method**: Always POST
- **Action URL**: Automatically generated from action name
- **URL Parameters**: Automatically included as hidden fields (from file routing)

**Accessing Form in Template:**

The form instance is available inside the ``{% form %}`` tag:

.. code-block:: html

   {% form @action="contact" %}
       <div class="field">
           <label for="{{ form.name.id_for_label }}">Name:</label>
           {{ form.name }}
           {% if form.name.errors %}
               <ul class="errors">
                   {% for error in form.name.errors %}
                       <li>{{ error }}</li>
                   {% endfor %}
               </ul>
           {% endif %}
       </div>
       <button type="submit">Submit</button>
   {% endform %}

Handler Responses
~~~~~~~~~~~~~~~~~

Handlers can return different types of responses:

**HttpResponseRedirect** (most common):

.. code-block:: python

   @forms.action("submit", form_class=MyForm)
   def submit_handler(request: HttpRequest, form: MyForm) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect("/success/")

**HttpResponse**:

.. code-block:: python

   @forms.action("submit", form_class=MyForm)
   def submit_handler(request: HttpRequest, form: MyForm) -> HttpResponse:
       return HttpResponse("Form submitted successfully!")

**None** (returns 204 No Content):

.. code-block:: python

   @forms.action("submit", form_class=MyForm)
   def submit_handler(request: HttpRequest, form: MyForm) -> None:
       form.save()
       # Returns 204 No Content

**Object with ``.url`` attribute** (automatically converted to redirect):

.. code-block:: python

   @forms.action("submit", form_class=MyForm)
   def submit_handler(request: HttpRequest, form: MyForm):
       obj = form.save()
       return obj  # If obj has .url attribute, it's converted to redirect

**Invalid Form Handling:**

If the form is invalid, the handler is **not called**. Instead, the form is automatically re-rendered with validation errors using the same template and context as the original page.

Working with ModelForm
----------------------

Creating New Objects
~~~~~~~~~~~~~~~~~~~~~

For creating new model instances, return an empty dictionary from ``get_initial()``:

.. code-block:: python

   from typing import ClassVar
   from next import forms
   from todos.models import Todo

   class TodoForm(forms.ModelForm):
       class Meta:
           model = Todo
           fields: ClassVar[list[str]] = ["title", "description", "is_completed"]

       title = forms.CharField(max_length=200)
       description = forms.CharField(widget=forms.Textarea, required=False)
       is_completed = forms.BooleanField(required=False)

       @classmethod
       def get_initial(cls, request: HttpRequest) -> dict:
           """Return empty dict for creating new todos."""
           return {}

   @forms.action("create_todo", form_class=TodoForm)
   def create_todo_handler(request: HttpRequest, form: TodoForm) -> HttpResponseRedirect:
       """Create a new todo and redirect."""
       form.save()
       messages.success(request, "Todo created successfully.")
       return HttpResponseRedirect("/")

Editing Existing Objects
~~~~~~~~~~~~~~~~~~~~~~~~

For editing existing objects, return the model instance from ``get_initial()``:

.. code-block:: python

   from django.http import Http404
   from typing import ClassVar
   from next import forms
   from todos.models import Todo

   class TodoEditForm(forms.ModelForm):
       class Meta:
           model = Todo
           fields: ClassVar[list[str]] = ["title", "description", "is_completed"]

       title = forms.CharField(max_length=200)
       description = forms.CharField(widget=forms.Textarea, required=False)
       is_completed = forms.BooleanField(required=False)

       @classmethod
       def get_initial(
           cls,
           request: HttpRequest,
           id: int,  # URL parameter from [int:id]
           **_kwargs: object,
       ) -> Todo | dict:
           """Return Todo instance for editing, or empty dict if not found."""
           try:
               return Todo.objects.get(pk=id)
           except Todo.DoesNotExist:
               return {}

   @forms.action("update_todo", form_class=TodoEditForm)
   def update_todo_handler(
       request: HttpRequest, form: TodoEditForm, **_kwargs: object
   ) -> HttpResponseRedirect:
       """Update todo and redirect."""
       form.save()
       messages.success(request, "Todo updated successfully.")
       return HttpResponseRedirect("/")

**Automatic Instance Detection:**

The system automatically detects if ``get_initial()`` returns a model instance:

- **Model instance**: Used as ``instance`` parameter (for ModelForm only)
- **Dictionary**: Used as ``initial`` parameter (for both Form and ModelForm)

**URL Parameters:**

URL parameters from file routing (e.g., ``[int:id]``) are automatically passed to ``get_initial()``:

.. code-block:: python

   # pages/edit/[int:id]/page.py
   class TodoEditForm(forms.ModelForm):
       @classmethod
       def get_initial(cls, request: HttpRequest, id: int, **kwargs) -> Todo | dict:
           # id is automatically passed from URL pattern [int:id]
           return Todo.objects.get(pk=id)

Integration with Other Features
-------------------------------

File Routing
~~~~~~~~~~~~

Forms automatically receive URL parameters from file routing:

**Example with URL parameters:**

.. code-block:: python

   # pages/post/[int:post_id]/edit/page.py
   from django.http import HttpRequest, HttpResponseRedirect
   from next import forms

   class PostEditForm(forms.ModelForm):
       # ... form fields ...

       @classmethod
       def get_initial(cls, request: HttpRequest, post_id: int, **kwargs) -> Post | dict:
           # post_id is automatically passed from [int:post_id] in URL
           return Post.objects.get(pk=post_id)

   @forms.action("update_post", form_class=PostEditForm)
   def update_post_handler(
       request: HttpRequest, form: PostEditForm, post_id: int, **kwargs
   ) -> HttpResponseRedirect:
       # post_id is also available in handler
       form.save()
       return HttpResponseRedirect(f"/post/{post_id}/")

**How URL Parameters Work:**

1. URL parameters are extracted from ``request.resolver_match.kwargs``
2. They are included as hidden fields in the form (``_url_param_<name>``)
3. They are passed to ``get_initial()`` and handler functions
4. They are available in context functions when re-rendering errors

Context System
~~~~~~~~~~~~~~

Forms are automatically registered in the context system:

**Automatic Context Registration:**

When you register a form action with ``form_class``, the form is automatically available in templates:

.. code-block:: python

   @forms.action("create_todo", form_class=TodoForm)
   def create_todo_handler(request: HttpRequest, form: TodoForm) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect("/")

The form is available in templates as ``create_todo.form``:

.. code-block:: html

   {% form @action="create_todo" %}
       {{ form.as_p }}
   {% endform %}

**Using Forms with Context Functions:**

You can combine forms with context functions:

.. code-block:: python

   from next.pages import context

   @context("todos")
   def get_todos(request: HttpRequest) -> list[Todo]:
       """Get all todos for the list."""
       return list(Todo.objects.all())

   @forms.action("create_todo", form_class=TodoForm)
   def create_todo_handler(request: HttpRequest, form: TodoForm) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect("/")

In the template:

.. code-block:: html

   <h2>Create New Todo</h2>
   {% form @action="create_todo" %}
       {{ form.as_p }}
       <button type="submit">Create</button>
   {% endform %}

   <h2>All Todos</h2>
   {% for todo in todos %}
       <p>{{ todo.title }}</p>
   {% endfor %}

Layouts
~~~~~~~

Forms work seamlessly with layout inheritance:

.. code-block:: html

   <!-- pages/layout.djx -->
   <!DOCTYPE html>
   <html>
   <head>
       <title>My App</title>
   </head>
   <body>
       {% if messages %}
           <ul>
               {% for message in messages %}
                   <li>{{ message }}</li>
               {% endfor %}
           </ul>
       {% endif %}
       {% block template %}{% endblock template %}
   </body>
   </html>

.. code-block:: html

   <!-- pages/contact/template.djx -->
   {% extends "layout.djx" %}
   
   {% block template %}
       <h1>Contact Us</h1>
       {% form @action="contact" %}
           {{ form.as_p }}
           <button type="submit">Send</button>
       {% endform %}
   {% endblock template %}

**Form Errors in Layouts:**

When form validation fails, the entire page (including layout) is re-rendered with errors, maintaining the same visual structure.

Messages Framework
~~~~~~~~~~~~~~~~~~

Use Django's messages framework for user feedback:

.. code-block:: python

   from django.contrib import messages
   from django.http import HttpResponseRedirect

   @forms.action("create_todo", form_class=TodoForm)
   def create_todo_handler(request: HttpRequest, form: TodoForm) -> HttpResponseRedirect:
       form.save()
       messages.success(request, "Todo created successfully!")
       return HttpResponseRedirect("/")

   @forms.action("delete_todo")
   def delete_todo_handler(request: HttpRequest, todo_id: int) -> HttpResponseRedirect:
       Todo.objects.filter(pk=todo_id).delete()
       messages.warning(request, "Todo deleted.")
       return HttpResponseRedirect("/")

Display messages in templates:

.. code-block:: html

   {% if messages %}
       <ul class="messages">
           {% for message in messages %}
               <li class="{{ message.tags }}">{{ message }}</li>
           {% endfor %}
       </ul>
   {% endif %}

Internal Architecture
---------------------

Action Registration
~~~~~~~~~~~~~~~~~~~

When you use ``@forms.action()``, the following happens:

1. **UID Generation**: A unique identifier (UID) is created from the file path and action name using SHA256 hash (first 16 characters)

2. **Registry Storage**: The action is stored in ``FormActionManager`` with:
   - Handler function
   - Form class (if provided)
   - File path
   - UID

3. **Context Registration**: If ``form_class`` is provided, a context function is automatically registered that:
   - Calls ``form_class.get_initial()`` with request and URL parameters
   - Creates form instance with initial data or model instance
   - Returns form in a ``SimpleNamespace`` object

**Example Registration Flow:**

.. code-block:: python

   # File: pages/contact/page.py
   @forms.action("contact", form_class=ContactForm)
   def contact_handler(request: HttpRequest, form: ContactForm) -> HttpResponseRedirect:
       # This action is registered with:
       # - name: "contact"
       # - uid: hash("pages/contact/page.py:contact")[:16]
       # - handler: contact_handler
       # - form_class: ContactForm
       # - file_path: Path("pages/contact/page.py")
       pass

URL Generation
~~~~~~~~~~~~~~

Form actions generate URL patterns automatically:

**URL Pattern Format:**

All form actions use a single URL pattern:

.. code-block:: text

   /_next/form/<str:uid>/

**URL Resolution:**

1. ``FormActionManager.generate_urls()`` creates URL patterns from all registered backends
2. Each backend's ``generate_urls()`` returns a list of URL patterns
3. The default backend (``RegistryFormActionBackend``) creates one pattern for all actions
4. Actions are dispatched by UID, not by name

**Getting Action URLs:**

Use ``form_action_manager.get_action_url(action_name)`` to get the URL for an action:

.. code-block:: python

   from next.forms import form_action_manager

   url = form_action_manager.get_action_url("contact")
   # Returns: "/_next/form/abc123def4567890/"

Request Handling
~~~~~~~~~~~~~~~~

**GET Requests:**

GET requests to form action URLs return ``405 Method Not Allowed``. Forms are displayed through the normal page rendering process using context functions.

**POST Request Flow:**

1. **Extract URL Parameters**: URL parameters are extracted from hidden form fields (``_url_param_<name>``)

2. **Get Initial Data**: If ``form_class`` is provided:
   - Call ``form_class.get_initial(request, **url_kwargs)``
   - Check if result is a model instance (has ``_meta.model`` attribute)
   - If model instance: create form with ``instance=initial_data`` (ModelForm only)
   - If dictionary: create form with ``initial=initial_data``

3. **Validate Form**: Call ``form.is_valid()``

4. **Handle Invalid Form**: If form is invalid:
   - Load template from registry (same template as original page)
   - Build context with form errors
   - Pass URL parameters to context functions
   - Render full page with errors
   - Return 200 with rendered HTML

5. **Handle Valid Form**: If form is valid:
   - Call handler function with ``request, form, **url_kwargs``
   - Normalize handler response
   - Return appropriate HTTP response

**Response Normalization:**

Handler responses are normalized through ``_normalize_handler_response()``:

- **None**: Returns 204 No Content (or re-renders form if backend/request available)
- **HttpResponse**: Returns as-is
- **str**: Wraps in HttpResponse
- **Object with .url**: Converts to HttpResponseRedirect

**Error Rendering:**

When form validation fails:

1. **Template Loading**: Load full template from ``page._template_registry`` using file_path

2. **Context Building**: Build context with:
   - All context functions (with URL parameters)
   - Form instance with errors (under action name and as ``form``)
   - Request object

3. **Rendering**: Render template with context, showing form errors

**Component Architecture:**

- **``FormActionBackend``**: Abstract base class for form action backends
- **``RegistryFormActionBackend``**: In-memory registry implementation (default)
- **``FormActionManager``**: Manages multiple backends, aggregates URL patterns
- **``_FormActionDispatch``**: Internal class handling dispatch logic and response normalization

Examples
--------

Simple Contact Form
~~~~~~~~~~~~~~~~~~~

A complete example of a contact form without a model:

.. code-block:: python

   # pages/contact/page.py
   from django.contrib import messages
   from django.http import HttpRequest, HttpResponseRedirect
   from next import forms

   class ContactForm(forms.Form):
       name = forms.CharField(max_length=100)
       email = forms.EmailField()
       subject = forms.CharField(max_length=200)
       message = forms.CharField(widget=forms.Textarea)

       @classmethod
       def get_initial(cls, request: HttpRequest) -> dict:
           """Pre-fill email if user is authenticated."""
           initial = {}
           if request.user.is_authenticated:
               initial['email'] = request.user.email
               initial['name'] = request.user.get_full_name()
           return initial

   @forms.action("contact", form_class=ContactForm)
   def contact_handler(request: HttpRequest, form: ContactForm) -> HttpResponseRedirect:
       """Send contact message."""
       # In a real app, send email here
       send_contact_email(
           form.cleaned_data['email'],
           form.cleaned_data['subject'],
           form.cleaned_data['message']
       )
       messages.success(request, "Your message has been sent!")
       return HttpResponseRedirect("/contact/")

.. code-block:: html

   <!-- pages/contact/template.djx -->
   <h1>Contact Us</h1>
   
   {% load forms %}
   {% form @action="contact" %}
       {{ form.as_p }}
       <button type="submit">Send Message</button>
   {% endform %}

CRUD Operations
~~~~~~~~~~~~~~~

Complete CRUD example with a Todo model:

**Create:**

.. code-block:: python

   # pages/page.py
   from typing import ClassVar
   from django.contrib import messages
   from django.http import HttpRequest, HttpResponseRedirect
   from todos.models import Todo
   from next import forms
   from next.pages import context

   @context("todos")
   def get_todos(_request: HttpRequest) -> list[Todo]:
       """Get all todos."""
       return list(Todo.objects.all())

   class TodoForm(forms.ModelForm):
       class Meta:
           model = Todo
           fields: ClassVar[list[str]] = ["title", "description", "is_completed"]

       title = forms.CharField(max_length=200)
       description = forms.CharField(widget=forms.Textarea, required=False)
       is_completed = forms.BooleanField(required=False)

       @classmethod
       def get_initial(cls, _request: HttpRequest) -> dict:
           """Empty initial data for new todos."""
           return {}

   @forms.action("create_todo", form_class=TodoForm)
   def create_todo_handler(request: HttpRequest, form: TodoForm) -> HttpResponseRedirect:
       """Create new todo."""
       form.save()
       messages.success(request, "Todo created successfully.")
       return HttpResponseRedirect("/")

**Read (List):**

.. code-block:: html

   <!-- pages/template.djx -->
   <h1>Todo List</h1>
   
   {% load forms %}
   
   <h2>Create New Todo</h2>
   {% form @action="create_todo" %}
       {{ form.as_p }}
       <button type="submit">Create Todo</button>
   {% endform %}

   <h2>All Todos</h2>
   {% if todos %}
       <ul>
           {% for todo in todos %}
               <li>
                   <a href="/edit/{{ todo.id }}/">{{ todo.title }}</a>
                   {% if todo.is_completed %}
                       <strong>(Completed)</strong>
                   {% endif %}
               </li>
           {% endfor %}
       </ul>
   {% else %}
       <p>No todos yet.</p>
   {% endif %}

**Update:**

.. code-block:: python

   # pages/edit/[int:id]/page.py
   from typing import ClassVar
   from django.contrib import messages
   from django.http import Http404, HttpRequest, HttpResponseRedirect
   from todos.models import Todo
   from next import forms
   from next.pages import context

   @context("todo")
   def get_todo(_request: HttpRequest, id: int, **_kwargs: object) -> Todo:
       """Get todo by ID or raise 404."""
       try:
           return Todo.objects.get(pk=id)
       except Todo.DoesNotExist as err:
           msg = "Todo not found"
           raise Http404(msg) from err

   class TodoEditForm(forms.ModelForm):
       class Meta:
           model = Todo
           fields: ClassVar[list[str]] = ["title", "description", "is_completed"]

       title = forms.CharField(max_length=200)
       description = forms.CharField(widget=forms.Textarea, required=False)
       is_completed = forms.BooleanField(required=False)

       @classmethod
       def get_initial(
           cls,
           _request: HttpRequest,
           id: int,
           **_kwargs: object,
       ) -> Todo | dict:
           """Return todo instance for editing."""
           try:
               return Todo.objects.get(pk=id)
           except Todo.DoesNotExist:
               return {}

   @forms.action("update_todo", form_class=TodoEditForm)
   def update_todo_handler(
       request: HttpRequest, form: TodoEditForm, **_kwargs: object
   ) -> HttpResponseRedirect:
       """Update todo."""
       form.save()
       messages.success(request, "Todo updated successfully.")
       return HttpResponseRedirect("/")

.. code-block:: html

   <!-- pages/edit/[int:id]/template.djx -->
   <h1>Edit Todo</h1>
   
   {% load forms %}
   
   <p><a href="/">Back to list</a></p>
   
   {% form @action="update_todo" %}
       {{ form.as_p }}
       <button type="submit">Update Todo</button>
   {% endform %}

**Delete (without form):**

.. code-block:: python

   # pages/delete/[int:id]/page.py
   from django.contrib import messages
   from django.http import HttpRequest, HttpResponseRedirect
   from todos.models import Todo
   from next import forms

   @forms.action("delete_todo")
   def delete_todo_handler(request: HttpRequest, id: int) -> HttpResponseRedirect:
       """Delete todo."""
       Todo.objects.filter(pk=id).delete()
       messages.success(request, "Todo deleted.")
       return HttpResponseRedirect("/")

File Upload Example
~~~~~~~~~~~~~~~~~~~

Example with file uploads:

.. code-block:: python

   from django.core.files.storage import default_storage
   from django.http import HttpRequest, HttpResponseRedirect
   from next import forms

   class DocumentForm(forms.Form):
       title = forms.CharField(max_length=200)
       document = forms.FileField()

   @forms.action("upload_document", form_class=DocumentForm)
   def upload_document_handler(request: HttpRequest, form: DocumentForm) -> HttpResponseRedirect:
       """Handle file upload."""
       file = form.cleaned_data['document']
       # Save file using Django's storage
       filename = default_storage.save(file.name, file)
       # Process file...
       return HttpResponseRedirect("/documents/")

Best Practices
--------------

When to Use Form vs ModelForm
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Use ``Form``** when:
  - You don't need to save to a database
  - You're processing data without a model
  - You need custom validation logic
  - You're integrating with external APIs

- **Use ``ModelForm``** when:
  - You're creating or editing model instances
  - You want automatic field generation from model
  - You need database persistence

Organizing Forms
~~~~~~~~~~~~~~~~

**File Structure:**

Organize forms logically:

.. code-block:: text

   pages/
   ├── contact/
   │   ├── page.py          # Contact form
   │   └── template.djx
   ├── todos/
   │   ├── page.py          # Todo list and create form
   │   ├── template.djx
   │   └── edit/
   │       └── [int:id]/
   │           ├── page.py  # Edit form
   │           └── template.djx

**Naming Conventions:**

- Use descriptive action names: ``create_todo``, ``update_todo``, ``delete_todo``
- Use consistent form class names: ``TodoForm``, ``TodoEditForm``
- Use clear handler names: ``create_todo_handler``, ``update_todo_handler``

Error Handling
~~~~~~~~~~~~~~

**Always handle errors gracefully:**

.. code-block:: python

   @forms.action("update_todo", form_class=TodoEditForm)
   def update_todo_handler(request: HttpRequest, form: TodoEditForm, id: int) -> HttpResponseRedirect:
       try:
           form.save()
           messages.success(request, "Todo updated successfully.")
       except Exception as e:
           messages.error(request, f"Error updating todo: {e}")
       return HttpResponseRedirect(f"/edit/{id}/")

**Handle 404 errors in get_initial():**

.. code-block:: python

   @classmethod
   def get_initial(cls, request: HttpRequest, id: int, **kwargs) -> Todo | dict:
       try:
           return Todo.objects.get(pk=id)
       except Todo.DoesNotExist:
           # Return empty dict to show empty form
           # Or raise Http404 if editing is required
           return {}

Security
~~~~~~~~

**CSRF Protection:**

CSRF tokens are automatically included. Ensure Django's CSRF middleware is enabled:

.. code-block:: python

   # settings.py
   MIDDLEWARE = [
       # ...
       'django.middleware.csrf.CsrfViewMiddleware',
       # ...
   ]

**Input Validation:**

Always validate user input:

- Use Django's built-in field validators
- Add custom validation in ``clean_<field>()`` methods
- Validate in handler functions for complex logic

**Access Control:**

Add permission checks in handlers:

.. code-block:: python

   @forms.action("delete_todo", form_class=TodoForm)
   def delete_todo_handler(request: HttpRequest, form: TodoForm, id: int) -> HttpResponseRedirect:
       if not request.user.has_perm('todos.delete_todo'):
           messages.error(request, "Permission denied.")
           return HttpResponseRedirect("/")
       
       todo = Todo.objects.get(pk=id)
       todo.delete()
       return HttpResponseRedirect("/")

Troubleshooting
---------------

Common Issues
~~~~~~~~~~~~~

**Form not appearing in template:**

- Ensure ``{% load forms %}`` is called before using ``{% form %}``
- Check that action name matches ``@action`` parameter
- Verify form action is registered (check for import errors)

**CSRF token missing:**

- Ensure ``django.middleware.csrf.CsrfViewMiddleware`` is in ``MIDDLEWARE``
- Check that ``request`` is in template context (add ``django.template.context_processors.request``)

**URL parameters not passed:**

- Verify file routing pattern matches (e.g., ``[int:id]``)
- Check that hidden fields ``_url_param_<name>`` are in form HTML
- Ensure ``get_initial()`` and handler accept URL parameters

**Form errors not showing:**

- Check that form validation is working (add ``print(form.errors)``)
- Verify template has error display (``{{ form.errors }}`` or ``{{ form.<field>.errors }}``)
- Ensure form is re-rendered (check response status is 200, not redirect)

**Handler not called:**

- Form might be invalid (check ``form.is_valid()``)
- Check handler signature matches (request, form, **kwargs)
- Verify action is registered correctly

Debugging Tips
~~~~~~~~~~~~~~

**Check form registration:**

.. code-block:: python

   from next.forms import form_action_manager

   # Check if action exists
   try:
       url = form_action_manager.get_action_url("my_action")
       print(f"Action URL: {url}")
   except KeyError:
       print("Action not registered!")

**Check form validation:**

.. code-block:: python

   @forms.action("my_action", form_class=MyForm)
   def my_handler(request: HttpRequest, form: MyForm) -> HttpResponseRedirect:
       if not form.is_valid():
           print("Form errors:", form.errors)
           print("Form data:", form.data)
       # ...

**Check URL parameters:**

.. code-block:: python

   @forms.action("my_action", form_class=MyForm)
   def my_handler(request: HttpRequest, form: MyForm, **kwargs) -> HttpResponseRedirect:
       print("URL kwargs:", kwargs)
       # ...

See Also
--------

- :doc:`file-router` - File-based routing system
- :doc:`context-system` - Context management
- :doc:`templates-layouts` - Template and layout system
- `Django Forms Documentation <https://docs.djangoproject.com/en/stable/topics/forms/>`_ - Django forms reference
