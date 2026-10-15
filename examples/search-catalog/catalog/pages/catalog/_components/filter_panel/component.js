// Live constraint-validation hints for the search field. The auto-submit and
// debounce now ride the data-next-* trigger attributes on the form and select,
// so this script only narrates the minlength rule. Next.partial.onMount rewires
// each panel when it mounts in the initial DOM or arrives in a morphed zone, so
// there is no module-load scan that a partial insertion would leave behind.

const TONES = {
  idle: "text-slate-500",
  error: "text-rose-600",
  ok: "text-emerald-600",
};

function bindSearchHints(panel) {
  const search = panel.querySelector('input[name="q"]');
  const help = panel.querySelector("[data-filter-help]");
  if (!search || !help || search.dataset.helpBound) {
    return;
  }
  search.dataset.helpBound = "true";

  const defaultMessage = search.dataset.helpDefault || "";
  const tooShortTemplate = search.dataset.helpTooshort || "";
  const minLength = parseInt(search.getAttribute("minlength"), 10) || 0;

  function setState(message, tone) {
    help.textContent = message;
    help.classList.remove(TONES.idle, TONES.error, TONES.ok);
    help.classList.add(TONES[tone]);
  }

  function update() {
    const length = search.value.length;
    if (length === 0) {
      search.setCustomValidity("");
      setState(defaultMessage, "idle");
      return;
    }
    if (length < minLength) {
      const remaining = minLength - length;
      const message = tooShortTemplate.replace("{min}", remaining);
      search.setCustomValidity(message);
      setState(message, "error");
      return;
    }
    search.setCustomValidity("");
    setState("Looks good — typing filters the catalog live.", "ok");
  }

  search.addEventListener("input", update);
  update();
}

window.Next.partial.onMount("[data-filter-panel]", bindSearchHints);
