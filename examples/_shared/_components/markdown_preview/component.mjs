/* Co-located client for the markdown_preview shell. The server render owns
   the first paint, this keeps the pane in sync with the textarea on every
   keystroke. It registers through Next.partial.onMount rather than a load-time
   querySelectorAll, so a preview that re-renders inside a morphed form is
   rebound the same way the first render was, with no stale listener left by a
   swap. */

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
