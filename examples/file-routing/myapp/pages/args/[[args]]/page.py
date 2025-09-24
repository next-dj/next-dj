from django.http import HttpRequest, HttpResponse


html = """
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{
                font-family: Arial, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
                background-color: #f0f0f0;
            }}
            .container {{
                text-align: center;
                background: white;
                padding: 2rem;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }}
            h1 {{ color: #333; }}
            p {{ color: #666; }}
            .highlight {{
                background-color: #e8f5e8;
                padding: 0.5rem;
                border-radius: 4px;
                margin: 1rem 0;
            }}
            .args-list {{
                background-color: #fff3cd;
                padding: 0.5rem;
                border-radius: 4px;
                margin: 1rem 0;
                font-family: monospace;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Variable Arguments Page</h1>
            <p>This page demonstrates variable number of arguments.</p>
            <div class="highlight">
                <strong>Arguments Count:</strong> {args_count}
            </div>
            <div class="args-list">
                <strong>Arguments:</strong> {args_str}
            </div>
        </div>
    </body>
    </html>
"""


def render(_request: HttpRequest, *args, **_kwargs) -> HttpResponse:
    """Render page with variable arguments."""
    args_str = ", ".join(args) if args else "(none)"
    args_count = len(args) if args else 0

    return HttpResponse(html.format(args_str=args_str, args_count=args_count))
