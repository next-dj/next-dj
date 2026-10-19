from library.models import Book

from next.deps import DObject
from next.pages import proxy


proxy.permission_required("library.change_book", obj=DObject[Book])
