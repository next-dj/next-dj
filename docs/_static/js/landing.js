/* next.dj docs — landing page interactions.
   Loaded on every page but a no-op wherever the landing markup is absent. */

(function () {
  "use strict";

  function initCopy() {
    document.querySelectorAll(".ndj-install").forEach(function (box) {
      var btn = box.querySelector(".ndj-copy");
      var text = box.getAttribute("data-copy");
      if (!btn || !text || !navigator.clipboard) {
        return;
      }
      btn.addEventListener("click", function () {
        navigator.clipboard.writeText(text).then(function () {
          box.classList.add("is-copied");
          window.setTimeout(function () {
            box.classList.remove("is-copied");
          }, 1600);
        }, function () {});
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCopy);
  } else {
    initCopy();
  }
})();
