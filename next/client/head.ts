// The four URL-bound head tags a meta op syncs and a layer snapshots. Social and
// JSON-LD tags stay as first rendered, since their crawlers never run the runtime.

export type HeadKey = "title" | "description" | "canonical" | "robots";

/** A full head snapshot, null for a tag the document does not carry. */
export type Head = Record<HeadKey, string | null>;

/** The tags one meta op names, an absent key left as it is. */
export type HeadPatch = Partial<Head>;

type TagKey = Exclude<HeadKey, "title">;

const TAG_KEYS: readonly TagKey[] = ["description", "canonical", "robots"];

// The tag, the attribute naming it by its key, then the attribute holding the value.
const TAGS: Record<TagKey, readonly [string, string, string]> = {
  description: ["meta", "name", "content"],
  canonical: ["link", "rel", "href"],
  robots: ["meta", "name", "content"],
};

/** Collapse whitespace the way document.title reads, so a comparison is stable. */
export function normTitle(value: string): string {
  return value.replace(/[\t\n\f\r ]+/g, " ").trim();
}

function find(doc: Document, key: TagKey): Element | null {
  const [tag, name] = TAGS[key];
  return doc.head.querySelector(`${tag}[${name}="${key}"]`);
}

/** Read the four tags off the live document. */
export function readHead(doc: Document): Head {
  const title = doc.head.querySelector("title") && normTitle(doc.title);
  const head = { title } as Head;
  for (const key of TAG_KEYS) {
    head[key] = find(doc, key)?.getAttribute(TAGS[key][2]) ?? null;
  }
  return head;
}

/** Upsert each named tag, a null value removing it. */
export function writeHead(doc: Document, patch: HeadPatch): void {
  const title = patch.title;
  if (title === null) doc.head.querySelector("title")?.remove();
  else if (title !== undefined) doc.title = title;
  for (const key of TAG_KEYS) {
    const value = patch[key];
    if (value === undefined) continue;
    const [tag, name, attr] = TAGS[key];
    let el = find(doc, key);
    if (value === null) {
      el?.remove();
      continue;
    }
    if (el === null) {
      el = doc.createElement(tag);
      el.setAttribute(name, key);
      doc.head.append(el);
    }
    el.setAttribute(attr, value);
  }
}

/** Narrow a wire meta op to a head patch, a malformed value read as absent. */
export function readPatch(op: Partial<Record<HeadKey, unknown>>): HeadPatch {
  const patch: HeadPatch = {};
  for (const key of ["title", ...TAG_KEYS] as const) {
    const value = op[key];
    if (value === null) patch[key] = null;
    else if (typeof value === "string")
      patch[key] = key === "title" ? normTitle(value) : value;
  }
  return patch;
}
