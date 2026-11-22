import type { Target } from "./apply";
import type { Bounds } from "./morph";

export type Layout = [id: string, digest: string, seal: string];

const SLOT = "nx:s";

export function markers(root: Document, id: string, seal: string): Bounds | undefined {
  const walk = root.createTreeWalker(root, NodeFilter.SHOW_COMMENT);
  let start: Node | undefined;
  for (let node = walk.nextNode(); node; node = walk.nextNode()) {
    const data = (node as Comment).data;
    if (data === `${SLOT} ${id} ${seal}`) start = node;
    else if (start && data === `/${SLOT} ${id} ${seal}`)
      return start.parentNode === node.parentNode
        ? [node.parentElement as Element, start, node]
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
  const seal = chain.find((layout) => layout[0] === id)?.[2];
  return seal === undefined ? undefined : markers(root, id, seal);
}
