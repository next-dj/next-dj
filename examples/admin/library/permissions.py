from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from library.models import Book, Chapter, Tag
from next.permissions import add_perm


type Visitor = AbstractBaseUser | AnonymousUser


def uses_admin(user: Visitor) -> bool:
    """Return whether `user` works the admin, asked of the rule the admin registers."""
    return user.has_perm("shadcn_admin.use_admin")


def holds_draft(user: Visitor, book: Book) -> bool:
    """Return whether `book` is a draft assigned to `user` or to no editor at all."""
    return book.status == Book.DRAFT and book.editor_id in {None, user.pk}


def reads_book(user: Visitor, book: Book | None) -> bool:
    """Return whether `user` may read `book`, a draft only when assigned to them."""
    return uses_admin(user) and (
        book is None or book.status != Book.DRAFT or book.editor_id == user.pk
    )


def edits_book(user: Visitor, book: Book | None) -> bool:
    """Return whether `user` may edit `book`, any book at model level, else a draft."""
    return uses_admin(user) and (book is None or holds_draft(user, book))


def edits_chapter(user: Visitor, chapter: Chapter | None) -> bool:
    """Return whether `user` may edit `chapter`, whose book `holds_draft` must pass."""
    return uses_admin(user) and (chapter is None or holds_draft(user, chapter.book))


def deletes_tag(user: Visitor, tag: Tag | None) -> bool:
    """Return whether `user` may delete `tag`, which no book past draft may carry."""
    return uses_admin(user) and (
        tag is None or not tag.books.exclude(status=Book.DRAFT).exists()
    )


for verb in ("add", "change", "delete"):
    add_perm(f"library.{verb}_author", uses_admin)
add_perm("library.add_tag", uses_admin)
add_perm("library.change_tag", uses_admin)
add_perm("library.delete_tag", deletes_tag)

add_perm("library.add_book", uses_admin)
add_perm("library.view_book", reads_book)
add_perm("library.change_book", edits_book)
add_perm("library.delete_book", edits_book)
add_perm("library.add_chapter", uses_admin)
add_perm("library.change_chapter", edits_chapter)
add_perm("library.delete_chapter", edits_chapter)
