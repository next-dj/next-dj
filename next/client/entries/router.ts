import { once } from "../protocol";
import { createRouter } from "../router";
import type { Router } from "../router";

const next = window.Next as typeof window.Next & { router?: Router };
once("router", () => {
  next.router = createRouter(next);
});
