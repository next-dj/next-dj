from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tests.support.boundaries import UNREADABLE, app_config


if TYPE_CHECKING:
    from collections.abc import Mapping


_SHOP_DEPENDS = """from next.deps import Depends


def render(client=Depends("crm_client")):
    pass
"""

_CRM_DEPS = """from next.deps import resolver


resolver.register_dependency("crm_client", lambda: None)
"""


@dataclass(frozen=True, slots=True)
class EdgeKindCase:
    """One kind of edge from `shop` to `crm` and where its first edge stands.

    `shop` and `crm` are the files each app adds, `load` the modules run first.
    """

    id: str
    shop: dict[str, str]
    file: str
    line: int
    crm: dict[str, str] = field(default_factory=dict)
    load: tuple[str, ...] = ()


EDGE_KIND_CASES: tuple[EdgeKindCase, ...] = (
    EdgeKindCase(
        "import", {"views.py": "\n\nfrom crm.services import notify\n"}, "views.py", 3
    ),
    EdgeKindCase(
        "fk",
        {
            "models.py": (
                "from django.db import models\n\n\n"
                "class Order(models.Model):\n"
                '    buyer = models.ForeignKey("crm.Client", on_delete=models.CASCADE)\n'
            )
        },
        "models.py",
        5,
    ),
    EdgeKindCase(
        "get_model",
        {
            "services.py": (
                "from django.apps import apps\n\n"
                'client = apps.get_model("crm", "Client")\n'
            )
        },
        "services.py",
        3,
    ),
    EdgeKindCase(
        "signal",
        {
            "signals.py": (
                "from django.db.models.signals import post_save\n"
                "from django.dispatch import receiver\n\n\n"
                '@receiver(post_save, sender="crm.Client")\n'
                "def on_client(sender, **kwargs):\n    pass\n"
            )
        },
        "signals.py",
        5,
    ),
    EdgeKindCase(
        "di",
        {"views.py": _SHOP_DEPENDS},
        "views.py",
        4,
        crm={"deps.py": _CRM_DEPS},
        load=("crm.deps",),
    ),
    EdgeKindCase(
        "zone",
        {
            "pages/page.py": "def render(request):\n    Patches(request).refresh(zone='x')\n"
        },
        "pages/page.py",
        2,
        crm={"pages/page.py": "template = \"{% zone 'x' %}a{% endzone %}\"\n"},
    ),
    EdgeKindCase(
        "component",
        {"pages/template.djx": "\n{% component 'badge' %}"},
        "pages/template.djx",
        2,
        crm={"pages/page.py": "", "pages/_components/badge/component.djx": "badge"},
    ),
    EdgeKindCase(
        "perm", {"perms.py": 'has_perm(user, "crm.view_client")\n'}, "perms.py", 1
    ),
)


@dataclass(frozen=True, slots=True)
class ServiceCase:
    """One return annotation of a service of an isolated app and what W152 says.

    `source` heads the services module, and `reason` ends the message or is `None`.
    """

    id: str
    returns: str
    reason: str | None
    source: str = ""


_MODELS = "from iso.models import Record, Base\n"
_DJANGO = "from django.db import models\nfrom django.db.models import QuerySet\n"
_TYPING = "import typing\nfrom typing import Annotated, Any, Optional\n"
_ITER = "from collections.abc import Iterator\n"
_DATA = "import dataclasses\nfrom dataclasses import dataclass\n"

SERVICE_LEAK_CASES: tuple[ServiceCase, ...] = (
    ServiceCase("model", "Record", "the model iso.models.Record", _MODELS),
    ServiceCase(
        "model_or_none", "Record | None", "the model iso.models.Record", _MODELS
    ),
    ServiceCase(
        "optional", "Optional[Record]", "the model iso.models.Record", _MODELS + _TYPING
    ),
    ServiceCase("list", "list[Record]", "the model iso.models.Record", _MODELS),
    ServiceCase(
        "iterator", "Iterator[Record]", "the model iso.models.Record", _MODELS + _ITER
    ),
    ServiceCase(
        "queryset",
        "QuerySet[Record]",
        "the queryset or manager django.db.models.QuerySet",
        _MODELS + _DJANGO,
    ),
    ServiceCase(
        "manager",
        "models.Manager",
        "the queryset or manager django.db.models.Manager",
        _DJANGO,
    ),
    ServiceCase(
        "from_queryset",
        "RecordManager",
        "the queryset or manager django.db.models.Manager",
        _DJANGO
        + "\n\nclass RecordQuerySet(QuerySet):\n    pass\n\n\n"
        + "RecordManager = models.Manager.from_queryset(RecordQuerySet)\n",
    ),
    ServiceCase(
        "manager_class_from_queryset",
        "Built",
        "the queryset or manager iso.services.Built",
        _DJANGO
        + "\n\nclass Built(models.Manager.from_queryset(QuerySet)):\n    pass\n",
    ),
    ServiceCase(
        "project_queryset",
        "RecordQuerySet",
        "the queryset or manager iso.services.RecordQuerySet",
        _DJANGO + "\n\nclass RecordQuerySet(QuerySet):\n    pass\n",
    ),
    ServiceCase("abstract_model", "Base", "the model iso.models.Base", _MODELS),
    ServiceCase("any", "Any", "typing.Any", _TYPING),
    ServiceCase(
        "extensions_any",
        "Any",
        "typing_extensions.Any",
        "from typing_extensions import Any\n",
    ),
    ServiceCase("object", "object", "builtins.object"),
    ServiceCase("string", '"list[Base]"', "the model iso.models.Base", _MODELS),
    ServiceCase(
        "broken_string", '"list["', "list[, and the check cannot tell what list[ is"
    ),
    ServiceCase(
        "annotated",
        'Annotated[Record, "doc"]',
        "the model iso.models.Record",
        _MODELS + _TYPING,
    ),
    ServiceCase("dict_any", "dict[str, Any]", "typing.Any", _TYPING),
    ServiceCase(
        "type_alias",
        "Records",
        "the queryset or manager django.db.models.QuerySet",
        _MODELS + _DJANGO + "\n\ntype Records = QuerySet[Record]\n",
    ),
    ServiceCase(
        "plain_class",
        "Plain",
        "the class iso.services.Plain, which is no frozen slots dataclass or enum",
        "\n\nclass Plain:\n    pass\n",
    ),
    ServiceCase(
        "frozen_without_slots",
        "Half",
        "the class iso.services.Half, which is no frozen slots dataclass or enum",
        _DATA + "\n\n@dataclass(frozen=True)\nclass Half:\n    pass\n",
    ),
    ServiceCase(
        "slots_without_frozen",
        "Half",
        "the class iso.services.Half, which is no frozen slots dataclass or enum",
        _DATA + "\n\n@dataclass(slots=True, frozen=False)\nclass Half:\n    pass\n",
    ),
    ServiceCase(
        "bare_dataclass",
        "Half",
        "the class iso.services.Half, which is no frozen slots dataclass or enum",
        _DATA + "\n\n@dataclass\nclass Half:\n    pass\n",
    ),
    ServiceCase(
        "unknown_name", "Missing", "Missing, and the check cannot tell what Missing is"
    ),
    ServiceCase(
        "unknown_module",
        "Thing",
        "Thing, and the check cannot tell what Thing is",
        "from nowhere_at_all import Thing\n",
    ),
    ServiceCase(
        "nested_class",
        "Outer.Inner",
        "Outer.Inner, and the check cannot tell what Outer.Inner is",
        "\n\nclass Outer:\n    class Inner:\n        pass\n",
    ),
    ServiceCase("call", "make()", "make(), and the check cannot tell what make() is"),
    ServiceCase(
        "re_exported_model",
        "Record",
        "the model iso.models.Record",
        "from iso.shared import Record\n",
    ),
    ServiceCase(
        "missing_re_export",
        "Gone",
        "Gone, and the check cannot tell what Gone is",
        "from iso.shared import Gone\n",
    ),
    ServiceCase(
        "circular_re_export",
        "Loop",
        "Loop, and the check cannot tell what Loop is",
        "from iso.shared import Loop\n",
    ),
    ServiceCase(
        "annotated_alias",
        "Alias",
        "the model iso.models.Record",
        _MODELS + "\n\nAlias: type = Record\nFirst = Second = Record\n",
    ),
    ServiceCase(
        "alias_base",
        "Child",
        "the model iso.services.Child",
        _DJANGO + "\n\nParent = models.Model\n\n\nclass Child(Parent):\n    pass\n",
    ),
    ServiceCase(
        "generic_base",
        "Rows",
        "the queryset or manager iso.services.Rows",
        _MODELS + _DJANGO + "\n\nclass Rows(QuerySet[Record]):\n    pass\n",
    ),
    ServiceCase(
        "re_exported_any", "Any", "asgiref.sync.Any", "from asgiref.sync import Any\n"
    ),
    ServiceCase(
        "loaded_manager",
        "UserManager",
        "the queryset or manager django.contrib.auth.models.UserManager",
        "from django.contrib.auth.models import UserManager\n",
    ),
    ServiceCase(
        "module",
        "iso.models",
        "iso.models, and the check cannot tell what iso.models is",
        "import iso.models\n",
    ),
    ServiceCase(
        "unreadable_module",
        "Thing",
        "Thing, and the check cannot tell what Thing is",
        "from iso.lib.broken import Thing\n",
    ),
    ServiceCase(
        "unknown_base",
        "Odd",
        "the class iso.services.Odd, which is no frozen slots dataclass or enum",
        "\n\nclass Odd(Missing):\n    pass\n",
    ),
    ServiceCase(
        "project_base",
        "Sub",
        "the queryset or manager iso.services.Sub",
        _DJANGO
        + "\n\nclass Rows(QuerySet):\n    pass\n\n\nclass Sub(Rows):\n    pass\n",
    ),
    ServiceCase(
        "callable",
        "Callable[[Record], int]",
        "the model iso.models.Record",
        _MODELS + "from collections.abc import Callable\n",
    ),
    ServiceCase(
        "third_party_model",
        "User",
        "the model django.contrib.auth.models.User",
        "from django.contrib.auth.models import User\n",
    ),
    ServiceCase(
        "model_base", "models.Model", "the model django.db.models.Model", _DJANGO
    ),
    ServiceCase(
        "bare_list",
        "list",
        "list without its parameters, and the check cannot tell what it holds",
    ),
    ServiceCase(
        "bare_iterable",
        "Iterable",
        "Iterable without its parameters, and the check cannot tell what it holds",
        "from collections.abc import Iterable\n",
    ),
    ServiceCase(
        "bare_typing",
        "typing.List",
        "typing.List without its parameters, and the check cannot tell what it holds",
        _TYPING,
    ),
    ServiceCase(
        "bare_page",
        "Page",
        "Page without its parameters, and the check cannot tell what it holds",
        "from django.core.paginator import Page\n",
    ),
    ServiceCase(
        "dataclass_field",
        "Wrapped",
        (
            "the dataclass iso.services.Wrapped, whose field log holds the model "
            "iso.models.Record"
        ),
        _DATA
        + _MODELS
        + "\n\n@dataclass(frozen=True, slots=True)\nclass Wrapped:\n"
        + "    size: int\n    log: Record\n",
    ),
    ServiceCase(
        "nested_dataclass_field",
        "Outer",
        (
            "the dataclass iso.services.Outer, whose field rows holds the dataclass "
            "iso.services.Wrapped, whose field log holds the model iso.models.Record"
        ),
        _DATA
        + _MODELS
        + "\n\n@dataclass(frozen=True, slots=True)\nclass Wrapped:\n"
        + "    log: Record\n\n\n"
        + "@dataclass(frozen=True, slots=True)\nclass Outer:\n"
        + '    rows: "list[Wrapped]"\n',
    ),
    ServiceCase(
        "import_in_a_function_shadows_nothing",
        "QuerySet",
        "the queryset or manager django.db.models.QuerySet",
        _DJANGO + "\n\ndef _local():\n    from typing import List as QuerySet\n",
    ),
    ServiceCase(
        "type_checking_import",
        "Record",
        "the model iso.models.Record",
        "import typing\n\nif typing.TYPE_CHECKING:\n    from iso.models import Record\n",
    ),
    ServiceCase(
        "try_import",
        "Record",
        "the model iso.models.Record",
        "try:\n    import json\nexcept ImportError:\n    pass\nelse:\n"
        "    from iso.models import Record\nfinally:\n    pass\n",
    ),
)

SERVICE_FINE_CASES: tuple[ServiceCase, ...] = (
    ServiceCase(
        "frozen_slots",
        "Data",
        None,
        _DATA
        + "\n\n@decorate()\n@dataclass(slots=True, frozen=True)\nclass Data:\n"
        + "    pass\n",
    ),
    ServiceCase(
        "dataclasses_module",
        "Data",
        None,
        _DATA
        + "\n\n@dataclasses.dataclass(frozen=True, slots=True)\nclass Data:\n    pass\n",
    ),
    ServiceCase(
        "frozen_slots_tuple",
        "tuple[Data, ...]",
        None,
        _DATA + "\n\n@dataclass(frozen=True, slots=True)\nclass Data:\n    pass\n",
    ),
    ServiceCase(
        "enum", "Color", None, "import enum\n\n\nclass Color(enum.Enum):\n    RED = 1\n"
    ),
    ServiceCase(
        "text_choices",
        "Status",
        None,
        _DJANGO + "\n\nclass Status(models.TextChoices):\n    ON = 'on'\n",
    ),
    ServiceCase(
        "integer_choices",
        "Level",
        None,
        _DJANGO + "\n\nclass Level(models.IntegerChoices):\n    LOW = 1\n",
    ),
    ServiceCase("builtin", "int", None),
    ServiceCase("builtin_generic", "dict[str, int]", None),
    ServiceCase("none", "None", None),
    ServiceCase("literal", 'typing.Literal["a", 1]', None, _TYPING),
    ServiceCase("third_party", "Decimal", None, "from decimal import Decimal\n"),
    ServiceCase(
        "third_party_not_loaded",
        "Thing",
        None,
        "from email.mime.audio import MIMEAudio as Thing\n",
    ),
    ServiceCase("loaded_value", "typing.NamedTuple", None, _TYPING),
    ServiceCase(
        "parametrized_iterable",
        "Iterable[int]",
        None,
        "from collections.abc import Iterable\n",
    ),
    ServiceCase(
        "dataclass_fields",
        "Node",
        None,
        _DATA
        + "\n\n@dataclass(frozen=True, slots=True)\nclass Node:\n"
        + '    """A node."""\n\n    value: int\n    next: "Node | None"\n\n'
        + "    def size(self) -> int:\n        return 1\n",
    ),
)

ISO_MODELS = """from django.db import models


class Base(models.Model):
    class Meta:
        abstract = True


class Record(Base):
    pass
"""

ISO_SHARED = """from iso.models import Record
from iso.shared import Loop
"""

ISO_BROKEN = UNREADABLE


@dataclass(frozen=True, slots=True)
class RefusedDeclaration:
    """Attributes of an `AppConfig` that E175 refuses, and the message it gives."""

    id: str
    attributes: Mapping[str, object]
    message: str


_NOT_NAMES = "shop declares depends_on as something other than a tuple of app names."

REFUSED_DECLARATIONS: tuple[RefusedDeclaration, ...] = (
    RefusedDeclaration("depends-on-list", {"depends_on": ["crm"]}, _NOT_NAMES),
    RefusedDeclaration("depends-on-number", {"depends_on": (1,)}, _NOT_NAMES),
    RefusedDeclaration(
        "depends-on-empty-name",
        {"depends_on": ("crm", "")},
        "shop lists an empty app name in depends_on.",
    ),
    RefusedDeclaration(
        "depends-on-repeated",
        {"depends_on": ("crm", "billing", "crm", "billing", "auth")},
        "shop lists billing, crm in depends_on more than once.",
    ),
    RefusedDeclaration(
        "depends-on-itself",
        {"depends_on": ("shop",)},
        "shop lists itself in depends_on.",
    ),
    RefusedDeclaration(
        "isolated-string",
        {"isolated": "yes"},
        "shop declares isolated as str instead of a bool.",
    ),
    RefusedDeclaration(
        "isolated-int",
        {"isolated": 1},
        "shop declares isolated as int instead of a bool.",
    ),
)


@dataclass(frozen=True, slots=True)
class UnreadableDependsOn:
    """An `apps.py` whose `depends_on` sync cannot rewrite, and the line it names."""

    id: str
    source: str
    line: int


UNREADABLE_DEPENDS_ON: tuple[UnreadableDependsOn, ...] = (
    UnreadableDependsOn("sum", app_config("shop", "depends_on = ('crm',) + ()"), 6),
    UnreadableDependsOn(
        "name", "NAMES = ()\n\n\n" + app_config("shop", "depends_on = NAMES"), 9
    ),
    UnreadableDependsOn(
        "augmented", app_config("shop", "depends_on = ()", "depends_on += ('crm',)"), 7
    ),
    UnreadableDependsOn(
        "two-targets", app_config("shop", "depends_on = other = ()"), 6
    ),
    UnreadableDependsOn(
        "not-strings", app_config("shop", "depends_on = ('crm', 1)"), 6
    ),
)


IMPORT_FORMS: dict[str, dict[str, str]] = {
    "shop": {
        "views.py": (
            "import crm.models\n"
            "import crm.sub.deep\n"
            "import crm.sub.deep.nothing\n"
            "from crm import models\n"
            "from crm import helper\n"
            "from crm.sub import deep\n"
            "from crm.models import Thing, Other\n"
            "from crm import *\n"
            "from . import local\n"
            "from .local import *\n"
            "import os, json\n"
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n    from crm.types import T\n"
            "def lazy():\n    import crm.lazy\n    from crm.sub.deep import y\n"
            "import crm.sub.deep.nothing.deeper\n"
            "from crm.sub.nothing import q\n"
            "from crm.nothing import *\n"
            "from crm.sub.deep import *\n"
            "import crm.pages.home.page\n"
            "from crm.pages.home import page\n"
            "from crm.extra.tool import VALUE\n"
            "import crm.extra.tool.deeper\n"
        ),
        "local.py": "x = 1\nfrom shop import views\n",
        "pkg/__init__.py": "from crm.models import Thing\nfrom . import inner\n",
        "pkg/inner.py": "from ..views import lazy\nfrom .. import local\n",
        "loose/mod.py": "import crm.models\n",
        "tests/test_x.py": "import crm.models\n",
        "odd.name.py": "import crm.models\n",
        ".hidden.py": "import crm.models\n",
        "not-a-name/mod.py": "import crm.models\n",
    },
    "crm": {
        "__init__.py": "from .models import Thing\nhelper = 1\nfrom crm import helper\n",
        "models.py": "Thing = Other = 1\n",
        "types.py": "T = 1\n",
        "lazy.py": "",
        "sub/__init__.py": "",
        "sub/deep.py": "y = 1\nfrom ledger.core import services\n",
        "pages/home/page.py": "",
        "extra/tool.py": "VALUE = 1\n",
    },
    "ledger.core": {"services.py": "import crm.models\nfrom ledger import util\n"},
}
