from library.models import Book

from next.deps import DObject
from next.pages import proxy


proxy.login_required()
proxy.permission_required("shadcn_admin.use_admin")
proxy.permission_required("library.change_book", obj=DObject[Book])
