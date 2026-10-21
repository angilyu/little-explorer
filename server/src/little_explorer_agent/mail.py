"""Sends the grown-up's sign-in codes by email.

``PIP_EMAIL_PROVIDER`` picks how:

- ``smtp``: any SMTP server with STARTTLS (e.g. Gmail with an App Password): ``SMTP_HOST``,
  ``SMTP_PORT`` (587), ``SMTP_USER``, ``SMTP_PASSWORD``.
- ``gmail``: the Gmail API over HTTPS, sending as the Google account that granted access:
  ``GMAIL_CLIENT_ID``, ``GMAIL_CLIENT_SECRET``, ``GMAIL_REFRESH_TOKEN`` (``tools/gmail_token.py``
  gets the token). For hosts that block SMTP, like Render's free plan.
- ``resend``: the Resend HTTP API with ``RESEND_API_KEY``.
- ``console``: logs the code instead (local development and tests only).

Unset means email is off and sign-in is unavailable. ``PIP_EMAIL_FROM`` is the sender, e.g.
``Little Explorer <you@gmail.com>`` (for SMTP it defaults to ``SMTP_USER``).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import parseaddr

log = logging.getLogger("little_explorer.mail")

PROVIDERS = ("smtp", "gmail", "resend", "console")
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
SUBJECTS = {"sign_in": "Your Little Explorer code: {code}", "delete": "Code to delete your Little Explorer account: {code}"}


class MailError(RuntimeError):
    pass


def compose(purpose: str, code: str, minutes: int) -> tuple[str, str]:
    """The subject and plain-text body for a code."""
    subject = SUBJECTS.get(purpose, SUBJECTS["sign_in"]).format(code=code)
    if purpose == "delete":
        intro = "Someone asked to delete this Little Explorer account and all of its stars, coins and hearts."
        outro = "If that wasn't you, ignore this email and nothing will be deleted."
    else:
        intro = "Here is your code to sign in to Little Explorer."
        outro = "If you didn't ask for it, you can ignore this email."
    body = f"{intro}\n\n    {code}\n\nIt works for {minutes} minutes. {outro}\n\nLittle Explorer\n"
    return subject, body


@dataclass
class Mailer:
    provider: str = ""
    sender: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = field(default="", repr=False)
    resend_key: str = field(default="", repr=False)
    gmail_client_id: str = ""
    gmail_client_secret: str = field(default="", repr=False)
    gmail_refresh_token: str = field(default="", repr=False)
    timeout_s: float = 15
    # What ``console`` sent, newest last (tests read it).
    outbox: list[tuple[str, str, str]] = field(default_factory=list, repr=False)
    _access: tuple[str, float] = field(default=("", 0.0), repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Mailer:
        env = os.environ if env is None else env
        provider = env.get("PIP_EMAIL_PROVIDER", "").strip().lower()
        if provider and provider not in PROVIDERS:
            log.error("PIP_EMAIL_PROVIDER=%r is not one of %s; email is off", provider, ", ".join(PROVIDERS))
            provider = ""
        user = env.get("SMTP_USER", "").strip()
        return cls(
            provider=provider,
            sender=env.get("PIP_EMAIL_FROM", "").strip() or user,
            smtp_host=env.get("SMTP_HOST", "").strip(),
            smtp_port=int(env.get("SMTP_PORT", "").strip() or 587),
            smtp_user=user,
            # Gmail shows App Passwords in groups of four; the spaces aren't part of it.
            smtp_password=env.get("SMTP_PASSWORD", "").replace(" ", "").strip(),
            resend_key=env.get("RESEND_API_KEY", "").strip(),
            gmail_client_id=env.get("GMAIL_CLIENT_ID", "").strip(),
            gmail_client_secret=env.get("GMAIL_CLIENT_SECRET", "").strip(),
            gmail_refresh_token=env.get("GMAIL_REFRESH_TOKEN", "").strip(),
        )

    @property
    def ready(self) -> bool:
        if self.provider == "console":
            return True
        if self.provider == "smtp":
            return bool(self.smtp_host and self.smtp_user and self.smtp_password and self.sender)
        if self.provider == "gmail":
            return bool(self.gmail_client_id and self.gmail_client_secret and self.gmail_refresh_token and self.sender)
        if self.provider == "resend":
            return bool(self.resend_key and self.sender)
        return False

    def describe(self) -> str:
        if not self.provider:
            return "off (PIP_EMAIL_PROVIDER is not set)"
        missing = "" if self.ready else " (not ready: missing settings)"
        where = f" via {self.smtp_host}:{self.smtp_port}" if self.provider == "smtp" else ""
        return f"{self.provider}{where} from {parseaddr(self.sender)[1] or '?'}{missing}"

    def send(self, to: str, subject: str, body: str) -> None:
        """Blocking; raises :class:`MailError` if the message wasn't handed over."""
        if not self.ready:
            raise MailError("email is not configured")
        if self.provider == "console":
            log.warning("console email to %s: %s", to, subject)
            self.outbox.append((to, subject, body))
            return
        try:
            if self.provider == "smtp":
                self._smtp(to, subject, body)
            elif self.provider == "gmail":
                self._gmail(to, subject, body)
            else:
                self._resend(to, subject, body)
        except MailError:
            raise
        except Exception as exc:  # network, auth, refusal
            raise MailError(f"{self.provider} failed: {type(exc).__name__}: {exc}") from exc

    def _message(self, to: str, subject: str, body: str) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        return message

    def _smtp(self, to: str, subject: str, body: str) -> None:
        message = self._message(to, subject, body)
        context = ssl.create_default_context()
        if self.smtp_port == 465:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=self.timeout_s, context=context) as smtp:
                smtp.login(self.smtp_user, self.smtp_password)
                smtp.send_message(message)
            return
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.timeout_s) as smtp:
            smtp.starttls(context=context)
            smtp.login(self.smtp_user, self.smtp_password)
            smtp.send_message(message)

    def _gmail_access_token(self) -> str:
        """A short-lived access token from the refresh token, reused until a minute before it ends."""
        with self._lock:
            token, until = self._access
            if token and time.monotonic() < until:
                return token
            form = urllib.parse.urlencode(
                {
                    "client_id": self.gmail_client_id,
                    "client_secret": self.gmail_client_secret,
                    "refresh_token": self.gmail_refresh_token,
                    "grant_type": "refresh_token",
                }
            ).encode()
            request = urllib.request.Request(GOOGLE_TOKEN_URL, data=form, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    data = json.load(response)
            except urllib.error.HTTPError as exc:
                # e.g. invalid_grant: the refresh token was revoked or expired; run tools/gmail_token.py again.
                detail = json.loads(exc.read() or b"{}").get("error", "")
                raise MailError(f"gmail token refresh failed: {exc.code} {detail}") from None
            token = str(data.get("access_token") or "")
            if not token:
                raise MailError("gmail token refresh gave no access token")
            self._access = (token, time.monotonic() + max(60, int(data.get("expires_in") or 3600) - 60))
            return token

    def _gmail(self, to: str, subject: str, body: str) -> None:
        raw = base64.urlsafe_b64encode(self._message(to, subject, body).as_bytes()).decode().rstrip("=")
        headers = {"Authorization": "Bear" "er " + self._gmail_access_token(), "Content-Type": "application/json"}
        request = urllib.request.Request(GMAIL_SEND_URL, data=json.dumps({"raw": raw}).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                if response.status >= 300:
                    raise MailError(f"gmail answered {response.status}")
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                self._access = ("", 0.0)
            raise MailError(f"gmail answered {exc.code}") from None

    def _resend(self, to: str, subject: str, body: str) -> None:
        payload = json.dumps({"from": self.sender, "to": [to], "subject": subject, "text": body}).encode()
        request = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={"Authorization": f"Bearer {self.resend_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            if response.status >= 300:
                raise MailError(f"resend answered {response.status}")
