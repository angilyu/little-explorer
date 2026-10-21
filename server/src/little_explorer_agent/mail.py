"""Sends the grown-up's sign-in codes by email.

``PIP_EMAIL_PROVIDER`` picks how:

- ``smtp``: any SMTP server with STARTTLS (e.g. Gmail with an App Password): ``SMTP_HOST``,
  ``SMTP_PORT`` (587), ``SMTP_USER``, ``SMTP_PASSWORD``.
- ``resend``: the Resend HTTP API with ``RESEND_API_KEY``.
- ``console``: logs the code instead (local development and tests only).

Unset means email is off and sign-in is unavailable. ``PIP_EMAIL_FROM`` is the sender, e.g.
``Little Explorer <you@gmail.com>`` (for SMTP it defaults to ``SMTP_USER``).
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import parseaddr

log = logging.getLogger("little_explorer.mail")

PROVIDERS = ("smtp", "resend", "console")
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
    timeout_s: float = 15
    # What ``console`` sent, newest last (tests read it).
    outbox: list[tuple[str, str, str]] = field(default_factory=list, repr=False)

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
        )

    @property
    def ready(self) -> bool:
        if self.provider == "console":
            return True
        if self.provider == "smtp":
            return bool(self.smtp_host and self.smtp_user and self.smtp_password and self.sender)
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
            else:
                self._resend(to, subject, body)
        except MailError:
            raise
        except Exception as exc:  # network, auth, refusal
            raise MailError(f"{self.provider} failed: {type(exc).__name__}: {exc}") from exc

    def _smtp(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
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
