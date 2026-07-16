"""
LinkedIn OAuth 2.0 authorization helper.

This script starts a temporary local web server to receive the OAuth callback,
exchanges the authorization code for an access token, and saves it to .linkedin_token.json.

Usage:
    1. Fill in .env file with your LinkedIn app credentials.
    2. Run: python linkedin_auth.py
    3. Open the printed URL in your browser and authorize the app.
    4. The token will be saved automatically.
"""

import os
import json
import urllib.parse
import http.server
import socketserver
import requests
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8080/callback")
SCOPES = os.getenv("LINKEDIN_SCOPES", "openid profile email").split()

TOKEN_FILE = ".linkedin_token.json"


def build_auth_url(state: str) -> str:
    """Build LinkedIn OAuth authorization URL."""
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "scope": " ".join(SCOPES),
    }
    return "https://www.linkedin.com/oauth/v2/authorization?" + urllib.parse.urlencode(params)


def exchange_code_for_token(code: str) -> dict:
    """Exchange authorization code for access token."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    response = requests.post("https://www.linkedin.com/oauth/v2/accessToken", data=data)
    response.raise_for_status()
    return response.json()


def save_token(token: dict) -> None:
    """Save token to a local JSON file."""
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token, f, indent=2)
    print(f"\n✅ Token saved to {TOKEN_FILE}")


def load_token() -> dict | None:
    """Load previously saved token if it exists."""
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Handle OAuth callback from LinkedIn."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        if "error" in query:
            self._send_response(f"❌ OAuth error: {query['error'][0]}")
            return

        if "code" not in query:
            self._send_response("❌ Missing authorization code.")
            return

        code = query["code"][0]

        try:
            token = exchange_code_for_token(code)
            save_token(token)
            self._send_response("Authorization successful! You can close this tab.")
        except requests.HTTPError as e:
            self._send_response(f"❌ Failed to exchange code: {e.response.text}")

    def _send_response(self, message: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"""
        <html>
            <head><title>LinkedIn Auth</title></head>
            <body style="font-family: sans-serif; padding: 2rem;">
                <h1>{message}</h1>
            </body>
        </html>
        """.encode("utf-8"))

    def log_message(self, format, *args):
        # Suppress default server logs
        pass


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌ LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET must be set in .env")
        return

    existing_token = load_token()
    if existing_token:
        print(f"⚠️  Token already exists in {TOKEN_FILE}. Delete it if you want to re-authorize.")
        return

    state = "linkedin_oauth_state"
    auth_url = build_auth_url(state)

    print("\n🔗 Open this URL in your browser and authorize the app:")
    print(auth_url)
    print("\n🚀 Waiting for callback on http://localhost:8080/callback ...")

    with socketserver.TCPServer(("", 8080), OAuthCallbackHandler) as httpd:
        httpd.handle_request()


if __name__ == "__main__":
    main()
