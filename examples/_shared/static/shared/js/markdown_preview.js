// Live Markdown preview shared by every example that ships a markdown_preview
// block. Both wiki and multi-tenant point a `scripts = [...]` entry at this one
// file, so the client behaviour lives in a single place while each app keeps
// its own server-side render in component.py.
//
// The work registers through Next.partial.onMount. The runtime runs this over
// the initial DOM and over every subtree it later inserts, so a preview that
// re-renders inside a morphed form is rebound the same way the first render
// was. There is no document.querySelectorAll scan at load that a partial
// insertion would leave behind.

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
  const form = root.closest("form");
  const textarea = form && form.querySelector("textarea");
  if (!target || !textarea || root.dataset.previewBound) {
    return;
  }
  root.dataset.previewBound = "true";
  const update = function () {
    renderMarkdown(textarea.value || "", target);
  };
  textarea.addEventListener("input", update);
  update();
}

window.Next.partial.onMount("[data-markdown-preview]", bindPreview);
