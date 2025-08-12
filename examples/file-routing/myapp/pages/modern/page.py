from django.http import HttpResponse, HttpRequest
from typing import Any

# modern python features demonstration
def render(request: HttpRequest, **kwargs: Any) -> HttpResponse:
    """Render modern page with contemporary Python features."""
    
    # using walrus operator for cleaner code
    if (user_agent := request.META.get('HTTP_USER_AGENT', '')):
        browser_type = _detect_browser(user_agent)
    else:
        browser_type = "Unknown"
    
    # using match/case for modern pattern matching
    status_message = _get_status_message(request.method)
    
    # using walrus operator in list comprehension
    query_params = {k: v for k, v in request.GET.items() if (v := v.strip())}
    
    # using modern f-string features
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Modern Python Features Demo</title>
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                margin: 0;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                min-height: 100vh;
            }}
            .container {{
                max-width: 800px;
                margin: 0 auto;
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                border-radius: 20px;
                padding: 30px;
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            }}
            h1 {{
                text-align: center;
                margin-bottom: 30px;
                font-size: 2.5em;
                text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.3);
            }}
            .feature-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 20px;
                margin-top: 30px;
            }}
            .feature-card {{
                background: rgba(255, 255, 255, 0.15);
                padding: 20px;
                border-radius: 15px;
                border: 1px solid rgba(255, 255, 255, 0.2);
            }}
            .feature-title {{
                font-size: 1.3em;
                font-weight: bold;
                margin-bottom: 10px;
                color: #ffd700;
            }}
            .code-block {{
                background: rgba(0, 0, 0, 0.3);
                padding: 15px;
                border-radius: 10px;
                font-family: 'Courier New', monospace;
                font-size: 0.9em;
                margin: 10px 0;
                overflow-x: auto;
            }}
            .info-item {{
                margin: 10px 0;
                padding: 10px;
                background: rgba(255, 255, 255, 0.1);
                border-radius: 8px;
            }}
            .highlight {{
                color: #ffd700;
                font-weight: bold;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🐍 Modern Python Features</h1>
            
            <div class="info-item">
                <strong>Browser:</strong> <span class="highlight">{browser_type}</span>
            </div>
            <div class="info-item">
                <strong>HTTP Method:</strong> <span class="highlight">{status_message}</span>
            </div>
            <div class="info-item">
                <strong>Query Parameters:</strong> <span class="highlight">{len(query_params)}</span> found
            </div>
            
            <div class="feature-grid">
                <div class="feature-card">
                    <div class="feature-title">🦭 Walrus Operator (:=)</div>
                    <p>Assigns values to variables as part of a larger expression</p>
                    <div class="code-block">
if (data := get_data()) and data.is_valid():
    process(data)
                    </div>
                </div>
                
                <div class="feature-card">
                    <div class="feature-title">🎯 Match/Case Statements</div>
                    <p>Pattern matching for cleaner control flow</p>
                    <div class="code-block">
match status:
    case "success":
        return "Great!"
    case "error" as e:
        return f"Oops: {{e}}"
    case _:
        return "Unknown"
                    </div>
                </div>
                
                <div class="feature-card">
                    <div class="feature-title">📝 Type Hints</div>
                    <p>Static type checking and better IDE support</p>
                    <div class="code-block">
def process_data(data: list[int]) -> dict[str, Any]:
    return {{"count": len(data)}}
                    </div>
                </div>
                
                <div class="feature-card">
                    <div class="feature-title">🔧 Pathlib</div>
                    <p>Modern path manipulation</p>
                    <div class="code-block">
from pathlib import Path
file_path = Path("data") / "config" / "settings.yaml"
                    </div>
                </div>
                
                <div class="feature-card">
                    <div class="feature-title">📦 Dataclasses</div>
                    <p>Automatic boilerplate code generation</p>
                    <div class="code-block">
@dataclass
class User:
    name: str
    age: int
    email: str = ""
                    </div>
                </div>
                
                <div class="feature-card">
                    <div class="feature-title">⚡ Async/Await</div>
                    <p>Asynchronous programming support</p>
                    <div class="code-block">
async def fetch_data():
    async with aiohttp.ClientSession() as session:
        return await session.get(url)
                    </div>
                </div>
            </div>
            
            <div class="info-item" style="margin-top: 30px; text-align: center;">
                <p>This page demonstrates modern Python 3.10+ features including:</p>
                <p>• Walrus operator (:=) • Match/case statements • Type hints • Pathlib • Dataclasses • Async/await</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return HttpResponse(html)


def _detect_browser(user_agent: str) -> str:
    """Detect browser type using modern Python features."""
    user_agent_lower = user_agent.lower()
    
    # using match/case for browser detection
    match user_agent_lower:
        case ua if "chrome" in ua:
            return "Chrome"
        case ua if "firefox" in ua:
            return "Firefox"
        case ua if "safari" in ua and "chrome" not in ua:
            return "Safari"
        case ua if "edge" in ua:
            return "Edge"
        case ua if "opera" in ua:
            return "Opera"
        case _:
            return "Other"


def _get_status_message(method: str) -> str:
    """Get status message using match/case."""
    match method.upper():
        case "GET":
            return "📖 Reading data"
        case "POST":
            return "✏️ Creating data"
        case "PUT":
            return "🔄 Updating data"
        case "DELETE":
            return "🗑️ Removing data"
        case "PATCH":
            return "🔧 Partial update"
        case _:
            return f"❓ Unknown method: {method}"
