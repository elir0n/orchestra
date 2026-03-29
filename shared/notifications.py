from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


class NotificationService:
    """Sends operator notifications via email."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        notify_email: str,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.notify_email = notify_email

    def send_email(self, subject: str, body: str) -> None:
        """Send a plain-text email to the operator."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.smtp_user
        msg["To"] = self.notify_email
        msg.attach(MIMEText(body, "plain", "utf-8"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.smtp_user, self.notify_email, msg.as_string())
            logger.info(f"Notification email sent to {self.notify_email}")
        except smtplib.SMTPAuthenticationError:
            logger.error(
                "SMTP authentication failed. For Gmail, set up an App Password at: "
                "https://myaccount.google.com/apppasswords"
            )
            raise
        except Exception as exc:
            logger.error(f"Failed to send notification email: {exc}")
            raise

    @classmethod
    def from_config(cls, config: "OrchestraConfig") -> "NotificationService":  # type: ignore[name-defined]
        return cls(
            smtp_host=config.smtp_host,
            smtp_port=config.smtp_port,
            smtp_user=config.smtp_user,
            smtp_password=config.smtp_password,
            notify_email=config.notify_email,
        )
