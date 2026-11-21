import { createHistory } from "../history";
import type { Router } from "../router";

const next = window.Next as typeof window.Next & { $history?: true; router?: Router };
if (!next.$history) {
  next.$history = true;
  next.router?._install(createHistory);
}
