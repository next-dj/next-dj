from next.components import component


BASE = (
    "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium"
    " transition-colors"
)

VARIANTS: dict[str, str] = {
    "default": "bg-primary text-primary-foreground",
    "secondary": "bg-secondary text-secondary-foreground",
    "outline": "border border-border text-foreground",
    "destructive": "bg-destructive text-destructive-foreground",
    "success": "bg-success text-success-foreground",
    "warning": "bg-warning text-warning-foreground",
    "info": "bg-info text-info-foreground",
    "muted": "bg-muted text-muted-foreground",
}


@component.context("classes")
def classes(variant: str = "default", extra: str = "") -> str:
    return f"{BASE} {VARIANTS.get(variant, VARIANTS['default'])} {extra}".strip()
