"""One-time setup for sending sign-in codes with the Gmail API (``PIP_EMAIL_PROVIDER=gmail``).

Run from server/ on your Mac, after making a Google Cloud OAuth client of type "Desktop app"::

    uv run python tools/gmail_token.py                     # asks for the client id and secret
    uv run python tools/gmail_token.py --test-to you@gmail.com   # ...then sends one test email

It opens Google's consent page in the browser. Sign in as the Gmail account the codes should come
from and allow "Send email on your behalf" (only that). It saves ``GMAIL_CLIENT_ID``,
``GMAIL_CLIENT_SECRET`` and ``GMAIL_REFRESH_TOKEN`` to the env file (mode 600) and never prints them.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from little_explorer_agent.config import USER_ENV, env_file, load_env  # noqa: E402
from little_explorer_agent.mail import GOOGLE_TOKEN_URL, Mailer, compose  # noqa: E402

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPE = "https://www.googleapis.com/auth/gmail.send"
KEYS = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")


def save_env(path: Path, values: dict[str, str]) -> None:
    """Set ``values`` in the env file, replacing existing lines and keeping everything else."""
    lines = path.read_text().splitlines() if path.is_file() else []
    left = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in left:
            out.append(f"{key}={left.pop(key)}")
        else:
            out.append(line)
    out += [f"{key}={value}" for key, value in left.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(out) + "\n")
    path.chmod(0o600)


def consent(client_id: str, client_secret: str, login_hint: str = "") -> str:
    """Runs Google's loopback sign-in in the browser and returns a refresh token."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    got: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if "code" not in query and "error" not in query:
                self.send_response(404)
                self.end_headers()
                return
            got.update(query)
            ok = "code" in query and query.get("state") == state
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"Done. You can close this tab." if ok else b"Something went wrong; see the terminal."
            )
            done.set()

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("Opening Google's consent page. If it doesn't open, paste this into the browser:\n" + url + "\n")
    webbrowser.open(url)
    if not done.wait(timeout=600):
        raise SystemExit("No answer from the browser in 10 minutes.")
    server.shutdown()
    if got.get("state") != state:
        raise SystemExit("The browser's answer didn't match this sign-in; run it again.")
    if "error" in got:
        raise SystemExit(f"Google said: {got['error']}")
    form = urllib.parse.urlencode(
        {
            "code": got["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }
    ).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(GOOGLE_TOKEN_URL, data=form, method="POST"), timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Google refused the code: {exc.code} {exc.read().decode(errors='replace')[:300]}") from None
    if SCOPE not in str(data.get("scope", "")).split():
        raise SystemExit("Google didn't grant 'send email'. Run it again and tick that box.")
    token = str(data.get("refresh_token") or "")
    if not token:
        raise SystemExit("Google gave no refresh token. Remove the app's access in your Google account and run it again.")
    return token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", help="env file to write (default: the one the server reads, or ~/.config/little-explorer/.env)")
    parser.add_argument("--login-hint", help="the Gmail address to sign in as (pre-fills Google's page)")
    parser.add_argument("--test-to", help="after saving, send one test code email to this address")
    args = parser.parse_args()

    load_env()
    path = Path(args.env).expanduser() if args.env else (env_file() or USER_ENV.expanduser())
    client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip() or input("OAuth client ID: ").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "").strip() or getpass.getpass("OAuth client secret (hidden): ").strip()
    if not client_id.endswith(".apps.googleusercontent.com") or not client_secret:
        raise SystemExit("That doesn't look like a Google OAuth client ID and secret.")
    sender = os.environ.get("PIP_EMAIL_FROM", "")
    hint = args.login_hint or (sender.rsplit("<", 1)[-1].rstrip(">").strip() if "@" in sender else "")
    token = consent(client_id, client_secret, hint)
    save_env(path, {"GMAIL_CLIENT_ID": client_id, "GMAIL_CLIENT_SECRET": client_secret, "GMAIL_REFRESH_TOKEN": token})
    print(f"Saved {', '.join(KEYS)} to {path} (mode 600).")

    if args.test_to:
        mailer = Mailer.from_env({**os.environ, "PIP_EMAIL_PROVIDER": "gmail", "GMAIL_CLIENT_ID": client_id,
                                  "GMAIL_CLIENT_SECRET": client_secret, "GMAIL_REFRESH_TOKEN": token})
        subject, body = compose("sign_in", "000000", 10)
        mailer.send(args.test_to, "Test: " + subject, "This is a test from tools/gmail_token.py.\n\n" + body)
        print(f"Sent a test email to {args.test_to}.")

    print(
        "\nOn Render (Environment), set PIP_EMAIL_PROVIDER=gmail and the three GMAIL_* values, then redeploy.\n"
        "Copy one to the clipboard without showing it, e.g.:\n"
        f"  grep '^GMAIL_REFRESH_TOKEN=' {path} | cut -d= -f2- | tr -d '\\n' | pbcopy"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
