def render(request):
    """Render home page from root pages directory."""
    return {
        "message": "Hello from root pages directory!",
        "source": "root_pages/home/page.py",
        "type": "root_level_page"
    }
