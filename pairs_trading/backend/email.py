from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
import json
import logging
from pathlib import Path
import smtplib
from typing import Any
from uuid import uuid4

from .config import BackendSettings


logger = logging.getLogger("pairs_trading.email")


@dataclass(frozen=True)
class EmailDelivery:
    mode: str
    recipient: str
    subject: str
    outbox_path: str | None = None


class EmailService:
    """Small transactional email adapter.

    Production uses SMTP. Development/test writes a local JSON outbox so auth
    flows can be exercised without a paid email provider.
    """

    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.outbox_dir = Path("artifacts/email_outbox")

    def send(self, *, to_email: str, subject: str, text: str, metadata: dict[str, Any] | None = None) -> EmailDelivery:
        if self.settings.smtp_host:
            message = EmailMessage()
            message["From"] = self.settings.email_from
            message["To"] = to_email
            message["Subject"] = subject
            message.set_content(text)
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port or 587, timeout=20) as smtp:
                if self.settings.smtp_starttls:
                    smtp.starttls()
                if self.settings.smtp_username:
                    smtp.login(self.settings.smtp_username, self.settings.smtp_password or "")
                smtp.send_message(message)
            logger.info("transactional_email_sent", extra={"recipient_domain": to_email.rsplit("@", 1)[-1], "subject": subject})
            return EmailDelivery(mode="smtp", recipient=to_email, subject=subject)

        if self.settings.is_production:
            raise RuntimeError("SMTP_HOST is required for production transactional emails.")

        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        outbox_path = self.outbox_dir / f"{uuid4().hex}.json"
        outbox_path.write_text(
            json.dumps(
                {
                    "to": to_email,
                    "subject": subject,
                    "text": text,
                    "metadata": metadata or {},
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        logger.info("transactional_email_written_to_outbox", extra={"path": str(outbox_path)})
        return EmailDelivery(mode="local_outbox", recipient=to_email, subject=subject, outbox_path=str(outbox_path))
