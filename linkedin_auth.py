"""
LinkedIn OAuth 2.0 authorization helper.

This script starts a temporary local web server to receive the OAuth callback,
exchanges the authorization code for an access token, fetches the LinkedIn
person URN, and saves both values to .env.

Usage:
    1. Create a LinkedIn app at https://www.linkedin.com/developers/apps
    2. Copy .env.example to .env and fill in LINKEDIN_CLIENT_ID and
       LINKEDIN_CLIENT_SECRET.
    3. Run: python linkedin_auth.py  (add --force to replace an existing token,
       --open to launch the browser automatically)
    4. Open the printed URL in your browser and authorize the app.
    5. Token and person URN are saved to .env automatically.
"""

import argparse
import html
import os
import re
import secrets
import subprocess
import time
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
    # Keep the running process in sync: the bot is long-lived and reads
    # tokens via os.getenv, so without this it would keep the old token.
    os.environ[name] = value
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


def save_token_response(token_data: dict) -> str:
    """Persist a fresh token (+ person URN, expiry). Returns expiry YYYY-MM-DD."""
    from datetime import datetime, timedelta

    access_token = token_data["access_token"]
    person_urn = get_person_urn(access_token)

    set_env_var("LINKEDIN_ACCESS_TOKEN", access_token)
    set_env_var("LINKEDIN_PERSON_URN", person_urn)

    # Track expiry so the bot can warn before the token dies
    expires_in = token_data.get("expires_in", 60 * 86400)
    expires_at = (datetime.now() + timedelta(seconds=expires_in)).strftime("%Y-%m-%d")
    set_env_var("LINKEDIN_TOKEN_EXPIRES_AT", expires_at)

    # Ordinary apps never get one, but store it if LinkedIn sends it —
    # token_manager.try_refresh() will then renew tokens silently
    if token_data.get("refresh_token"):
        set_env_var("LINKEDIN_REFRESH_TOKEN", token_data["refresh_token"])
    return expires_at


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Handle OAuth callback from LinkedIn.

    The outcome goes to self.server.result: {"ok": bool, "message": str,
    "expires_at": str | None}.
    """

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path != "/callback":
            # Stray requests (port probes, favicon, etc.) don't count as the
            # real OAuth callback — respond and keep waiting for it.
            self._send_response(404, "Not found")
            return

        if "error" in query:
            self._finish(False, f"OAuth error: {query['error'][0]}")
            return

        if query.get("state", [None])[0] != self.server.expected_state:
            # Don't end the wait: a forged/stale request must not cancel the
            # real callback that may still arrive.
            self._send_response(400, "Invalid state parameter (possible CSRF)")
            return

        if "code" not in query:
            self._finish(False, "Missing authorization code")
            return

        try:
            expires_at = save_token_response(exchange_code_for_token(query["code"][0]))
            self._finish(
                True,
                "Authorization successful! Token and person URN saved to .env. "
                "You can close this tab.",
                expires_at,
            )
        except requests.HTTPError as e:
            self._finish(False, f"Failed to exchange code: {e.response.text}")
        except Exception as e:
            self._finish(False, f"Error: {e}")

    def _finish(self, ok: bool, message: str, expires_at: str | None = None):
        self.server.result = {"ok": ok, "message": message, "expires_at": expires_at}
        self._send_response(200 if ok else 400, message)

    def _send_response(self, status: int, message: str):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        body = f"""
        <html>
            <head><title>LinkedIn Auth</title></head>
            <body style="font-family: sans-serif; padding: 2rem;">
                <h1>{html.escape(message)}</h1>
            </body>
        </html>
        """
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        pass


def _callback_port() -> int:
    return urllib.parse.urlparse(REDIRECT_URI).port or 8080


def start_oauth_server() -> tuple[socketserver.TCPServer, str]:
    """Bind the callback server and return it with the authorization URL.

    Raises OSError if the port is busy (e.g. another auth flow is running).
    """
    httpd = socketserver.TCPServer(("127.0.0.1", _callback_port()), OAuthCallbackHandler)
    httpd.expected_state = secrets.token_urlsafe(32)  # CSRF protection, per run
    httpd.result = None
    httpd.timeout = 10  # poll interval, so the overall deadline is enforced
    return httpd, build_auth_url(httpd.expected_state)


def wait_for_callback(httpd: socketserver.TCPServer, timeout: int = 600) -> dict:
    """Serve until the OAuth callback arrives or `timeout` seconds pass."""
    deadline = time.monotonic() + timeout
    try:
        while httpd.result is None:
            if time.monotonic() > deadline:
                return {"ok": False, "message": "Timed out waiting for the LinkedIn callback",
                        "expires_at": None}
            httpd.handle_request()
        return httpd.result
    finally:
        httpd.server_close()


def open_in_browser(url: str) -> bool:
    """Open url in the desktop browser. With a live LinkedIn session and an
    app you already approved, LinkedIn skips the consent screen and the whole
    flow finishes without a click."""
    try:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description="LinkedIn OAuth 2.0 authorization helper")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-authorize even if a token already exists in .env",
    )
    parser.add_argument(
        "--open", action="store_true",
        help="Open the authorization URL in the default browser automatically",
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

    httpd, auth_url = start_oauth_server()

    print("\n🔗 Open this URL in your browser and authorize the app:")
    print(auth_url)
    if args.open:
        open_in_browser(auth_url)
    print(f"\n🚀 Waiting for callback on {REDIRECT_URI} ...")

    result = wait_for_callback(httpd)
    print(("✅ " if result["ok"] else "❌ ") + result["message"])
    if result["expires_at"]:
        print(f"   Token valid until {result['expires_at']}")


if __name__ == "__main__":
    main()
