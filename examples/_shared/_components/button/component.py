from next.components import component


BASE = (
    "inline-flex items-center justify-center gap-2 whitespace-nowrap"
    " rounded-md text-sm font-medium transition-colors"
    " disabled:pointer-events-none disabled:opacity-50"
)

VARIANTS: dict[str, str] = {
    "default": "bg-primary text-primary-foreground hover:bg-primary/90",
    "secondary": "bg-secondary text-secondary-foreground hover:bg-secondary/80",
    "outline": (
        "border border-border bg-background text-foreground"
        " hover:bg-accent hover:text-accent-foreground"
    ),
    "ghost": "text-foreground hover:bg-accent hover:text-accent-foreground",
    "destructive": (
        "bg-destructive text-destructive-foreground hover:bg-destructive/90"
    ),
    "link": "text-primary underline-offset-4 hover:underline",
}

SIZES: dict[str, str] = {
    "sm": "h-9 px-3",
    "md": "h-10 px-4 py-2",
    "lg": "h-11 px-8",
    "icon": "h-10 w-10",
}


@component.context("classes")
def classes(variant: str = "default", size: str = "md", extra: str = "") -> str:
    parts = [
        BASE,
        VARIANTS.get(variant, VARIANTS["default"]),
        SIZES.get(size, SIZES["md"]),
    ]
    if extra:
        parts.append(extra)
    return " ".join(parts)
