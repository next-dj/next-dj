import { createMore } from "../more";

const next = window.Next as typeof window.Next & { $more?: true };
if (!next.$more) {
  next.$more = true;
  createMore(next);
}
