from django.http import HttpResponse

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
            .type-info {{
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
            <h1>Typed Parameter Page</h1>
            <p>This page demonstrates typed parameters.</p>
            <div class="highlight">
                <strong>Parameter:</strong> post_id = {post_id}
            </div>
            <div class="type-info">
                <strong>Type:</strong> int
                <br>
                <strong>URL Pattern:</strong> /kwargs/[int:post-id]/
            </div>
            <p>URL: /kwargs/{post_id}</p>
            <p>Try different integer values like /kwargs/123, /kwargs/456, etc.</p>
        </div>
    </body>
    </html>
"""


def render(request, post_id=None, **kwargs):
    """Render page with typed parameter."""
    return HttpResponse(html.format(post_id=post_id))
