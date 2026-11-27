// The slot markers of the layout chain, the comments a routed render encloses each
// layout slot with. A navigation asks for the same slot several times, so the last pair
// found is kept and checked instead of walking the whole document again.

import type { Target } from "./apply";
import type { Bounds } from "./morph";
import { ATTR_LAYOUTS, SLOT_MARK } from "./protocol";

export type Layout = [id: string, digest: string, responseId: string];

// The chain a routed document rendered, read from its layouts block, empty for a page
// the router did not render or a block the server never filled.
export function layoutsOf(doc: Document): Layout[] {
  try {
    const chain: unknown = JSON.parse(
      doc.querySelector(`script[${ATTR_LAYOUTS}]`)?.textContent ?? "[]",
    );
    return Array.isArray(chain) ? (chain as Layout[]) : [];
  } catch {
    return [];
  }
}

let last: Bounds | undefined;

// A pair still bounds its slot while its markers read the same under a live parent.
const holds = (bounds: Bounds | undefined, open: string): bounds is Bounds =>
  bounds?.[0].isConnected === true &&
  bounds[1]?.parentNode === bounds[0] &&
  bounds[2]?.parentNode === bounds[0] &&
  (bounds[1] as Comment).data === open &&
  (bounds[2] as Comment).data === `/${open}`;

export function markers(
  root: Document,
  id: string,
  responseId: string,
): Bounds | undefined {
  const open = `${SLOT_MARK} ${id} ${responseId}`;
  if (holds(last, open) && last[0].ownerDocument === root) return last;
  const walk = root.createTreeWalker(root, NodeFilter.SHOW_COMMENT);
  let start: Node | undefined;
  for (let node = walk.nextNode(); node; node = walk.nextNode()) {
    const data = (node as Comment).data;
    if (data === open) start = node;
    else if (start && data === `/${open}`)
      return start.parentNode === node.parentNode
        ? (last = [node.parentElement as Element, start, node])
        : undefined;
  }
  return undefined;
}

/** The bounds of a navigation target, the body or the slot of a layout in the chain. */
export function locate(
  root: Document,
  chain: readonly Layout[],
  target: Target,
): Bounds | undefined {
  if (target.body) return [root.body, null, null];
  const id = target.slot ?? "";
  const responseId = chain.find((layout) => layout[0] === id)?.[2];
  return responseId === undefined ? undefined : markers(root, id, responseId);
}
