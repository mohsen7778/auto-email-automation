"""
email_service.py - Gmail SMTP email sending
Sends directly via Gmail SMTP using App Password.
Better deliverability than third party services.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import textwrap
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from config import SENDER_EMAIL, SENDER_NAME, GMAIL_APP_PASSWORD

log = logging.getLogger(__name__)


def _build_html(body_text: str) -> str:
    """Wrap plain-text body in minimal HTML."""
    html_body = body_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html_body = html_body.replace("\n", "<br>\n")
    return textwrap.dedent(f"""\
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8">
          <meta name="viewport" content="width=device-width,initial-scale=1">
          <style>
            body {{font-family:Arial,Helvetica,sans-serif;font-size:15px;
                   color:#222;background:#fff;margin:0;padding:20px}}
            .wrap {{max-width:600px;margin:0 auto}}
            p {{line-height:1.6;margin:0 0 12px}}
          </style>
        </head>
        <body>
          <div class="wrap">
            <p>{html_body}</p>
          </div>
        </body>
        </html>
    """)


def _send_smtp(to_email: str, to_name: str, subject: str, body_text: str) -> tuple[bool, str]:
    """Send email via Gmail SMTP (blocking — run in thread)."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{SENDER_NAME} <{SENDER_EMAIL}>"
        msg["To"]      = f"{to_name} <{to_email}>"

        # Plain text version
        msg.attach(MIMEText(body_text, "plain"))
        # HTML version
        msg.attach(MIMEText(_build_html(body_text), "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(SENDER_EMAIL, to_email, msg.as_string())

        log.info("Email sent via Gmail → %s (%s)", to_email, to_name)
        return True, "OK"

    except smtplib.SMTPAuthenticationError:
        msg = "Gmail auth failed — check SENDER_EMAIL and GMAIL_APP_PASSWORD"
        log.error(msg)
        return False, msg
    except Exception as exc:
        msg = f"SMTP error: {exc}"
        log.error("Email error → %s | %s", to_email, msg)
        return False, msg


async def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    body_text: str,
) -> tuple[bool, str]:
    """Async wrapper around blocking SMTP call."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _send_smtp, to_email, to_name, subject, body_text
    )


async def check_brevo_inbox(*args, **kwargs) -> list:
    """Stub — not used with Gmail SMTP."""
    return []


async def close_http_client() -> None:
    """Stub — no HTTP client to close."""
    pass

