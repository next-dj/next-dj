from next.components import component


BASE = "relative w-full rounded-lg border p-4 text-sm"

VARIANTS: dict[str, str] = {
    "default": "bg-background text-foreground border-border",
    "info": "bg-info/10 text-info border-info/40",
    "success": "bg-success/10 text-success border-success/40",
    "warning": "bg-warning/10 text-warning border-warning/40",
    "destructive": "bg-destructive/10 text-destructive border-destructive/40",
}


@component.context("classes")
def classes(variant: str = "default", extra: str = "") -> str:
    return f"{BASE} {VARIANTS.get(variant, VARIANTS['default'])} {extra}".strip()
