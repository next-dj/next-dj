import { createReload } from "../reload";

const next = window.Next as typeof window.Next & { $reload?: true };
if (!next.$reload) {
  next.$reload = true;
  createReload(next);
}
