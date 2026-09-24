/* Co-located client for the markdown_preview shell. The server render owns
   the first paint, this keeps the pane in sync with the textarea on every
   keystroke. It registers through Next.partial.onMount rather than a load-time
   querySelectorAll, so a pane that re-renders inside a morphed form is rebound
   the same way the first render was. The pairing lives in a WeakMap rather than
   a data attribute, which the morph strips when the server markup lacks it, so
   a re-render adds no second listener to a control already wired. */

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

const boundControls = new WeakMap();

function sourceOf(root) {
  const previous = root.previousElementSibling;
  if (previous && previous.matches("[data-markdown-source]")) {
    return previous;
  }
  const parent = root.parentElement;
  const nearby = parent && parent.querySelector("[data-markdown-source]");
  if (nearby) {
    return nearby;
  }
  const form = root.closest("form");
  return (form && form.querySelector("[data-markdown-source]")) || null;
}

function bindPreview(root) {
  const target = root.querySelector(".markdown-body");
  const textarea = sourceOf(root);
  if (!target || !textarea || boundControls.get(root) === textarea) {
    return;
  }
  boundControls.set(root, textarea);
  const update = function () {
    renderMarkdown(textarea.value || "", target);
  };
  textarea.addEventListener("input", update);
  update();
}

window.Next.partial.onMount("[data-markdown-preview]", bindPreview);
