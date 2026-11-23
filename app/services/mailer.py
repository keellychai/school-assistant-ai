"""Optional email delivery over SMTP (standard library only).

Email is used only when the server has SMTP settings in its environment:

    SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM (sender address), SMTP_TLS (default true)

Without them, email reminders are delivered in-app only and the admin page
says that email isn't configured. Credentials are never stored in the database.
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)


def email_configured():
    return bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("SMTP_FROM", "").strip())


def send_emails(recipients, subject, text):
    """Send one plain-text email per recipient. Returns (sent, failed). Never raises."""
    if not email_configured() or not recipients:
        return 0, 0
    host = os.getenv("SMTP_HOST").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    use_tls = os.getenv("SMTP_TLS", "true").strip().lower() in {"1", "true", "yes", "on"}
    user, password = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM").strip()

    sent = failed = 0
    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if user and password:
                smtp.login(user, password)
            for address in recipients:
                message = EmailMessage()
                message["From"], message["To"], message["Subject"] = sender, address, subject[:200]
                message.set_content(text)
                try:
                    smtp.send_message(message)
                    sent += 1
                except smtplib.SMTPException:
                    failed += 1
                    log.warning("Email to %s failed", address, exc_info=True)
    except (OSError, smtplib.SMTPException):
        log.exception("Could not connect to the SMTP server %s:%s", host, port)
        failed += len(recipients) - sent
    return sent, failed
