import { inPlace } from "./events";

export function submitUrl(event: Event): URL | undefined {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || event.defaultPrevented) return undefined;
  const { submitter } = event as SubmitEvent;
  const pick = (name: string): string | null | undefined =>
    [submitter?.getAttribute(`form${name}`), form.getAttribute(name)].find(Boolean);
  if (
    (pick("method") ?? "get").toLowerCase() !== "get" ||
    !inPlace(pick("target") ?? null) ||
    form.getAttributeNames().some((name) => name.startsWith("data-next-"))
  )
    return undefined;
  const url = new URL(pick("action") ?? location.href, document.baseURI);
  const query = new URLSearchParams();
  for (const [name, value] of new FormData(form, submitter))
    if (typeof value === "string") query.append(name, value);
  url.search = query.toString();
  return url;
}
