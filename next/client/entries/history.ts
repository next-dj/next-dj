import { createHistory } from "../history";
import { once } from "../protocol";
import type { Router } from "../router";

const next = window.Next as typeof window.Next & { router?: Router };
once("history", () => next.router?._install(createHistory));
