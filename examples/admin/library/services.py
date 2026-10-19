from library.models import Chapter


def append_chapter(chapter: Chapter) -> Chapter:
    """Save `chapter` as the last chapter of its book, numbered one past the end."""
    chapter.number = chapter.book.chapters.next_number()
    chapter.save()
    return chapter
