from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from library.models import Book, Chapter, Tag
from next.permissions import add_perm, predicate


type Visitor = AbstractBaseUser | AnonymousUser


@predicate
def is_staff(user: Visitor) -> bool:
    """Return whether `user` works the admin, all the catalog asks of anyone."""
    return user.is_staff


@predicate
def no_row(_user: Visitor, obj: object) -> bool:
    """Return whether the question names no row, the model-level question."""
    return obj is None


@predicate
def is_draft(_user: Visitor, book: Book | None) -> bool:
    """Return whether `book` is still a draft, the one status an editor may touch."""
    return book is not None and book.status == Book.DRAFT


@predicate
def in_draft_book(_user: Visitor, chapter: Chapter | None) -> bool:
    """Return whether `chapter` belongs to a draft book, so it follows its book."""
    return chapter is not None and chapter.book.status == Book.DRAFT


@predicate
def carried_by_drafts_only(_user: Visitor, tag: Tag | None) -> bool:
    """Return whether no book past draft carries `tag`, so removing it touches none."""
    return tag is not None and not tag.books.exclude(status=Book.DRAFT).exists()


for verb in ("add", "change", "delete"):
    add_perm(f"library.{verb}_author", is_staff)
add_perm("library.add_tag", is_staff)
add_perm("library.change_tag", is_staff)
add_perm("library.delete_tag", is_staff & (no_row | carried_by_drafts_only))

add_perm("library.add_book", is_staff)
add_perm("library.change_book", is_staff & (no_row | is_draft))
add_perm("library.delete_book", is_staff & (no_row | is_draft))
add_perm("library.add_chapter", is_staff)
add_perm("library.change_chapter", is_staff & (no_row | in_draft_book))
add_perm("library.delete_chapter", is_staff & (no_row | in_draft_book))
