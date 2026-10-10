// The four page-specific head tags a meta op updates and a layer saves and restores.
// Social and JSON-LD tags keep their rendered value, since their crawlers run no JS.

export type HeadKey = "title" | "description" | "canonical" | "robots";

/** A full head snapshot, null for a tag the document does not carry. */
export type Head = Record<HeadKey, string | null>;

/** The tags one meta op sets. An absent key leaves its tag unchanged. */
export type HeadPatch = Partial<Head>;

type TagKey = Exclude<HeadKey, "title">;

const TAG_KEYS: readonly TagKey[] = ["description", "canonical", "robots"];

// Per key, the tag name, the attribute identifying the tag, and the value attribute.
const TAGS: Record<TagKey, readonly [string, string, string]> = {
  description: ["meta", "name", "content"],
  canonical: ["link", "rel", "href"],
  robots: ["meta", "name", "content"],
};

/** Collapse whitespace as document.title does, so two titles compare reliably. */
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

/** Create or update each tag the patch sets, and remove each one set to null. */
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

/** Narrow a wire meta op to a head patch, ignoring a value of the wrong type. */
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
