import { createHistory } from "../history";
import { once } from "../protocol";
import type { Router } from "../router";
import { submitUrl } from "../submit";

const next = window.Next as typeof window.Next & { router?: Router };
once("history", () =>
  next.router?._install((host) => ({ ...createHistory(host), form: submitUrl })),
);
