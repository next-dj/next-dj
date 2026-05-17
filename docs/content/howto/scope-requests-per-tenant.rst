.. _howto-scope-requests-per-tenant:

Scope Requests Per Tenant
=========================

Problem
-------

Several tenants share one Django project, one page tree, and one static pipeline, and every request must see only the data, theme, and asset URLs of the tenant it belongs to.

Solution
--------

Resolve the tenant once in :doc:`middleware <django:topics/http/middleware>`, stash it on the request, and let a dependency provider, a :doc:`context processor <django:ref/templates/api>`, and a custom static backend each read it back from there.

Walkthrough
-----------

Resolve The Tenant In Middleware
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The middleware parses the ``X-Tenant`` header, looks up the matching row, and attaches it to ``request.tenant``.
A missing header is a ``400`` and an unknown slug is a ``404``.

.. code-block:: python
   :caption: notes/middleware.py

   from notes.models import Tenant


   HEADER_NAME = "HTTP_X_TENANT"


   class TenantMiddleware:
       def __init__(self, get_response):
           self._get_response = get_response

       def __call__(self, request):
           slug = request.META.get(HEADER_NAME, "").strip()
           if not slug:
               return HttpResponseBadRequest("Missing X-Tenant header.")
           try:
               tenant = Tenant.objects.get(slug=slug)
           except Tenant.DoesNotExist:
               return HttpResponse(f"Unknown tenant slug {slug!r}.", status=404)
           request.tenant = tenant
           return self._get_response(request)

Register it last in ``MIDDLEWARE`` so it runs after sessions and authentication.

.. code-block:: python
   :caption: config/settings.py

   MIDDLEWARE = [
       "django.middleware.security.SecurityMiddleware",
       "django.contrib.sessions.middleware.SessionMiddleware",
       "django.middleware.common.CommonMiddleware",
       "django.middleware.csrf.CsrfViewMiddleware",
       "django.contrib.auth.middleware.AuthenticationMiddleware",
       "django.contrib.messages.middleware.MessageMiddleware",
       "django.middleware.clickjacking.XFrameOptionsMiddleware",
       "notes.middleware.TenantMiddleware",
   ]

Read The Tenant Through One Helper
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Every consumer reads the tenant through a single accessor instead of touching ``request.tenant`` directly.
On error pages where the middleware short-circuited, the attribute is absent and the helper returns ``None``.

.. code-block:: python
   :caption: notes/access.py

   def get_active_tenant(request):
       """Return the tenant attached to `request` by `TenantMiddleware`."""
       return getattr(request, "tenant", None)

Inject The Tenant Into Pages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``DDependencyBase`` marker plus a ``RegisteredParameterProvider`` lets page and action callables ask for the tenant by type.
The provider matches the bare ``DTenant`` annotation when a request carries a tenant.

.. code-block:: python
   :caption: notes/providers.py

   from next.deps import DDependencyBase, RegisteredParameterProvider
   from notes.access import get_active_tenant


   class DTenant(DDependencyBase["Tenant"]):
       """DI marker that resolves to the active `Tenant` for the request."""

       __slots__ = ()


   class TenantProvider(RegisteredParameterProvider):
       def can_handle(self, param, context):
           if param.annotation is not DTenant:
               return False
           request = getattr(context, "request", None)
           if request is None:
               return False
           return get_active_tenant(request) is not None

       def resolve(self, _param, context):
           return get_active_tenant(context.request)

Import the module from ``AppConfig.ready`` so the auto-registry wires the provider at startup.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig


   class NotesConfig(AppConfig):
       default_auto_field = "django.db.models.BigAutoField"
       name = "notes"

       def ready(self):
           from notes import providers  # noqa: F401, PLC0415

A page context function now requests the tenant by name and type, and the resolver hands back the model instance.
Keep real annotations in these modules, because the resolver compares parameter annotations by identity.

.. code-block:: python
   :caption: notes/workspaces/notes/page.py

   from notes.models import Note
   from notes.providers import DTenant

   from next.pages import context


   @context("notes")
   def notes(active_tenant: DTenant) -> list[Note]:
       """Return every note that belongs to the active tenant."""
       return list(Note.objects.filter(tenant=active_tenant))

Lift The Tenant To Every Descendant Page
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``@context(..., inherit_context=True)`` callable on the workspace root publishes the tenant once, and every nested page reads it without re-resolving.

.. code-block:: python
   :caption: notes/workspaces/page.py

   from notes.models import Note
   from notes.providers import DTenant

   from next.pages import context


   @context("tenant", inherit_context=True)
   def tenant(active_tenant: DTenant) -> "Tenant":
       """Expose the active tenant under `tenant` to every workspace page."""
       return active_tenant

Theme The Chrome With A Context Processor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A context processor turns the tenant's color into a CSS variable for every template.
List it in the page backend ``OPTIONS`` so the file router runs it.

.. code-block:: python
   :caption: notes/context_processors.py

   from notes.access import get_active_tenant


   def tenant_theme(request):
       """Surface per-tenant CSS variables to every page template."""
       tenant = get_active_tenant(request)
       if tenant is None:
           return {"tenant_theme": {}, "tenant_theme_css": ""}
       css_vars = {"--tenant-accent": tenant.primary_color}
       css = ";".join(f"{name}:{value}" for name, value in css_vars.items())
       return {"tenant_theme": css_vars, "tenant_theme_css": css}

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "PAGES_DIR": "workspaces",
               "OPTIONS": {
                   "context_processors": [
                       "django.template.context_processors.request",
                       "notes.context_processors.tenant_theme",
                   ],
               },
           },
       ],
   }

Prefix Asset URLs Per Tenant
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A static backend subclass reads the tenant from the ``request`` keyword that the static manager threads into every renderer.
It prepends ``/_t/<slug>`` to each collected URL and leaves absolute URLs untouched.

.. code-block:: python
   :caption: notes/backends.py

   from next.static import StaticFilesBackend
   from notes.access import get_active_tenant


   PREFIX_FORMAT = "/_t/{slug}"


   class TenantPrefixStaticBackend(StaticFilesBackend):
       def render_link_tag(self, url, *, request=None):
           return super().render_link_tag(_prefixed(url, request))

       def render_script_tag(self, url, *, request=None):
           return super().render_script_tag(_prefixed(url, request))


   def _prefixed(url, request):
       tenant = get_active_tenant(request) if request is not None else None
       if tenant is None or not url.startswith("/"):
           return url
       return PREFIX_FORMAT.format(slug=tenant.slug) + url

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {"BACKEND": "notes.backends.TenantPrefixStaticBackend"},
       ],
   }

Verification
------------

Send the same path with two different headers and confirm the responses diverge.

.. code-block:: bash
   :caption: confirm tenant isolation

   curl -H 'X-Tenant: acme' http://127.0.0.1:8000/notes/
   curl -H 'X-Tenant: globex' http://127.0.0.1:8000/notes/

The Acme response lists only Acme notes and carries ``/_t/acme/static/`` URLs.
The Globex response lists only Globex notes and carries ``/_t/globex/static/`` URLs.
A request with no header returns ``400``.

See Also
--------

.. seealso::

   :doc:`/content/topics/dependency-injection` for the request-scoped provider pattern.
   :doc:`/content/topics/static-assets/backends` for the request-aware backend contract.
   :doc:`/content/topics/context` for ``inherit_context`` and context processors.
   :doc:`/content/internals/request-lifecycle` for where middleware sits in the request path.
