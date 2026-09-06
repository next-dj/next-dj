from datetime import date
from decimal import Decimal

from library.models import Author, Book, Chapter, Tag


DEMO_TAGS = [
    ("adventure", "Adventure"),
    ("classics", "Classics"),
    ("detective", "Detective"),
    ("gothic", "Gothic"),
    ("romance", "Romance"),
    ("science-fiction", "Science Fiction"),
]

DEMO_AUTHORS = [
    {
        "full_name": "Mary Shelley",
        "email": "mary.shelley@example.com",
        "born_in": 1797,
        "bio": "Wrote the first modern science-fiction novel at nineteen.",
    },
    {
        "full_name": "Jules Verne",
        "email": "jules.verne@example.com",
        "born_in": 1828,
        "bio": "Voyages extraordinaires, from the sea floor to the moon.",
    },
    {
        "full_name": "Charlotte Bronte",
        "email": "charlotte.bronte@example.com",
        "born_in": 1816,
        "bio": "Governess novels with a first-person voice that still carries.",
    },
    {
        "full_name": "Jane Austen",
        "email": "jane.austen@example.com",
        "born_in": 1775,
        "bio": "Six novels of manners, money and marriage.",
    },
    {
        "full_name": "Bram Stoker",
        "email": "bram.stoker@example.com",
        "born_in": 1847,
        "bio": "Theatre manager by day, gothic epistolary novelist by night.",
    },
    {
        "full_name": "Herbert George Wells",
        "email": "hg.wells@example.com",
        "born_in": 1866,
        "bio": "Scientific romances that gave the genre most of its furniture.",
    },
    {
        "full_name": "Anonymous",
        "email": "",
        "born_in": None,
        "bio": "Collections the library holds without a named author.",
    },
]

DEMO_BOOKS = [
    {
        "title": "Frankenstein",
        "author": "Mary Shelley",
        "status": "published",
        "summary": "A student assembles a living creature and abandons it.",
        "published_at": date(1818, 1, 1),
        "price": Decimal("12.50"),
        "is_featured": True,
        "tags": ["classics", "gothic", "science-fiction"],
    },
    {
        "title": "The Last Man",
        "author": "Mary Shelley",
        "status": "archived",
        "summary": "A plague empties the twenty-first century.",
        "published_at": date(1826, 2, 18),
        "price": Decimal("9.95"),
        "is_featured": False,
        "tags": ["classics", "science-fiction"],
    },
    {
        "title": "Mathilda",
        "author": "Mary Shelley",
        "status": "draft",
        "summary": "A novella held back from print for over a century.",
        "published_at": None,
        "price": Decimal("7.25"),
        "is_featured": False,
        "tags": [],
    },
    {
        "title": "Journey to the Center of the Earth",
        "author": "Jules Verne",
        "status": "published",
        "summary": "A runic note sends three men down an Icelandic volcano.",
        "published_at": date(1864, 11, 25),
        "price": Decimal("14.00"),
        "is_featured": False,
        "tags": ["adventure", "science-fiction"],
    },
    {
        "title": "Twenty Thousand Leagues Under the Seas",
        "author": "Jules Verne",
        "status": "published",
        "summary": "Captain Nemo hosts three castaways aboard the Nautilus.",
        "published_at": date(1870, 6, 20),
        "price": Decimal("18.75"),
        "is_featured": True,
        "tags": ["adventure", "science-fiction"],
    },
    {
        "title": "Around the World in Eighty Days",
        "author": "Jules Verne",
        "status": "published",
        "summary": "A wager sends Phileas Fogg east on every timetable he can find.",
        "published_at": date(1873, 1, 30),
        "price": Decimal("11.40"),
        "is_featured": False,
        "tags": ["adventure"],
    },
    {
        "title": "The Mysterious Island",
        "author": "Jules Verne",
        "status": "draft",
        "summary": "Balloon castaways rebuild a civilisation on bare rock.",
        "published_at": None,
        "price": Decimal("0.00"),
        "is_featured": False,
        "tags": [],
    },
    {
        "title": "Jane Eyre",
        "author": "Charlotte Bronte",
        "status": "published",
        "summary": "An orphan takes a post at Thornfield Hall and hears the attic.",
        "published_at": date(1847, 10, 16),
        "price": Decimal("11.75"),
        "is_featured": False,
        "tags": ["classics", "romance"],
    },
    {
        "title": "Villette",
        "author": "Charlotte Bronte",
        "status": "archived",
        "summary": "An English teacher in a Belgian boarding school.",
        "published_at": date(1853, 1, 28),
        "price": Decimal("7.90"),
        "is_featured": False,
        "tags": [],
    },
    {
        "title": "Pride and Prejudice",
        "author": "Jane Austen",
        "status": "published",
        "summary": "Elizabeth Bennet revises her first impression of Mr Darcy.",
        "published_at": date(1813, 1, 28),
        "price": Decimal("10.99"),
        "is_featured": True,
        "tags": ["classics", "romance"],
    },
    {
        "title": "Sense and Sensibility",
        "author": "Jane Austen",
        "status": "published",
        "summary": "Two sisters, two temperaments, one reduced income.",
        "published_at": date(1811, 10, 30),
        "price": Decimal("9.60"),
        "is_featured": False,
        "tags": ["classics", "romance"],
    },
    {
        "title": "Emma",
        "author": "Jane Austen",
        "status": "published",
        "summary": "A matchmaker who is wrong about everyone including herself.",
        "published_at": date(1815, 12, 23),
        "price": Decimal("10.20"),
        "is_featured": False,
        "tags": ["romance"],
    },
    {
        "title": "Persuasion",
        "author": "Jane Austen",
        "status": "archived",
        "summary": "A broken engagement gets a second hearing eight years on.",
        "published_at": date(1817, 12, 20),
        "price": Decimal("8.80"),
        "is_featured": False,
        "tags": [],
    },
    {
        "title": "Dracula",
        "author": "Bram Stoker",
        "status": "published",
        "summary": "Letters, diaries and phonograph cylinders track a count west.",
        "published_at": date(1897, 5, 26),
        "price": Decimal("13.30"),
        "is_featured": True,
        "tags": ["classics", "gothic"],
    },
    {
        "title": "The Jewel of Seven Stars",
        "author": "Bram Stoker",
        "status": "archived",
        "summary": "An Egyptologist's household stages a resurrection.",
        "published_at": date(1903, 9, 1),
        "price": Decimal("8.15"),
        "is_featured": False,
        "tags": ["detective", "gothic"],
    },
    {
        "title": "The Time Machine",
        "author": "Herbert George Wells",
        "status": "published",
        "summary": "A traveller reports back from the year 802,701.",
        "published_at": date(1895, 5, 7),
        "price": Decimal("12.05"),
        "is_featured": True,
        "tags": ["science-fiction"],
    },
    {
        "title": "The War of the Worlds",
        "author": "Herbert George Wells",
        "status": "published",
        "summary": "Cylinders land on Horsell Common and Surrey empties out.",
        "published_at": date(1898, 4, 1),
        "price": Decimal("15.45"),
        "is_featured": False,
        "tags": ["classics", "science-fiction"],
    },
    {
        "title": "The Invisible Man",
        "author": "Herbert George Wells",
        "status": "draft",
        "summary": "A refraction experiment with no way back.",
        "published_at": None,
        "price": Decimal("6.50"),
        "is_featured": False,
        "tags": [],
    },
    {
        "title": "The Arabian Nights",
        "author": "Anonymous",
        "status": "published",
        "summary": "A frame story that keeps deferring its own ending.",
        "published_at": date(1885, 1, 15),
        "price": Decimal("21.00"),
        "is_featured": False,
        "tags": ["adventure", "classics"],
    },
]

DEMO_CHAPTERS = {
    "Frankenstein": [
        (1, "Letters from the Archangel", 3120),
        (2, "Ingolstadt", 4480),
        (3, "The creature speaks", 5210),
        (4, "Pursuit north", 3890),
    ],
    "The Time Machine": [
        (1, "The inventor explains", 2740),
        (2, "Eloi and Morlocks", 4025),
        (3, "The furthest shore", 3310),
    ],
}


def seed_demo() -> None:
    """Fill the catalog so every changelist feature has data to show."""
    tags = {
        slug: Tag.objects.get_or_create(slug=slug, defaults={"name": name})[0]
        for slug, name in DEMO_TAGS
    }
    authors = {
        data["full_name"]: Author.objects.get_or_create(
            full_name=data["full_name"],
            defaults={k: v for k, v in data.items() if k != "full_name"},
        )[0]
        for data in DEMO_AUTHORS
    }
    for data in DEMO_BOOKS:
        book, created = Book.objects.get_or_create(
            title=data["title"],
            defaults={
                "author": authors[data["author"]],
                "status": data["status"],
                "summary": data["summary"],
                "published_at": data["published_at"],
                "price": data["price"],
                "is_featured": data["is_featured"],
            },
        )
        if not created:
            continue
        book.tags.set([tags[slug] for slug in data["tags"]])
        for number, title, word_count in DEMO_CHAPTERS.get(data["title"], []):
            Chapter.objects.create(
                book=book, number=number, title=title, word_count=word_count
            )
