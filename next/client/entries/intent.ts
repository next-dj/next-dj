import { createCurrent } from "../current";
import { createPrefetch } from "../prefetch";
import { once } from "../protocol";
import type { Router } from "../router";

const next = window.Next as typeof window.Next & { router?: Router };
once("intent", () =>
  next.router?._install((host) => {
    createCurrent(host);
    return createPrefetch(host);
  }),
);
