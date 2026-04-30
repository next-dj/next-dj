(function () {
  "use strict";

  const EMPTY = "<p class='text-slate-400 italic'>Nothing to preview yet.</p>";
  const UNSAFE_HREF = /href="\s*(?:javascript|data|vbscript):[^"]*"/gi;

  function escapeHtml(value) {
    return value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function neutraliseHrefs(html) {
    return html.replace(UNSAFE_HREF, 'href="#"');
  }

  function renderMarkdown(body, target) {
    if (!body.trim()) {
      target.innerHTML = EMPTY;
      return;
    }
    if (typeof window.marked === "undefined") {
      target.textContent = body;
      return;
    }
    const escaped = escapeHtml(body);
    const html = window.marked.parse(escaped, { gfm: true, breaks: false });
    target.innerHTML = neutraliseHrefs(html);
  }

  function bindPreview(root) {
    const target = root.querySelector(".markdown-body");
    const textarea = document.querySelector('textarea[name="body"]');
    if (!target || !textarea) {
      return;
    }
    const update = function () {
      renderMarkdown(textarea.value || "", target);
    };
    textarea.addEventListener("input", update);
    update();
  }

  function init() {
    const roots = document.querySelectorAll("[data-markdown-preview]");
    roots.forEach(bindPreview);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
