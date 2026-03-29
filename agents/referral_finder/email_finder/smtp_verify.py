from __future__ import annotations

import logging
import smtplib
import socket
import time

import dns.resolver

logger = logging.getLogger(__name__)

# Mail providers known to use catch-all (SMTP verify is unreliable for these)
_CATCH_ALL_PROVIDERS = {
    "outlook.com", "hotmail.com", "office365.com",
    "protection.outlook.com",  # Microsoft 365
    "mimecast.com",
    "proofpoint.com",
    "barracudanetworks.com",
}


def _get_mx_host(domain: str) -> str | None:
    """Return the highest-priority MX host for the domain."""
    try:
        records = dns.resolver.resolve(domain, "MX")
        sorted_records = sorted(records, key=lambda r: r.preference)
        return str(sorted_records[0].exchange).rstrip(".")
    except Exception as exc:
        logger.debug(f"MX lookup failed for {domain}: {exc}")
        return None


def _is_catch_all_provider(mx_host: str) -> bool:
    mx_lower = mx_host.lower()
    return any(provider in mx_lower for provider in _CATCH_ALL_PROVIDERS)


def _smtp_check(mx_host: str, email: str, from_domain: str) -> bool | None:
    """
    Perform SMTP RCPT TO check.
    Returns True if accepted, False if rejected, None if inconclusive.
    """
    try:
        with smtplib.SMTP(timeout=10) as smtp:
            smtp.connect(mx_host, 25)
            smtp.ehlo(from_domain)
            smtp.mail(f"verify@{from_domain}")
            code, _ = smtp.rcpt(email)
            smtp.quit()
            if code == 250:
                return True
            elif code in (550, 551, 553):
                return False
            else:
                # 421, 450, 452 = temporary — inconclusive
                return None
    except (ConnectionRefusedError, socket.timeout, socket.gaierror) as exc:
        logger.debug(f"SMTP connection failed to {mx_host}: {exc}")
        return None
    except smtplib.SMTPException as exc:
        logger.debug(f"SMTP error checking {email}: {exc}")
        return None


def verify_email(email: str, from_domain: str = "example.com") -> bool | None:
    """
    Verify an email address via SMTP RCPT TO (no email is sent).

    Returns:
      True   — address accepted by mail server
      False  — address rejected
      None   — inconclusive (catch-all server, connection refused, timeout)
    """
    domain = email.split("@")[-1]
    mx_host = _get_mx_host(domain)

    if not mx_host:
        return None

    if _is_catch_all_provider(mx_host):
        logger.debug(f"Skipping SMTP verify for {email}: catch-all provider ({mx_host})")
        return None

    time.sleep(1)  # be polite, avoid triggering rate limits
    result = _smtp_check(mx_host, email, from_domain)
    logger.debug(f"SMTP verify {email}: {'accepted' if result is True else 'rejected' if result is False else 'inconclusive'}")
    return result


def generate_permutations(first: str, last: str, domain: str) -> list[str]:
    """Generate common email address permutations for a person."""
    f = first.lower()
    l = last.lower()
    fi = f[0] if f else ""
    li = l[0] if l else ""

    patterns = [
        f"{f}.{l}",
        f"{f}{l}",
        f"{fi}{l}",
        f"{fi}.{l}",
        f"{f}.{li}",
        f"{f}",
        f"{l}.{f}",
    ]
    return [f"{p}@{domain}" for p in patterns if p]
