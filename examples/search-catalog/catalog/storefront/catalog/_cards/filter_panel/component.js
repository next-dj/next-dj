document.querySelectorAll("[data-filter-form]").forEach(function (form) {
  // Auto-submit on checkbox / select change. Number and search fields
  // commit on Enter through the browser's default form-submit behaviour.
  form.addEventListener("change", function (event) {
    var el = event.target;
    if (el.matches('input[type="checkbox"], select') && form.checkValidity()) {
      form.submit();
    }
  });

  // Live constraint validation for the search field. The input declares
  // minlength=3, but we treat the empty string as "no filter" — so we
  // only enforce the length once the user has typed something.
  const search = form.querySelector('input[name="q"]');
  const help = form.querySelector("[data-filter-help]");
  if (!search || !help) {
    return;
  }

  const defaultMessage = search.dataset.helpDefault || "";
  const tooShortTemplate = search.dataset.helpTooshort || "";
  const minLength = parseInt(search.getAttribute("minlength"), 10) || 0;

  const TONES = {
    idle: "text-slate-500",
    error: "text-rose-600",
    ok: "text-emerald-600",
  };

  function setState(message, tone) {
    help.textContent = message;
    help.classList.remove(TONES.idle, TONES.error, TONES.ok);
    help.classList.add(TONES[tone]);
  }

  function update() {
    var length = search.value.length;

    if (length === 0) {
      search.setCustomValidity("");
      setState(defaultMessage, "idle");
      return;
    }

    if (length < minLength) {
      var remaining = minLength - length;
      var message = tooShortTemplate.replace("{min}", remaining);
      search.setCustomValidity(message);
      setState(message, "error");
      return;
    }

    search.setCustomValidity("");
    setState("Looks good — press Enter or click Apply filters.", "ok");
  }

  search.addEventListener("input", update);
  update();
});
