import { createRouter } from "../router";
import type { Router } from "../router";

const next = window.Next as typeof window.Next & { $router?: true; router?: Router };
if (!next.$router) {
  next.$router = true;
  next.router = createRouter(next);
}
