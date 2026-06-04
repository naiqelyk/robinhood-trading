from __future__ import annotations
import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import subprocess
import threading
import urllib.parse
import urllib.request
import webbrowser
from typing import Optional

CLIENT_ID = "LtLiNmbs9owbYfWgBlC68Z2V-claude"
AUTH_URL = "https://robinhood.com/oauth"
TOKEN_URL = "https://api.robinhood.com/oauth2/token/"
RESOURCE = "https://agent.robinhood.com/mcp/trading"
SCOPE = "internal"
KEYCHAIN_SERVICE = "cozy-hare-robinhood-mcp"
KEYCHAIN_ACCOUNT = "access_token"


def _keychain_read() -> Optional[str]:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _keychain_write(token: str) -> None:
    subprocess.run(
        ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
        capture_output=True,
    )
    subprocess.run(
        ["security", "add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w", token],
        check=True,
    )


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _exchange_code(code: str, verifier: str, redirect_uri: str) -> str:
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "client_id": CLIENT_ID,
        "resource": RESOURCE,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {data}")
    return token


def _run_oauth() -> str:
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    port = _free_port()
    redirect_uri = f"http://localhost:{port}/callback"

    auth_params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": SCOPE,
        "resource": RESOURCE,
    }
    full_auth_url = AUTH_URL + "?" + urllib.parse.urlencode(auth_params)

    code_holder: list[Optional[str]] = [None]
    done = threading.Event()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = qs.get("code", [None])[0]
            got_state = qs.get("state", [None])[0]
            if code and got_state == state:
                code_holder[0] = code
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h1>Authorized! You can close this tab.</h1>")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"<h1>Authorization failed.</h1>")
            done.set()

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer(("localhost", port), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    print("\nOpening Robinhood authorization in your browser…")
    print(f"If the browser doesn't open, visit:\n\n  {full_auth_url}\n")
    webbrowser.open(full_auth_url)

    if not done.wait(timeout=300):
        srv.shutdown()
        raise RuntimeError("Timed out waiting for Robinhood authorization (5 min). Re-run to try again.")

    srv.shutdown()

    if not code_holder[0]:
        raise RuntimeError("Authorization failed or was cancelled.")

    token = _exchange_code(code_holder[0], verifier, redirect_uri)
    _keychain_write(token)
    print("Robinhood MCP token saved to Keychain.\n")
    return token


def get_robinhood_mcp_token() -> str:
    """Return a Robinhood MCP Bearer token.

    Resolution order:
    1. ROBINHOOD_MCP_TOKEN env var
    2. macOS Keychain (cached from a previous OAuth flow)
    3. Interactive browser OAuth (PKCE) — opens on first use, then caches
    """
    token = os.environ.get("ROBINHOOD_MCP_TOKEN")
    if token:
        return token

    token = _keychain_read()
    if token:
        return token

    return _run_oauth()


def clear_cached_token() -> None:
    """Remove the cached token from Keychain (call this if the token has expired)."""
    subprocess.run(
        ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
        capture_output=True,
    )
