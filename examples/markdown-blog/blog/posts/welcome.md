---
title: Welcome to the blog
author: Ada Lovelace
date: 2026-01-12
updated: 2026-03-02
description: A tour of the blog, where every post is a Markdown file and one dynamic route serves them all.
keywords: next.dj, markdown, django
---
This is a demo blog built on **next-dj**. Every post you are reading is a Markdown file
under `blog/posts/`, and one dynamic route at `pages/posts/[slug]/` serves them all.

## Why Markdown?

Markdown keeps the authoring story simple:

- No CMS.
- No database migrations for content.
- A pull request is the publishing pipeline.

## How it works

The route declares two context callables:

1. `article` reads the file the URL names and parses its front matter once.
2. `post` hands the title and the slug to the share button through
   `window.Next.context`, marked `serialize=True`.

The front matter feeds the head as well: the description, the keywords, the Open Graph
article dates and the `BlogPosting` structured data all come from those few lines.

Read the second post for a shorter example.

## Embeds

A post may embed a widget the way a third-party snippet does, with a script tag of its
own. The router cannot run such a script in a page it swaps in, so it loads this page
whole and the widget below fills in.

<div data-embed>The widget is loading.</div>
<script src="/static/blog/embed.js"></script>
