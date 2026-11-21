import { createCurrent } from "../current";
import { createPrefetch } from "../prefetch";
import type { Router } from "../router";
import { submitUrl } from "../submit";

const next = window.Next as typeof window.Next & { $intent?: true; router?: Router };
if (!next.$intent) {
  next.$intent = true;
  next.router?._install((host) => {
    createCurrent(host);
    return { ...createPrefetch(host), form: submitUrl };
  });
}
