from django.http import HttpRequest, HttpResponse


html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Simple Page</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
                background-color: #f0f0f0;
            }
            .container {
                text-align: center;
                background: white;
                padding: 2rem;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 { color: #333; }
            p { color: #666; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Root Page</h1>
            <p>This is a root page without any parameters.</p>
            <p>URL: /home</p>
        </div>
    </body>
    </html>
"""


def render(_request: HttpRequest) -> HttpResponse:
    """Render root page."""
    return HttpResponse(html)
