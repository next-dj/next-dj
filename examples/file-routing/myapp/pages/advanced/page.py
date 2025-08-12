from dataclasses import dataclass, field
from django.http import HttpResponse, HttpRequest
from typing import Any, Union, Literal
from pathlib import Path
import json
from datetime import datetime, timezone

# modern dataclass with type hints
@dataclass
class UserProfile:
    """Modern dataclass with type hints and default values."""
    name: str
    age: int
    email: str = ""
    preferences: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with modern f-string formatting."""
        return {
            "name": self.name,
            "age": self.age,
            "email": self.email,
            "preferences": self.preferences,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat()
        }


@dataclass
class ApiResponse:
    """Generic API response structure."""
    status: Literal["success", "error", "warning"]
    message: str
    data: Any = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps({
            "status": self.status,
            "message": self.message,
            "data": self.data,
            "timestamp": self.timestamp.isoformat()
        }, indent=2)


def render(request: HttpRequest, **kwargs: Any) -> HttpResponse:
    """Render advanced page with modern Python features."""
    
    # using walrus operator for multiple assignments
    if (method := request.method) == "GET":
        if (query_data := request.GET.get('data')):
            try:
                parsed_data = json.loads(query_data)
                response = _process_data(parsed_data)
            except json.JSONDecodeError:
                response = ApiResponse("error", "Invalid JSON data")
        else:
            response = ApiResponse("success", "No data provided")
    else:
        response = ApiResponse("warning", f"Method {method} not supported")
    
    # using structural pattern matching
    match response:
        case ApiResponse(status="success", data=data) if data:
            status_emoji = "✅"
            status_class = "success"
        case ApiResponse(status="error"):
            status_emoji = "❌"
            status_class = "error"
        case ApiResponse(status="warning"):
            status_emoji = "⚠️"
            status_class = "warning"
        case _:
            status_emoji = "ℹ️"
            status_class = "info"
    
    # using walrus operator in list comprehension with filtering
    if (headers := request.headers):
        header_info = [
            f"{k}: {v}" for k, v in headers.items() 
            if (v := v.strip()) and k.lower().startswith('http')
        ]
    else:
        header_info = []
    
    # create sample user profiles using dataclasses
    users = [
        UserProfile("Alice", 25, "alice@example.com", {"theme": "dark", "lang": "en"}),
        UserProfile("Bob", 30, "bob@example.com", {"theme": "light", "lang": "es"}),
        UserProfile("Charlie", 35, preferences={"theme": "auto", "lang": "fr"})
    ]
    
    # using modern f-strings with complex expressions
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Advanced Python Features Demo</title>
        <style>
            body {{
                font-family: 'JetBrains Mono', 'Fira Code', monospace;
                margin: 0;
                padding: 20px;
                background: #0d1117;
                color: #c9d1d9;
                line-height: 1.6;
            }}
            .container {{
                max-width: 1200px;
                margin: 0 auto;
            }}
            .header {{
                text-align: center;
                margin-bottom: 40px;
                padding: 20px;
                background: #161b22;
                border-radius: 10px;
                border: 1px solid #30363d;
            }}
            .status {{
                padding: 15px;
                border-radius: 8px;
                margin: 20px 0;
                border-left: 4px solid;
            }}
            .status.success {{
                background: #0c532a;
                border-left-color: #238636;
            }}
            .status.error {{
                background: #5a1a1a;
                border-left-color: #da3633;
            }}
            .status.warning {{
                background: #5a3a00;
                border-left-color: #d29922;
            }}
            .status.info {{
                background: #1a4a4a;
                border-left-color: #39d353;
            }}
            .grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
                gap: 20px;
                margin: 20px 0;
            }}
            .card {{
                background: #161b22;
                border: 1px solid #30363d;
                border-radius: 8px;
                padding: 20px;
            }}
            .card h3 {{
                margin-top: 0;
                color: #58a6ff;
                border-bottom: 1px solid #30363d;
                padding-bottom: 10px;
            }}
            .code-block {{
                background: #0d1117;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 15px;
                overflow-x: auto;
                font-size: 0.9em;
                margin: 10px 0;
            }}
            .highlight {{
                color: #ff7b72;
                font-weight: bold;
            }}
            .user-list {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 15px;
                margin: 20px 0;
            }}
            .user-card {{
                background: #21262d;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 15px;
            }}
            .user-name {{
                color: #58a6ff;
                font-weight: bold;
                font-size: 1.1em;
            }}
            .user-detail {{
                color: #8b949e;
                margin: 5px 0;
            }}
            .emoji {{
                font-size: 1.2em;
                margin-right: 10px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🚀 Advanced Python Features</h1>
                <p>Demonstrating modern Python 3.10+ capabilities</p>
            </div>
            
            <div class="status {status_class}">
                <span class="emoji">{status_emoji}</span>
                <strong>Status:</strong> {response.status.upper()}
                <br>
                <strong>Message:</strong> {response.message}
                {f'<br><strong>Data:</strong> <pre>{json.dumps(response.data, indent=2)}</pre>' if response.data else ''}
            </div>
            
            <div class="grid">
                <div class="card">
                    <h3>🦭 Walrus Operator Examples</h3>
                    <div class="code-block">
# Multiple assignments in one line
if (data := request.GET.get('data')) and (parsed := json.loads(data)):
    process(parsed)

# In list comprehensions
filtered = [x for x in items if (clean := x.strip())]
                    </div>
                </div>
                
                <div class="card">
                    <h3>🎯 Structural Pattern Matching</h3>
                    <div class="code-block">
match response:
    case ApiResponse(status="success", data=data) if data:
        # Handle success with data
    case ApiResponse(status="error"):
        # Handle error
    case _:
        # Default case
                    </div>
                </div>
                
                <div class="card">
                    <h3>📦 Modern Dataclasses</h3>
                    <div class="code-block">
@dataclass
class UserProfile:
    name: str
    age: int
    email: str = ""
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
                    </div>
                </div>
                
                <div class="card">
                    <h3>🔤 Type Hints & Literals</h3>
                    <div class="code-block">
def process_data(
    data: dict[str, Any]
) -> Union[ApiResponse, None]:
    status: Literal["success", "error"] = "success"
    return ApiResponse(status, "Processed")
                    </div>
                </div>
            </div>
            
            <div class="card">
                <h3>👥 Sample User Profiles (Dataclass Demo)</h3>
                <div class="user-list">
                    {_render_user_cards(users)}
                </div>
            </div>
            
            <div class="card">
                <h3>📊 Request Information</h3>
                <p><strong>Method:</strong> <span class="highlight">{method}</span></p>
                <p><strong>Headers Found:</strong> <span class="highlight">{len(header_info)}</span></p>
                {f'<p><strong>HTTP Headers:</strong></p><div class="code-block">{chr(10).join(header_info)}</div>' if header_info else ''}
            </div>
            
            <div class="card">
                <h3>⚡ Performance Features</h3>
                <ul>
                    <li><strong>Pathlib:</strong> Modern path manipulation with <code>Path("data") / "config"</code></li>
                    <li><strong>Type Hints:</strong> Better IDE support and static analysis</li>
                    <li><strong>Dataclasses:</strong> Automatic <code>__init__</code>, <code>__repr__</code>, etc.</li>
                    <li><strong>Structural Matching:</strong> Cleaner control flow than if/elif chains</li>
                    <li><strong>Walrus Operator:</strong> More concise assignments in expressions</li>
                </ul>
            </div>
        </div>
    </body>
    </html>
    """
    
    return HttpResponse(html)


def _process_data(data: Any) -> ApiResponse:
    """Process data using modern Python features."""
    
    # using structural pattern matching for data processing
    match data:
        case {"type": "user", "name": str(name), "age": int(age)} if age > 0:
            user = UserProfile(name=name, age=age)
            return ApiResponse("success", "User created successfully", user.to_dict())
        
        case {"type": "config", "settings": dict(settings)} if settings:
            # using walrus operator for validation
            if (theme := settings.get("theme")) and theme in ["light", "dark", "auto"]:
                return ApiResponse("success", "Configuration updated", {"theme": theme})
            else:
                return ApiResponse("error", "Invalid theme setting")
        
        case {"type": "list", "items": list(items)} if len(items) > 0:
            # using walrus operator in list comprehension
            processed = [item for item in items if (clean := str(item).strip())]
            return ApiResponse("success", f"Processed {len(processed)} items", processed)
        
        case _:
            return ApiResponse("error", "Unknown data type or invalid structure")


def _render_user_cards(users: list[UserProfile]) -> str:
    """Render user cards using modern string formatting."""
    cards = []
    for user in users:
        card = f"""
        <div class="user-card">
            <div class="user-name">{user.name}</div>
            <div class="user-detail">Age: {user.age}</div>
            <div class="user-detail">Email: {user.email or 'Not provided'}</div>
            <div class="user-detail">Active: {'Yes' if user.is_active else 'No'}</div>
            <div class="user-detail">Created: {user.created_at.strftime('%Y-%m-%d %H:%M')}</div>
            <div class="user-detail">Preferences: {len(user.preferences)} items</div>
        </div>
        """
        cards.append(card)
    
    return "".join(cards)
