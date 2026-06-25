// Client handler for the `metric-pulse` custom verb the window-filter form
// emits. `register_patch_op("metric-pulse")` in `obs/apps.py` clears the
// server side, this `defineOp` clears the client side, so the verb rides the
// same envelope and apply pipeline as a built-in op. The handler reads the
// payload the server authored (`window`, `selector`) and flashes the morphed
// totals so a glance lands on the numbers that just changed.
//
// Co-located as `template.js` next to the live page, so it loads only on
// `/stats/` and survives a `live-totals` morph because the morph never
// reloads the script. No browser run is asserted by the server-side e2e
// suite, the verb's wire shape is what those tests pin.

window.Next?.partial?.defineOp("metric-pulse", function (patch, ctx) {
  const root = patch.selector ? ctx.root.querySelector(patch.selector) : null;
  if (!root) return;
  root.dataset.pulseWindow = patch.window ?? "";
  root.classList.remove("metric-pulse");
  void root.offsetWidth;
  root.classList.add("metric-pulse");
  ctx.dispatch("metric-pulse", { window: patch.window });
});
