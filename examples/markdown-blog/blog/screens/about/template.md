# About

A demo blog built on next-dj. Every post is a Markdown file under `blog/posts/` with a
few lines of front matter on top, and one dynamic route at `screens/posts/[slug]/`
reads the file the URL names.

This page takes the other road. It is a `template.md` next to a `page.py`, and a custom
template loader renders it as the page body, so a static page needs no template of its
own.

The share button on every post reads the post title from `window.Next.context.post`,
populated by a `@context(serialize=True)` declaration.
