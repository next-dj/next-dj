// The client trip of a deferred boundary: fetch its content once it loads or comes into
// view, retry an incomplete one with jitter, and back off on overload.

import { defaultReload } from "./adapters";
import type {
  Applied,
  BeforeApply,
  InnerPatch,
  MorphPatch,
  ReplacePatch,
} from "./apply";
import { focusable } from "./events";
import type { Kernel } from "./kernel";
import { ORDER_ADOPT, ORDER_STAMP } from "./kernel";
import {
  ATTR_ADOPTED,
  ATTR_BOUNDARY,
  ATTR_BUSY,
  ATTR_CARRIER,
  ATTR_DEFER,
  ATTR_ERROR,
  ATTR_FETCH,
  ATTR_PREFIX,
  ATTR_RETRY,
  ATTR_STATE,
  ATTR_TOKEN,
  ATTR_ZONE,
  CHUNK_KIND,
  CHUNK_STATE,
  HEADER_BOUNDARY,
  HEADER_ZONE,
  NAVIGATED_FLAG,
  TOKEN_FIELD,
  matching,
  streaming,
} from "./protocol";
import type { Outcome, WireRequest } from "./wire";

const WRAPPER = `[${ATTR_BOUNDARY}][${ATTR_TOKEN}]`;
const ZONE = `[${ATTR_ZONE}]`;

export type DeferHost = Pick<
  Kernel,
  "partial" | "serve" | "hook" | "watch" | "diagnostics"
>;

type BoundaryOp = (InnerPatch | MorphPatch | ReplacePatch) & {
  [CHUNK_STATE]?: string;
  [CHUNK_KIND]?: string;
  token?: string;
};

const dropped = (): Outcome => ({ status: "dropped", applied: false });

const statusOf = ({ error }: Outcome): number =>
  error?.kind === "http" ? error.status : 0;

const rearm = ({ error }: Outcome): { status: string } => ({
  status: error !== undefined && error.kind !== "http" ? "error" : "ok",
});

export function createDefer(
  kernel: DeferHost,
  listening: AddEventListenerOptions = {},
  reload = defaultReload(),
): void {
  const doc = document;
  const { partial } = kernel;
  const send = (request: WireRequest): Promise<Outcome> => partial.fetch(request);
  const seen = new WeakMap<Element, string | null>();
  const queued: Element[] = [];
  let halted = false;

  // A 409 or an expired token needs a fresh page, reloaded once per session.
  function reloadOnce(): boolean {
    try {
      if (sessionStorage.getItem(NAVIGATED_FLAG) !== null) return false;
      sessionStorage.setItem(NAVIGATED_FLAG, "1");
    } catch {
      return false;
    }
    reload();
    return true;
  }

  // Focus inside a boundary its content replaced goes back to the new boundary.
  async function settled(el: Element, sent: WireRequest): Promise<Outcome> {
    const active = doc.activeElement;
    const inside = el.contains(active) && active !== el ? active : null;
    const outcome = await send(sent);
    const target = doc.getElementById(el.id) ?? (el as HTMLElement);
    if (inside !== null && !inside.isConnected) focusable(target).focus();
    return outcome;
  }

  // The token travels where the boundary's carrier says: a header, the query or a body.
  async function boundary(el: Element, token: string, auto: boolean): Promise<Outcome> {
    const [place, field = TOKEN_FIELD] = (el.getAttribute(ATTR_CARRIER) ?? "").split(
      ":",
    );
    const url = new URL(partial.layers.urlFor(el), location.href);
    const headers: Record<string, string> = { [HEADER_ZONE]: el.id };
    const sent: WireRequest = { url: "", headers, queue: el.id, boundaries: [el] };
    if (place === "query") url.searchParams.set(field, token);
    else if (place === "body") {
      sent.method = "POST";
      sent.abortable = true;
      sent.body = new URLSearchParams({ [field]: token });
    } else headers[HEADER_BOUNDARY] = token;
    sent.url = url.pathname + url.search;
    const outcome = await settled(el, sent);
    const status = statusOf(outcome);
    // Only a DEBUG server explains a refused token, and only to the dev diagnostics.
    const { error } = outcome;
    if ((status === 403 || status === 409) && error?.kind === "http" && error.body)
      kernel.diagnostics()?.rejected(el, error.body.trim());
    if (status === 409) reloadOnce();
    if (status === 403 && !(auto && reloadOnce()))
      el.setAttribute(ATTR_ERROR, "forbidden");
    return outcome;
  }

  function request(el: Element, auto = false): Promise<Outcome> {
    const token = el.getAttribute(ATTR_TOKEN);
    const zone = el.getAttribute(ATTR_ZONE);
    if (token !== null) return boundary(el, token, auto);
    if (zone === null) return Promise.resolve(dropped());
    return settled(el, { url: partial.layers.urlFor(el), zone });
  }

  function retry(control: Element): void {
    const wrapper = control.closest(`[${ATTR_BOUNDARY}]`);
    const owner = wrapper?.hasAttribute(ATTR_TOKEN) ? wrapper : control.closest(ZONE);
    if (owner === null) reload();
    else void request(owner);
  }

  // The load boundaries of one task share one flush, after the parse unless the page
  // streams, where the document is still parsing as the boundaries arrive.
  function load(el: Element): void {
    if (queued.push(el) > 1) return;
    const flush = (): void =>
      queueMicrotask(() => {
        for (const wrapper of queued.splice(0)) void request(wrapper, true);
      });
    if (doc.readyState === "loading" && !streaming(doc))
      doc.addEventListener("DOMContentLoaded", flush, { ...listening, once: true });
    else flush();
  }

  // Each boundary a stream left incomplete is retried once with jitter, and a 429 or
  // a 5xx stops the retries, so a struggling server is not stampeded.
  function incomplete(): void {
    if (halted) return;
    for (const el of matching(doc, `[${ATTR_ERROR}=incomplete]`)) {
      const target = el.hasAttribute(ATTR_TOKEN) ? el : el.closest(ZONE);
      if (target === null || seen.has(target)) continue;
      seen.set(target, null);
      setTimeout(() => {
        if (halted) return;
        void request(target, true).then((outcome) => {
          const status = statusOf(outcome);
          if (status === 429 || status >= 500) halted = true;
        });
      }, Math.random() * 2000);
    }
  }

  const later = (): void => void setTimeout(incomplete);

  function activate(el: Element): void {
    const mode = el.getAttribute(ATTR_DEFER);
    const token = el.getAttribute(ATTR_TOKEN);
    if (seen.get(el) === token) return;
    seen.set(el, token);
    if (mode === "load") load(el);
    if (mode === "visible") kernel.watch(el, () => request(el, true).then(rearm));
  }

  function eachBoundary(
    { envelope }: BeforeApply | Applied,
    run: (op: BoundaryOp, el: Element) => void,
  ): void {
    for (const op of envelope.ops as readonly BoundaryOp[]) {
      const el = doc.getElementById(op.target?.boundary ?? "");
      if (el !== null) run(op, el);
    }
  }

  // An adopted boundary is replaced by its content's root, so that root takes over the
  // id and the data-next attributes the boundary carried.
  function carry(op: BoundaryOp, el: Element): void {
    if (op.op !== "morph" || !el.hasAttribute(ATTR_ADOPTED)) return;
    const parsed = doc.createElement("template");
    parsed.innerHTML = op.html ?? "";
    const root = parsed.content.firstElementChild;
    if (root === null) return;
    for (const { name, value } of el.attributes) {
      if (
        (name === "id" ||
          (name.startsWith(ATTR_PREFIX) && !name.startsWith(ATTR_BUSY))) &&
        !root.hasAttribute(name)
      )
        root.setAttribute(name, value);
    }
    op.html = parsed.innerHTML;
  }

  function stamp(op: BoundaryOp, el: Element): void {
    if (op[CHUNK_STATE] !== undefined)
      el.setAttribute(ATTR_STATE, op[CHUNK_STATE] === "error" ? "error" : "ready");
    if (op[CHUNK_STATE] === "error")
      el.setAttribute(ATTR_ERROR, op[CHUNK_KIND] ?? "error");
    if (op.token === undefined) return;
    el.setAttribute(ATTR_TOKEN, op.token);
  }

  function onClick(event: Event): void {
    const target = event.target;
    const control =
      target instanceof Element
        ? target.closest(`[${ATTR_FETCH}],[${ATTR_RETRY}]`)
        : null;
    if (control === null) return;
    const wrapper = control.hasAttribute(ATTR_FETCH)
      ? control.closest(WRAPPER)
      : control;
    if (wrapper === null) return;
    event.preventDefault();
    if (wrapper === control) retry(control);
    else void request(wrapper);
  }

  // The harness tears the chunk down between cases, a page keeps it armed.
  TEST: listening.signal?.addEventListener("abort", () => (halted = true));
  doc.addEventListener("click", onClick, listening);
  doc.addEventListener("partial:settled", later, listening);
  addEventListener("load", later, listening);
  kernel.hook("before", ORDER_ADOPT, (detail) => eachBoundary(detail, carry));
  kernel.hook("applied", ORDER_STAMP, (applied) => eachBoundary(applied, stamp));
  kernel.serve("fetch", (el) => request(el));
  partial.onMount(`${WRAPPER}[${ATTR_STATE}=idle]`, activate);
  incomplete();
}
