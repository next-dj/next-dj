# Welcome to the blog

This is a demo blog built on **next-dj**. Every post you are reading is a plain
`post.md` file on disk. The per-post `page.py` reads the Markdown, converts it to
HTML at import time, and registers three pieces of template context.

## Why Markdown?

Markdown keeps the authoring story simple:

- No CMS.
- No database migrations for content.
- A pull request is the publishing pipeline.

## How it works

Each post exposes:

1. `post` — metadata (title, slug, URL name) marked `serialize=True` so the
   share button can read it from `window.Next.context`.
2. `post_html` — the rendered HTML body, spliced into the nested post layout.
3. `reading_minutes` — an estimated read time shown in the meta bar.

Read the second post for a shorter example.
