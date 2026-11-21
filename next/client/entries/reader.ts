import { createReader } from "../reader";

const next = window.Next as typeof window.Next & { $reader?: true };
if (!next.$reader) {
  next.$reader = true;
  createReader(next);
}
