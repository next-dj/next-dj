# Welcome to the blog

This is a demo blog built on **next-dj**. Every post you are reading is a plain
`template.md` file on disk, picked up by a custom template loader. The per-post
`page.py` never touches the Markdown body, it only registers template context.

## Why Markdown?

Markdown keeps the authoring story simple:

- No CMS.
- No database migrations for content.
- A pull request is the publishing pipeline.

## How it works

Each post exposes two context callables:

1. `post` — metadata (title, slug, URL name) marked `serialize=True` so the
   share button can read it from `window.Next.context`.
2. `reading_minutes` — an estimated read time shown in the meta bar.

The body itself is not context. `MarkdownTemplateLoader` finds `template.md` next
to the `page.py` and renders it as the page body.

Read the second post for a shorter example.
