from library.models import Book, Chapter


def append_chapter(chapter: Chapter, book: Book) -> Chapter:
    """Save `chapter` as the last chapter of `book`, numbered one past the end."""
    chapter.book = book
    chapter.number = book.chapters.next_number()
    chapter.save()
    return chapter
