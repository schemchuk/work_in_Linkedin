"""
LinkedIn OAuth 2.0 authorization helper.

This script starts a temporary local web server to receive the OAuth callback,
exchanges the authorization code for an access token, fetches the LinkedIn
person URN, and saves both values to .env.

Usage:
    1. Create a LinkedIn app at https://www.linkedin.com/developers/apps
    2. Copy .env.example to .env and fill in LINKEDIN_CLIENT_ID and
       LINKEDIN_CLIENT_SECRET.
    3. Run: python linkedin_auth.py  (add --force to replace an existing token)
    4. Open the printed URL in your browser and authorize the app.
    5. Token and person URN are saved to .env automatically.
"""

import argparse
import os
import re
import secrets
import urllib.parse
import http.server
import socketserver
from pathlib import Path

import requests
from dotenv import load_dotenv

from linkedin_client import get_person_urn

# Load existing .env (if any) so values are preserved
load_dotenv()

CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8080/callback")
SCOPES = os.getenv("LINKEDIN_SCOPES", "openid profile email w_member_social").split()

ENV_PATH = Path(".env")
ENV_EXAMPLE_PATH = Path(".env.example")

# CSRF protection: random state generated per run, verified in the callback
EXPECTED_STATE = secrets.token_urlsafe(32)


def ensure_env_file() -> None:
    """Create .env from .env.example if it does not exist."""
    if ENV_PATH.exists():
        return
    if ENV_EXAMPLE_PATH.exists():
        ENV_PATH.write_text(ENV_EXAMPLE_PATH.read_text(), encoding="utf-8")
        print(f"✅ Created {ENV_PATH} from {ENV_EXAMPLE_PATH}")
    else:
        ENV_PATH.write_text("", encoding="utf-8")


def read_env() -> str:
    ensure_env_file()
    return ENV_PATH.read_text(encoding="utf-8")


def write_env(content: str) -> None:
    ENV_PATH.write_text(content, encoding="utf-8")


def set_env_var(name: str, value: str) -> None:
    """Set or update a variable in .env."""
    content = read_env()
    lines = content.splitlines(keepends=True)
    pattern = re.compile(rf"^{re.escape(name)}\s*=.*$")

    updated = False
    for i, line in enumerate(lines):
        if pattern.match(line.rstrip("\n")):
            lines[i] = f"{name}={value}\n"
            updated = True
            break

    if not updated:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{name}={value}\n")

    write_env("".join(lines))
    print(f"✅ Saved {name} to {ENV_PATH}")


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
    response = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data=data,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Handle OAuth callback from LinkedIn."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path != "/callback":
            self._send_response(404, "Not found")
            return

        if "error" in query:
            self._send_response(400, f"OAuth error: {query['error'][0]}")
            return

        if query.get("state", [None])[0] != EXPECTED_STATE:
            self._send_response(400, "Invalid state parameter (possible CSRF)")
            return

        if "code" not in query:
            self._send_response(400, "Missing authorization code")
            return

        code = query["code"][0]

        try:
            token_data = exchange_code_for_token(code)
            access_token = token_data["access_token"]
            person_urn = get_person_urn(access_token)

            set_env_var("LINKEDIN_ACCESS_TOKEN", access_token)
            set_env_var("LINKEDIN_PERSON_URN", person_urn)

            self._send_response(
                200,
                "Authorization successful! Token and person URN saved to .env. "
                "You can close this tab.",
            )
        except requests.HTTPError as e:
            self._send_response(400, f"Failed to exchange code: {e.response.text}")
        except Exception as e:
            self._send_response(400, f"Error: {e}")

    def _send_response(self, status: int, message: str):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        body = f"""
        <html>
            <head><title>LinkedIn Auth</title></head>
            <body style="font-family: sans-serif; padding: 2rem;">
                <h1>{message}</h1>
            </body>
        </html>
        """
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="LinkedIn OAuth 2.0 authorization helper")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-authorize even if a token already exists in .env",
    )
    args = parser.parse_args()

    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌ LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET must be set in .env")
        return

    access_token = os.getenv("LINKEDIN_ACCESS_TOKEN")
    if access_token and not args.force:
        print(f"⚠️  LINKEDIN_ACCESS_TOKEN already exists in {ENV_PATH}.")
        print("   Run with --force to re-authorize.")
        return

    auth_url = build_auth_url(EXPECTED_STATE)

    print("\n🔗 Open this URL in your browser and authorize the app:")
    print(auth_url)
    print("\n🚀 Waiting for callback on http://localhost:8080/callback ...")

    with socketserver.TCPServer(("127.0.0.1", 8080), OAuthCallbackHandler) as httpd:
        httpd.handle_request()


if __name__ == "__main__":
    main()
