// DOM event predicates shared by the link and form triggers and the layer openers.

/**
 * Whether a click on a link is the plain activation the runtime may take over.
 *
 * A modifier, a middle click, a handler that cancelled it, a foreign target and a
 * download all ask the browser for its own behaviour, so the link stays a link.
 */
export function isPlainActivation(event: Event, link: Element): boolean {
  if (event.defaultPrevented) return false;
  if (
    event instanceof MouseEvent &&
    (event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey)
  )
    return false;
  return inPlace(link.getAttribute("target")) && !link.hasAttribute("download");
}

/** Whether a browsing-context name keeps the navigation in the current page. */
export function inPlace(target: string | null): boolean {
  const name = target?.toLowerCase() ?? "";
  return name === "" || name === "_self";
}
