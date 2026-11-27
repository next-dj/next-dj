.. _topics-boundaries-compared:

Boundaries compared with htmx, Next.js, Turbo, and LiveView
===========================================================

A widget that is slow to load, shows a skeleton while it loads, and shows its own message when it fails is a task every full-stack framework answers.
This page writes that one task in next.dj and in four other stacks, and lists what the developer writes in each.
Claims about the other stacks stay inside the model each project describes as its own, with no version-specific detail, and the code of the other stacks shows the shape of the answer rather than a complete project.

.. contents::
   :local:
   :depth: 1

The task
--------

A product page lists related products, which a remote service computes.
The list should not hold the page back, a placeholder stands in for it while it loads, and a failure of the service shows a short message in the list's place with the rest of the page intact.

next.dj
-------

The widget is a component in a directory, and the two fallbacks are files beside it.

.. code-block:: text

   shop/pages/product/_components/recs/
       component.py
       component.djx
       loading.djx
       error.djx

.. code-block:: python
   :caption: component.py

   from shop.models import Product
   from shop.services import recommend

   from next import component

   @component.context("related")
   def related(product: Product) -> list[Product]:
       return recommend(product)

.. code-block:: jinja
   :caption: component.djx

   <ul>{% for item in related %}<li>{{ item.name }}</li>{% endfor %}</ul>

.. code-block:: jinja
   :caption: loading.djx

   <p>Loading&hellip;</p>

.. code-block:: jinja
   :caption: error.djx

   <p>Unavailable.</p>

.. code-block:: jinja
   :caption: template.djx

   {% component "recs" %}

The developer writes the four files and one call.
No URL and no view exist for the widget, because the response fills it in.
The mode ``stream`` follows from the ``loading.djx``, and a ``timeout`` and a rollback of the widget's database writes belong to the boundary, see :doc:`loading` and :doc:`errors`.
The same widget moves to its own request with ``component.defer("visible", props=(...))``, which adds the declared props and a token, see :doc:`loading`.

Django and htmx
---------------

The widget is a second view behind its own URL, and the page polls it on load.

.. code-block:: python
   :caption: urls.py

   path("product/<int:pk>/recs/", recs, name="recs")

.. code-block:: python
   :caption: views.py

   def recs(request, pk):
       product = get_object_or_404(Product, pk=pk)
       try:
           items = recommend(product)
       except ServiceError:
           return render(request, "_recs_error.html")
       return render(request, "_recs.html", {"items": items})

.. code-block:: django
   :caption: product.html

   <div hx-get="{% url 'recs' product.pk %}" hx-trigger="load" hx-swap="outerHTML">
     Loading&hellip;
   </div>

The developer writes a URL entry, a view with its own error handling, two partial templates, and the placeholder in the page.
The view repeats whatever access check the page makes, and a timeout is set on the client or the server by hand.
The placeholder and the call sit in the same template.

Next.js App Router
------------------

The widget is an async server component, and the placeholder is the ``fallback`` of a ``<Suspense>`` around it.

.. code-block:: tsx
   :caption: page.tsx

   <ErrorBoundary fallback={<p>Unavailable.</p>}>
     <Suspense fallback={<p>Loading&hellip;</p>}>
       <Recs id={product.id} />
     </Suspense>
   </ErrorBoundary>

.. code-block:: tsx
   :caption: recs.tsx

   export default async function Recs({ id }) {
     const items = await recommend(id)
     return <ul>{items.map((i) => <li key={i.id}>{i.name}</li>)}</ul>
   }

The developer writes the component, the ``<Suspense>`` with its fallback, and an error boundary.
The ``loading.tsx`` and ``error.tsx`` file conventions wrap a whole route segment, so a boundary around one widget is an error boundary component written or imported for it.
The fallbacks sit at the call site, and no URL exists for the widget.

Rails and Turbo
---------------

The widget is a lazy turbo frame that fetches its own route.

.. code-block:: erb
   :caption: show.html.erb

   <%= turbo_frame_tag "recs", src: recommendations_product_path(@product), loading: :lazy do %>
     Loading&hellip;
   <% end %>

.. code-block:: erb
   :caption: recommendations.html.erb

   <%= turbo_frame_tag "recs" do %>
     <ul><% @items.each do |item| %><li><%= item.name %></li><% end %></ul>
   <% end %>

The developer writes a route, a controller action that handles the failure with a ``rescue``, a template with the same frame id, and the frame in the page.
The placeholder sits in the page, and a response that does not hold the frame is handled through the ``turbo:frame-missing`` event.

Phoenix LiveView
----------------

The widget is an assign loaded by a task, and a component renders its three states.

.. code-block:: elixir
   :caption: product_live.ex

   def mount(%{"id" => id}, _session, socket) do
     {:ok, assign_async(socket, :recs, fn -> {:ok, %{recs: recommend(id)}} end)}
   end

.. code-block:: text
   :caption: product_live.html.heex

   <.async_result :let={recs} assign={@recs}>
     <:loading>Loading&hellip;</:loading>
     <:failed :let={_reason}>Unavailable.</:failed>
     <ul><li :for={item <- recs}>{item.name}</li></ul>
   </.async_result>

The developer writes the ``assign_async`` call and the ``async_result`` component with its two slots, all in the view module and its template.
No URL exists for the widget, and the page keeps a connection open to the server for the updates.

Side by side
------------

.. list-table::
   :header-rows: 1
   :widths: 20 22 22 16 20

   * - Approach
     - What the developer writes
     - Own URL for the widget
     - Where the fallback sits
     - Failure handling
   * - next.dj ``stream``
     - Four files and one call, two of the files being the widget itself.
     - No.
     - Beside the component, not at the call.
     - The ``error.djx``, with a rollback of the widget's writes.
   * - next.dj ``visible``
     - The same, plus the declared props and the changed signature.
     - No.
     - Beside the component.
     - The same, and the access rules are checked again on the fetch.
   * - htmx
     - A URL, a view, two partials, and the placeholder.
     - Yes.
     - At the call site.
     - In the view.
   * - Next.js
     - The async component, a ``<Suspense>``, and an error boundary.
     - No.
     - At the call site.
     - An error boundary around the widget.
   * - Turbo
     - A route, an action, a template, and the frame.
     - Yes.
     - At the call site.
     - In the action, or on ``turbo:frame-missing``.
   * - LiveView
     - ``assign_async`` and ``async_result`` with its slots.
     - No.
     - At the call site.
     - The ``:failed`` slot.

Where next.dj asks for more
---------------------------

The same task costs more in next.dj in three places.

Four files per widget.
   The loading UI and the error UI are files of their own, and a widget with both holds four.
   The other stacks keep the fallbacks in the template that calls the widget.

A model of props and tokens for the modes that fetch later.
   A ``load``, ``visible``, or ``manual`` component renders on its own request from declared props alone, so the props are named in ``component.defer()``, typed so a token carries them, and read by a callable that takes them instead of the page context.
   The token carries them readable unless the seal encrypts them, see :doc:`loading`.
   htmx and Turbo have no such model, since the URL of the widget carries the same information.

A fallback that does not show where the component is called.
   A reader of the page template sees ``{% component "recs" %}`` and nothing that says it loads later.
   The mode comes from the call, the ``component.defer()`` declaration, or the presence of a ``loading.djx``, in that order.

What next.dj returns for the extra files is a widget without a URL or a view of its own, access rules and a timeout that belong to the boundary, and one component that moves between arriving in the same response and arriving on a request of its own by changing one word.
